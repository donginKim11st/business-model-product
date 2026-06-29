#!/usr/bin/env python3
"""아디다스 디스커버리: Akamai 챌린지 통과 → 라이브 택소노미 + PLP JSON 엔드포인트 탐지."""
import json
import re
import time
import urllib.parse
from patchright.sync_api import sync_playwright

BASE = "https://www.adidas.co.kr"


def clear_challenge(pg, url, label):
    pg.goto(url, timeout=60000, wait_until="domcontentloaded")
    for i in range(40):
        pg.wait_for_timeout(1500)
        try:
            b = pg.content()
        except Exception:
            b = ""
        if "sec-if-cpt-container" not in b and len(b) > 8000:
            print(f"[{label}] cleared after {i*1.5:.0f}s, len={len(b)}")
            return b
    print(f"[{label}] NOT cleared, len={len(b)}")
    return b


def main():
    api_hits = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pr_adidas", headless=False, locale="ko-KR",
            viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()

        def on_resp(resp):
            u = resp.url
            ct = resp.headers.get("content-type", "")
            if "json" in ct and ("adidas.co.kr" in u):
                if any(k in u.lower() for k in ["plp", "content", "product", "search", "taxonomy", "api", "engine", "glass"]):
                    api_hits.append((u, resp.status))
        pg.on("response", on_resp)

        # 1) homepage → clear challenge + taxonomy
        home = clear_challenge(pg, BASE + "/", "home")
        hrefs = sorted(set(re.findall(r'href="(/[^"#?]+)"', home)))
        cats = [h for h in hrefs if not h.endswith(".html") and h.count("/") <= 3 and len(h) > 2]
        print(f"\n[home] {len(hrefs)} hrefs, {len(cats)} candidate cats")
        # also from __NEXT_DATA__
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', home, re.S)
        nd_cats = []
        if m:
            nd = m.group(1)
            nd_cats = sorted(set(re.findall(r'"url":"(/[^"]+?)"', nd)))
            print(f"[home] __NEXT_DATA__ urls: {len(nd_cats)}")
        for c in (cats + nd_cats)[:60]:
            print("   cat?", c)

        # 2) try a known taxonomy: 신발/의류/men/women — pick whichever returns products
        candidates = ["/신발", "/의류", "/men", "/women", "/kids", "/men-신발",
                      "/men/신발", "/women/신발", "/액세서리", "/신상품", "/세일"]
        good = None
        for c in candidates:
            url = BASE + urllib.parse.quote(c, safe="/")
            pg.goto(url, timeout=60000, wait_until="domcontentloaded")
            pg.wait_for_timeout(4000)
            for _ in range(4):
                pg.mouse.wheel(0, 5000)
                pg.wait_for_timeout(1800)
            b = pg.content()
            codes = set(re.findall(r'/([A-Z]{2}\d{4})\.html', b))
            print(f"[cat] {c}: len={len(b)} codes={len(codes)}")
            if len(codes) > 5 and not good:
                good = (c, url)
        print(f"\n[GOOD CAT] {good}")

        time.sleep(2)
        ctx.close()

    print("\n=== API HITS (json) ===")
    seen = set()
    for u, st in api_hits:
        key = u.split("?")[0]
        if key not in seen:
            seen.add(key)
            print(f"  {st}  {u[:300]}")


if __name__ == "__main__":
    main()
