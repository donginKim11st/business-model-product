#!/usr/bin/env python3
"""반스 상세 추출 병렬 마감 (collection 체크포인트 _vans_styles.tsv 재사용).

extract_vans_full.py 의 collection 결과(_vans_styles.tsv)와 부분 상세(_vans_rows.csv)를
재개해, 남은 styleCode 만 ThreadPool 로 받아 _vans_rows.csv 에 append,
끝나면 dedup 하여 outputs/extract_brand_vans.csv 로 마감.
"""
import csv
import os
import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from extract_vans_full import (parse_detail, HEADER, OUT_CSV, PARTIAL,
                               STYLES_TSV, CAP)

WORKERS = 6
lock = threading.Lock()


def load_collected():
    items = []
    with open(STYLES_TSV, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p and p[0]:
                items.append((p[0], p[1] if len(p) > 1 else "",
                              p[2] if len(p) > 2 else "공용"))
    # dedup preserve order
    seen, uniq = set(), []
    for s, c, g in items:
        if s not in seen:
            seen.add(s)
            uniq.append((s, c, g))
    return uniq[:CAP]


def load_done():
    done = set()
    if os.path.exists(PARTIAL):
        with open(PARTIAL, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                sc = (row.get("style_code") or "").strip().upper()
                if sc:
                    done.add(sc)
    return done


def main():
    collected = load_collected()
    done = load_done()
    todo = [t for t in collected if t[0].upper() not in done]
    print(f"수집 {len(collected)} | 완료 {len(done)} | 대상 {len(todo)}", file=sys.stderr)

    new_partial = not os.path.exists(PARTIAL)
    pf = open(PARTIAL, "a", encoding="utf-8-sig", newline="")
    w = csv.DictWriter(pf, fieldnames=HEADER)
    if new_partial:
        w.writeheader()
        pf.flush()

    fail = 0
    cnt = 0

    def job(t):
        style, label, gender = t
        time.sleep(random.uniform(0, 0.25))
        return parse_detail(style, label, gender)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(job, t): t for t in todo}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                rec = fut.result()
                with lock:
                    w.writerow(rec)
                    pf.flush()
                    cnt += 1
                    if cnt % 50 == 0:
                        print(f"[detail] {cnt}/{len(todo)} ...", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                fail += 1
                print(f"[detail] {t[0]} 실패: {e}", file=sys.stderr)
    pf.close()

    # finalize dedup -> OUT_CSV (collection 순서)
    rows_by_sc = {}
    with open(PARTIAL, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            sc = (row.get("style_code") or "").strip().upper()
            if sc and sc not in rows_by_sc:
                rows_by_sc[sc] = row
    ordered = [s.upper() for s, _, _ in collected]
    final = [rows_by_sc[sc] for sc in ordered if sc in rows_by_sc]
    extra = [r for sc, r in rows_by_sc.items() if sc not in set(ordered)]
    final += extra

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        wf = csv.DictWriter(f, fieldnames=HEADER)
        wf.writeheader()
        for r in final:
            wf.writerow({k: r.get(k, "") for k in HEADER})

    filled = {k: sum(1 for r in final if str(r.get(k, "")).strip()) for k in HEADER}
    print(f"\n=== 완료 === 최종 행수 {len(final)} (수집 {len(collected)}, 실패 {fail})")
    print("채워진 컬럼:", {k: v for k, v in filled.items() if v})
    print("경로:", OUT_CSV)


if __name__ == "__main__":
    main()
