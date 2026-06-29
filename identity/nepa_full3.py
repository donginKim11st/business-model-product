#!/usr/bin/env python3
"""
네파(nplus.co.kr, Styleship ASP) 전수 추출 — phase2(병렬 상세) + 올바른 finalize.

phase1(pno 수집)은 nepa_full.py collect 가 outputs/_nepa_pnos.json 에 마침.
  - 전 카테고리(nav cno 160; 현 카탈로그 131 + OUTLET 29) × 전 페이지(빈/중복까지) 페이지네이션.
  - pno_list 는 "현 카탈로그 먼저, OUTLET 나중" 순서로 적재됨(order_cnos).

여기서:
  - _nepa_pnos.json 의 pno 전부에 대해 /product/view.asp?pno= 상세를 병렬로 받아
    _nepa_rows.jsonl 에 append(재개 가능).
  - finalize: pno->row 맵 구성 후 state["pnos"] '수집순서(현 카탈로그 먼저)'대로 순회,
    non-NEPA(jsonld brand) skip, style_code(컬러웨이 sku, 절단 금지) 기준 dedup,
    앞에서부터 CAP(5000)개만 채택 → 현 카탈로그가 항상 포함되도록 보장.
  - 출력 outputs/extract_brand_nepa.csv (utf-8-sig). 페이지(배치)마다 중간 finalize.
"""
import csv, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

import nepa_full as nf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
PNOS = nf.PNOS_STATE          # outputs/_nepa_pnos.json
ROWS = nf.ROWS_JSONL          # outputs/_nepa_rows.jsonl
CSV_PATH = nf.CSV_PATH        # outputs/extract_brand_nepa.csv
HEADER = nf.HEADER
CAP = nf.CAP                  # 5000
WORKERS = 8


def load_done():
    done = set()
    if os.path.exists(ROWS):
        for line in open(ROWS, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            done.add(rec["pno"])
    return done


def finalize(state):
    """현 카탈로그(수집순서) 먼저, style_code(full) dedup, 앞에서부터 CAP개."""
    by_pno = {}
    errors = 0
    for line in open(ROWS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        by_pno[rec["pno"]] = rec
    rows, seen_key = [], set()
    kept = 0
    skipped_brands = {}
    dead = 0
    capped = False
    for pno in state["pnos"]:               # collection order = current catalog first
        rec = by_pno.get(pno)
        if rec is None:
            continue
        if rec.get("err"):
            errors += 1
            continue
        if not rec.get("keep"):
            b = rec.get("brand_ld") or "?"
            skipped_brands[b] = skipped_brands.get(b, 0) + 1
            continue
        r = rec["row"]
        sk = (r.get("style_code") or "").strip()
        if not sk:                          # delisted stub: no JSON-LD => no colorway/name/price
            dead += 1
            continue
        kept += 1
        if sk in seen_key:                  # full colorway sku; never truncate
            continue
        seen_key.add(sk)
        rows.append(r)
        if len(rows) >= CAP:
            capped = True
            break
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return rows, kept, errors, skipped_brands, capped, dead


def details(state):
    pnos = state["pnos"]
    done = load_done()
    todo = [p for p in pnos if p not in done]
    print(f"PHASE2: {len(pnos)} pnos, {len(done)} done, {len(todo)} todo", flush=True)
    lock = threading.Lock()
    fh = open(ROWS, "a", encoding="utf-8")
    counters = {"n": len(done)}

    def work(pno):
        try:
            row, bld = nf.parse_detail(pno)
            return pno, row, bld, None
        except Exception as e:
            return pno, None, None, str(e)

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(work, p): p for p in todo}
            for fut in as_completed(futs):
                pno, row, bld, err = fut.result()
                with lock:
                    if err:
                        rec = {"pno": pno, "keep": False, "err": err}
                    else:
                        keep = not (bld and "NEPA" not in bld)
                        rec = {"pno": pno, "keep": keep, "brand_ld": bld}
                        if keep:
                            rec["row"] = row
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    counters["n"] += 1
                    if counters["n"] % 250 == 0:
                        fh.flush()
                        rws, kept, errs, _, capped, _d = finalize(state)
                        print(f"  [{counters['n']}/{len(pnos)}] mid-save: csv={len(rws)} "
                              f"kept={kept} errs={errs} capped={capped}", flush=True)
    finally:
        fh.close()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    state = json.load(open(PNOS, encoding="utf-8"))
    if mode in ("all", "details"):
        details(state)
    rows, kept, errors, skipped, capped, dead = finalize(state)
    print(f"\nFINAL: {len(rows)} rows -> {CSV_PATH} (capped={capped})", flush=True)
    print(f"  pool_pnos={len(state['pnos'])} kept(pre-dedup)={kept} dead_stubs={dead} errors={errors}", flush=True)
    print(f"  skipped non-NEPA: {json.dumps(skipped, ensure_ascii=False)}", flush=True)
    filled = {k: sum(1 for r in rows if str(r.get(k, '')).strip()) for k in HEADER}
    print(f"  filled per col: {json.dumps(filled, ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    main()
