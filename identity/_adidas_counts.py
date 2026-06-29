#!/usr/bin/env python3
"""아디다스 택소노미별 count 비교 → 전수 커버 전략 설계."""
import json
from patchright.sync_api import sync_playwright

BASE = "https://www.adidas.co.kr"
TAX = ["men", "women", "kids",
       "men-shoes", "men-clothing", "men-accessories",
       "women-shoes", "women-clothing", "women-accessories",
       "kids-shoes", "kids-clothing", "kids-accessories",
       "kids-boys", "kids-girls", "kids-infants",
       "originals", "sportswear", "sale", "new_arrivals",
       "performance", "shoes", "clothing", "accessories"]


def clear(pg, url):
    pg.goto(url, timeout=60000, wait_until="domcontentloaded")
    for _ in range(40):
        pg.wait_for_timeout(1500)
        try:
            b = pg.content()
        except Exception:
            b = ""
        if "sec-if-cpt-container" not in b and len(b) > 8000:
            return b
    return b


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pr_adidas", headless=False, locale="ko-KR",
            viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        clear(pg, BASE + "/")
        total_check = {}
        for q in TAX:
            url = f"{BASE}/api/search/tf/taxonomy?query={q}"
            try:
                r = pg.request.get(url)
                if r.status != 200:
                    print(f"  {q:24s} status={r.status}")
                    continue
                j = json.loads(r.text())
                il = j.get("itemList", {})
                cnt = il.get("count")
                bc = " > ".join(b.get("text", "") for b in j.get("breadcrumbs", []))
                print(f"  {q:24s} count={cnt}  title={j.get('title','')}  bc=[{bc}]")
                total_check[q] = cnt
            except Exception as e:
                print(f"  {q:24s} ERR {repr(e)[:80]}")
        print("\nmen-sub sum:", sum(total_check.get(k,0) for k in ["men-shoes","men-clothing","men-accessories"]), "vs men:", total_check.get("men"))
        print("women-sub sum:", sum(total_check.get(k,0) for k in ["women-shoes","women-clothing","women-accessories"]), "vs women:", total_check.get("women"))
        print("kids-sub sum:", sum(total_check.get(k,0) for k in ["kids-shoes","kids-clothing","kids-accessories"]), "vs kids:", total_check.get("kids"))
        ctx.close()


if __name__ == "__main__":
    main()
