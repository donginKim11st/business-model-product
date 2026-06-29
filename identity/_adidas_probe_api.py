#!/usr/bin/env python3
"""아디다스 PLP search API 구조/페이지네이션 + 내비 택소노미 탐지."""
import json
import re
from patchright.sync_api import sync_playwright

BASE = "https://www.adidas.co.kr"


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
    search_urls = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pr_adidas", headless=False, locale="ko-KR",
            viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()

        def on_resp(resp):
            u = resp.url
            if "/api/search" in u:
                search_urls.append((u, resp.status))
        pg.on("response", on_resp)

        clear(pg, BASE + "/")

        # navigation taxonomy
        try:
            r = pg.request.get(BASE + "/content-service/api/navigation?asset=header&audience=default&locale=ko_KR")
            nav = r.json()
            navstr = json.dumps(nav, ensure_ascii=False)
            print("[nav] bytes", len(navstr))
            links = sorted(set(re.findall(r'"(?:url|link|href)"\s*:\s*"(/[^"]+)"', navstr)))
            cat_links = [l for l in links if not l.endswith(".html")]
            print("[nav] category links:", len(cat_links))
            for l in cat_links:
                print("   NAV", l)
        except Exception as e:
            print("[nav] ERR", repr(e)[:150])

        # load /men and scroll heavily to capture pagination params
        pg.goto(BASE + "/men", timeout=60000, wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        for i in range(15):
            pg.mouse.wheel(0, 8000)
            pg.wait_for_timeout(1500)
        b = pg.content()
        codes = set(re.findall(r'/([A-Z]{2}\d{4})\.html', b))
        print("\n[/men] after 15 scrolls codes on page:", len(codes))

        ctx.close()

    print("\n=== /api/search URLs (full, unique) ===")
    seen = set()
    for u, st in search_urls:
        if u not in seen:
            seen.add(u)
            print(f"  {st}  {u}")


if __name__ == "__main__":
    main()
