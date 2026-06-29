#!/usr/bin/env python3
"""전 상품 × 모든 속성 하베스트 (B안). 각 상품 PDP를 받아 JSON-LD/고시/메타/옵션 전체를 뽑음.
출력: outputs/attrs_full_<slug>.jsonl (상품 1개 = 1줄: {style_code,name,url,n,attrs:{...}})
재개 가능(이미 처리한 style_code 스킵).

  python3 harvest_all.py <slug> [limit]
  python3 harvest_all.py all          # 전 브랜드 순차(느림 → 보통 브랜드별 병렬 실행)
"""
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
import ocr_gosi as og
from harvest_attrs import harvest, SLUGS, DISPLAY


def csv_for(slug):
    for p in (f"extract_brand_{slug}.csv", f"extract_{slug}.csv"):
        fp = os.path.join(OUT, p)
        if os.path.exists(fp):
            return fp
    return None


def run(slug, limit=None):
    f = csv_for(slug)
    if not f:
        print(f"{slug}: CSV 없음")
        return
    rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
    if limit:
        rows = rows[:limit]
    outp = os.path.join(OUT, f"attrs_full_{slug}.jsonl")
    done = set()
    if os.path.exists(outp):
        for line in open(outp, encoding="utf-8"):
            try:
                done.add(json.loads(line)["style_code"])
            except Exception:
                pass
    n_new = 0
    with open(outp, "a", encoding="utf-8") as fout:
        for i, r in enumerate(rows):
            sc = r.get("style_code", "")
            url = r.get("url", "")
            if not url or sc in done:
                continue
            try:
                html = og.http(url)
                attrs = harvest(html)
            except Exception as e:
                attrs = {"error": str(e)[:60]}
            n = sum(len(v) for v in attrs.values() if isinstance(v, dict))
            rec = {"slug": slug, "brand": DISPLAY.get(slug, slug), "style_code": sc,
                   "name": r.get("name", ""), "color": r.get("color", ""),
                   "price": r.get("price", ""), "url": url, "n": n, "attrs": attrs}
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()
            done.add(sc)
            n_new += 1
            if n_new % 25 == 0:
                print(f"  [{slug}] {n_new} 처리…", file=sys.stderr)
            time.sleep(0.15)
    print(f"{slug}: 신규 {n_new}개 → {os.path.basename(outp)} (누적 {len(done)})")


if __name__ == "__main__":
    args = sys.argv[1:] or ["all"]
    if args == ["all"]:
        for s in SLUGS:
            run(s)
    elif len(args) >= 2 and args[1].isdigit():  # 단일 slug + limit
        run(args[0], int(args[1]))
    else:  # 여러 slug 그룹 (병렬 실행용)
        for s in args:
            run(s)
