#!/usr/bin/env python3
"""
네파 공식몰(nplus.co.kr, Styleship ASP) 전수(全數) 재추출 — 현 카탈로그 전 카테고리 × 전 페이지.

이전 실패 원인:
  - CSV를 쓴 건 extract_nepa.py(표본 스크립트)로, main()이 cats=[("100",3),("200",3)]
    (MEN/WOMEN 각 3페이지)만 돌고 pnos=pnos[:120] 하드캡 → ~120에서 절단. 페이지네이션 없음.
  - nepa_full.py 는 phase1(pno수집)만 돌고(_nepa_pnos.json=5082) phase2(상세)는 미실행
    (_nepa_rows.jsonl 부재) → CSV가 갱신된 적 없음.

이번 방법:
  - 전 카테고리(nav cno) 중 OUTLET(410~441, 과거시즌·단독 1만+ 상품, 5000캡 초과)만 제외하고
    각 cno page=1.. 를 "신규(비배너) pno 0" 나올 때까지 끝까지 페이지네이션 → 현 카탈로그 union.
  - 배너 pno(추천 4개)는 page=999 overflow에서 동적 감지 후 제외.
  - 상세 /product/view.asp?pno= JSON-LD(sku=컬러웨이코드/brand/price) + DOM(color/size/고시).
  - brand JSON-LD에 NEPA 없으면(PYRENEX 등 유통브랜드) skip. blank brand는 유지.
  - 병렬 상세수집(ThreadPool) + pno마다 jsonl append(재개가능).
  - style_code(컬러웨이 sku)로만 dedup — 절단/뭉침 금지. sku 없으면 url로 키.
  - kept 5000 상한.
출력: outputs/extract_brand_nepa.csv (utf-8-sig)
"""
import csv, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

import nepa_full as nf  # reuse proven http_get / page_pnos / detect_banners / discover_cnos / parse_detail

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
CSV_PATH = os.path.join(OUT, "extract_brand_nepa.csv")
PNOS2 = os.path.join(OUT, "_nepa_pnos_v2.json")
ROWS2 = os.path.join(OUT, "_nepa_rows_v2.jsonl")
HEADER = nf.HEADER
CAP = 5000
WORKERS = 8


def is_outlet(c):
    try:
        return 410 <= int(c) <= 441
    except Exception:
        return False


# ----------------------------- phase 1: collect non-outlet pnos -----------------------------
def collect():
    if os.path.exists(PNOS2):
        state = json.load(open(PNOS2, encoding="utf-8"))
    else:
        state = {"banners": [], "cnos": [], "cats_done": {}, "errors": {}, "pnos": []}

    if not state["banners"]:
        state["banners"] = nf.detect_banners()
        print(f"banners: {state['banners']}", flush=True)
    banners = set(state["banners"])

    if not state["cnos"]:
        allc = nf.discover_cnos()
        state["cnos"] = [c for c in allc if not is_outlet(c)]
    cnos = state["cnos"]
    # MEN/WOMEN/KIDS top-level first
    cnos = sorted(cnos, key=lambda c: (int(c) not in (100, 200, 300), int(c)))
    print(f"current-catalog cnos (outlet excluded): {len(cnos)}", flush=True)

    pno_set = set(state["pnos"])
    pno_list = list(state["pnos"])

    for cno in cnos:
        if cno in state["cats_done"]:
            continue
        seen = set()
        data_tot = None
        p = 1
        err = None
        while True:
            try:
                pnos, tot = nf.page_pnos(cno, p)  # http_get retries 4x + curl_cffi fallback inside
            except Exception as e:
                err = f"p{p}: {e}"
                print(f"  ! cno={cno} p={p} ERROR {e}", file=sys.stderr, flush=True)
                break
            if data_tot is None and tot is not None:
                data_tot = tot
            real = [x for x in pnos if x not in banners]
            new = [x for x in real if x not in seen]
            if not new:
                break  # HTTP 200 + 0 new non-banner pno = true end
            seen.update(new)
            for x in new:
                if x not in pno_set:
                    pno_set.add(x)
                    pno_list.append(x)
            p += 1
            time.sleep(0.12)
        rec = {"got": len(seen), "data_tot": data_tot, "pages": p - 1}
        if err:
            state["errors"][cno] = err
        else:
            state["cats_done"][cno] = rec
        flag = ""
        if data_tot is not None and not err and abs(len(seen) - data_tot) > 6:
            flag = f"  <-- MISMATCH got {len(seen)} vs tot {data_tot}"
        print(f"  cno={cno}: got {len(seen)} pages {p-1} tot={data_tot}{flag}", flush=True)
        state["pnos"] = pno_list
        json.dump(state, open(PNOS2, "w", encoding="utf-8"), ensure_ascii=False)

    print(f"\nPHASE1 done: {len(pno_list)} unique non-outlet pnos; "
          f"errors={list(state['errors'])}", flush=True)
    return state


