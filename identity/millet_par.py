#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""밀레 상세 병렬 페처 — millet_full2 의 parse_detail/split_code(패치판) 재사용.
   1 컬러웨이(product id) = 1행. resume(_millet_rows2.jsonl). 끝나면 phase3 CSV."""
import json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mf2", os.path.join(HERE, "millet_full2.py"))
mf2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mf2)

OUT = mf2.OUT
LIST_CKPT = mf2.LIST_CKPT
ROWS_CKPT = mf2.ROWS_CKPT
WORKERS = 16

lock = threading.Lock()


def fetch(pid, m):
    rec = {"id": pid, "name": m.get("name", ""),
           "style_code": m.get("style_code", ""), "price": m.get("price", ""),
           "category": m.get("category", ""), "color": "", "sizes": "",
           "origin": "", "material": "", "mfg_date": ""}
    try:
        d = mf2.parse_detail(pid)
        if d.get("style_code"):
            rec["style_code"] = d["style_code"]
        if d.get("name"):
            rec["name"] = d["name"]
        for k in ("color", "sizes", "origin", "material", "mfg_date"):
            rec[k] = d[k]
    except Exception as e:
        rec["_err"] = str(e)
    return rec


def main():
    data = json.load(open(LIST_CKPT, encoding="utf-8"))
    meta = data["meta"]
    done = {}
    if os.path.exists(ROWS_CKPT):
        for line in open(ROWS_CKPT, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    r = json.loads(line); done[r["id"]] = r
                except Exception:
                    pass
    todo = [pid for pid in meta if pid not in done]
    print(f"[par] total={len(meta)} done={len(done)} todo={len(todo)} workers={WORKERS}",
          flush=True)
    fout = open(ROWS_CKPT, "a", encoding="utf-8")
    n = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch, pid, meta[pid]): pid for pid in todo}
        for fut in as_completed(futs):
            rec = fut.result()
            with lock:
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fout.flush()
                done[rec["id"]] = rec
                n += 1
                if n % 100 == 0:
                    rate = n / (time.time() - t0)
                    print(f"[par] {n}/{len(todo)} ({rate:.1f}/s) total={len(done)}",
                          flush=True)
                    mf2.phase3(done)        # 중간저장
    fout.close()
    rows, _ = done, False
    nn, filled = mf2.phase3(rows)
    distinct_codes = len({r.get("style_code") for r in rows.values() if r.get("style_code")})
    empty_code = sum(1 for r in rows.values() if not r.get("style_code"))
    empty_color = sum(1 for r in rows.values() if not r.get("color"))
    errs = sum(1 for r in rows.values() if r.get("_err"))
    print("\n=== SUMMARY ===")
    print("rows (1 per colorway id):", nn)
    print("unique colorway ids:", len(meta))
    print("distinct style codes:", distinct_codes)
    print("empty style_code rows:", empty_code)
    print("empty color rows:", empty_color)
    print("fetch errors:", errs)
    print("csv:", mf2.CSV_PATH)
    for k in mf2.HEADER:
        print(f"  {k}: {filled[k]}/{nn}")
    meta_out = {"rows_after": nn, "unique_colorway_ids": len(meta),
                "distinct_style_codes": distinct_codes,
                "empty_style_code_rows": empty_code, "empty_color_rows": empty_color,
                "fetch_errors": errs, "n_categories": data.get("n_categories"),
                "filled": filled}
    json.dump(meta_out, open(mf2.META_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
