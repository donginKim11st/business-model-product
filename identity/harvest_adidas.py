#!/usr/bin/env python3
"""아디다스 전 상품 속성 하베스트 — patchright headed(Akamai 우회)로 PDP 받아 harvest().
시스템 python3(/usr/bin/python3, patchright 보유)로 실행. 재개 가능.
출력: outputs/attrs_full_adidas.jsonl
"""
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
from harvest_attrs import harvest
from patchright.sync_api import sync_playwright


def main():
    f = os.path.join(OUT, "extract_brand_adidas.csv")
    rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
    outp = os.path.join(OUT, "attrs_full_adidas.jsonl")
    done = set()
    if os.path.exists(outp):
        for line in open(outp, encoding="utf-8"):
            try:
                done.add(json.loads(line)["style_code"])
            except Exception:
                pass
    todo = [r for r in rows if r.get("url") and r.get("style_code") not in done]
    print(f"아디다스 {len(todo)} 처리 (기존 {len(done)})")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pr_adidas", headless=False, locale="ko-KR",
            viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        with open(outp, "a", encoding="utf-8") as fout:
            for i, r in enumerate(todo):
                html = ""
                try:
                    pg.goto(r["url"], timeout=50000, wait_until="domcontentloaded")
                    for _ in range(18):
                        pg.wait_for_timeout(1000)
                        try:
                            html = pg.content()
                        except Exception:
                            html = ""
                        if '"@type":"Product"' in html.replace(" ", "") or "application/ld+json" in html:
                            break
                    attrs = harvest(html) if html else {"error": "no content"}
                except Exception as e:
                    attrs = {"error": str(e)[:60]}
                n = sum(len(v) for v in attrs.values() if isinstance(v, dict))
                rec = {"slug": "adidas", "brand": "아디다스", "style_code": r["style_code"],
                       "name": r.get("name", ""), "color": r.get("color", ""),
                       "price": r.get("price", ""), "url": r["url"], "n": n, "attrs": attrs}
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fout.flush()
                print(f"  {i+1}/{len(todo)} {r['style_code']}: 속성 {n}")
                pg.wait_for_timeout(1000)
        ctx.close()
    print(f"완료 → {outp}")


if __name__ == "__main__":
    main()