# ----------------------------- phase 2: parallel detail fetch -----------------------------
def load_done():
    done, kept = set(), 0
    if os.path.exists(ROWS2):
        for line in open(ROWS2, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            done.add(rec["pno"])
            if rec.get("keep"):
                kept += 1
    return done, kept


def details(state):
    pnos = state["pnos"]
    done, kept = load_done()
    todo = [p for p in pnos if p not in done]
    print(f"PHASE2: {len(pnos)} pnos, {len(done)} done, {kept} kept, {len(todo)} todo", flush=True)

    lock = threading.Lock()
    counters = {"kept": kept, "n": len(done), "stop": kept >= CAP}

    def work(pno):
        try:
            row, bld = nf.parse_detail(pno)
            return pno, row, bld, None
        except Exception as e:
            return pno, None, None, str(e)

    f = open(ROWS2, "a", encoding="utf-8")
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
                            counters["kept"] += 1
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    counters["n"] += 1
                    if counters["n"] % 100 == 0:
                        print(f"  [{counters['n']}] kept={counters['kept']} "
                              f"last pno={pno}", flush=True)
                    if counters["kept"] >= CAP and not counters["stop"]:
                        counters["stop"] = True
                        print(f"  CAP {CAP} kept reached", flush=True)
    finally:
        f.close()
    return counters


# ----------------------------- finalize CSV -----------------------------
def finalize():
    rows, seen_key = [], set()
    kept = errors = 0
    skipped = {}
    capped_note = False
    for line in open(ROWS2, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("err"):
            errors += 1
            continue
        if not rec.get("keep"):
            b = rec.get("brand_ld") or "?"
            skipped[b] = skipped.get(b, 0) + 1
            continue
        kept += 1
        r = rec["row"]
        key = (r.get("style_code") or "").strip() or r["url"]  # colorway-level; never crush blanks
        if key in seen_key:
            continue
        seen_key.add(key)
        rows.append(r)
    if len(rows) > CAP:
        rows = rows[:CAP]
        capped_note = True
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nFINAL: {len(rows)} unique rows -> {CSV_PATH} (capped={capped_note})", flush=True)
    print(f"  kept(pre-dedup)={kept} errors={errors}", flush=True)
    print(f"  skipped non-NEPA: {json.dumps(skipped, ensure_ascii=False)}", flush=True)
    filled = {k: sum(1 for r in rows if str(r.get(k, '')).strip()) for k in HEADER}
    print(f"  filled per col: {json.dumps(filled, ensure_ascii=False)}", flush=True)
    return rows


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    state = None
    if mode in ("all", "collect"):
        state = collect()
    if mode in ("all", "details"):
        if state is None:
            state = json.load(open(PNOS2, encoding="utf-8"))
        details(state)
    if mode in ("all", "details", "finalize"):
        finalize()


if __name__ == "__main__":
    main()
