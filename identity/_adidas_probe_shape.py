#!/usr/bin/env python3
"""아디다스 search taxonomy API 응답 구조 + 페이지네이션 파라미터 확인."""
import json
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


def show(d, prefix="", depth=0):
    if depth > 3:
        return
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, (dict, list)):
                n = len(v)
                print(f"{prefix}{k}: {type(v).__name__}[{n}]")
                show(v, prefix + "  ", depth + 1)
            else:
                sv = str(v)[:60]
                print(f"{prefix}{k}: {sv}")
    elif isinstance(d, list) and d:
        print(f"{prefix}[0] sample:")
        show(d[0], prefix + "  ", depth + 1)


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pr_adidas", headless=False, locale="ko-KR",
            viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        clear(pg, BASE + "/")

        for q in ["men-shoes", "women-shoes"]:
            url = f"{BASE}/api/search/tf/taxonomy?query={q}"
            try:
                r = pg.request.get(url)
                print(f"\n===== {q} status={r.status} ct={r.headers.get('content-type','')} =====")
                txt = r.text()
                print("bytes:", len(txt))
                try:
                    j = json.loads(txt)
                except Exception:
                    print("NOT JSON, head:", txt[:300])
                    continue
                show(j, "", 0)
            except Exception as e:
                print(q, "ERR", repr(e)[:150])

        # pagination test: try start/offset params on men-shoes
        print("\n===== PAGINATION TEST men-shoes =====")
        for params in ["&start=48", "&offset=48", "&page=2", "&start=48&count=48"]:
            url = f"{BASE}/api/search/tf/taxonomy?query=men-shoes{params}"
            try:
                r = pg.request.get(url)
                j = json.loads(r.text())
                # find item count
                def count_items(obj):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ("items", "itemList", "products", "articles") and isinstance(v, list):
                                return len(v), (v[0].get("id") or v[0].get("productId") or v[0].get("articleId") if v and isinstance(v[0], dict) else None)
                            res = count_items(v)
                            if res:
                                return res
                    elif isinstance(obj, list):
                        for v in obj:
                            res = count_items(v)
                            if res:
                                return res
                    return None
                print(f"  {params}: status={r.status} firstItem={count_items(j)}")
            except Exception as e:
                print(f"  {params}: ERR {repr(e)[:80]}")

        ctx.close()


if __name__ == "__main__":
    main()
