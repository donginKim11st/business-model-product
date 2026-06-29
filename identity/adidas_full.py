#!/usr/bin/env python3
"""아디다스 코리아 전수 추출 — patchright headed로 Akamai 챌린지 통과 후
PLP JSON API(/api/search/tf/taxonomy?query=<tax>&start=<n>) 를 viewSize(48)씩
끝(count)까지 페이지네이션. 카테고리 men/women/kids = 전 상품 커버.
style_code(productId) 기준 dedup. 페이지마다 CSV+커서 저장(재개 가능). cap 5000.
출력: outputs/extract_brand_adidas.csv (공통 14컬럼, utf-8-sig)."""
import csv
import json
import os
import time
import traceback
from patchright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
PATH = os.path.join(OUT, "extract_brand_adidas.csv")
CURSOR = os.path.join(OUT, "_adidas_cursor.json")
BASE = "https://www.adidas.co.kr"
COLS = ["source", "brand", "style_code", "name", "color", "price", "currency",
        "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]

TAXONOMIES = [("kids", "KIDS"), ("women", "WOMEN"), ("men", "MEN")]  # 작은 것부터(캡 시 균형)
VIEW = 48
CAP = 5000
CAT_KO = {"shoes": "신발", "clothing": "의류", "accessories": "액세서리",
          "gear": "장비", "equipment": "장비"}


def clear_challenge(pg):
    pg.goto(BASE + "/", timeout=60000, wait_until="domcontentloaded")
    for _ in range(40):
        pg.wait_for_timeout(1500)
        try:
            b = pg.content()
        except Exception:
            b = ""
        if "sec-if-cpt-container" not in b and len(b) > 8000:
            return True
    return False


def fetch_json(pg, url, tries=5):
    """Akamai 쿠키 라이딩 GET. 챌린지/네트워크 오류 시 재챌린지+백오프 재시도."""
    last = ""
    for t in range(tries):
        try:
            r = pg.request.get(url, timeout=45000)
            txt = r.text()
            if r.status == 200 and txt.lstrip().startswith("{"):
                return json.loads(txt)
            last = f"status={r.status} head={txt[:80]!r}"
            # 챌린지/거부 → 쿠키 갱신
            if r.status in (403, 429, 503) or "sec-if-cpt" in txt:
                clear_challenge(pg)
        except Exception as e:
            last = repr(e)[:120]
        pg.wait_for_timeout(1500 + t * 1500)
    print(f"    [fail] {url[-60:]} :: {last}")
    return None


def parse_item(it, gender):
    code = it.get("productId") or ""
    name = it.get("displayName") or ""
    alt = it.get("altText") or ""
    color = ""
    if name and alt.endswith(name):
        color = alt[: len(alt) - len(name)].strip(" -")
    elif alt and name and name in alt:
        color = alt.replace(name, "").strip(" -")
    price = it.get("price") or it.get("salePrice") or ""
    sizes = []
    for s in (it.get("availableSizes") or []):
        if isinstance(s, dict):
            v = s.get("value") or s.get("size") or s.get("text") or ""
            if v:
                sizes.append(str(v))
        elif s:
            sizes.append(str(s))
    cat = CAT_KO.get((it.get("category") or "").lower(), it.get("category") or "")
    link = it.get("link") or ""
    url = (BASE + link) if link.startswith("/") else link
    return {"source": "adidas", "brand": "아디다스", "style_code": code, "name": name,
            "color": color, "price": str(price), "currency": "KRW", "category": cat,
            "gender": gender, "sizes": "|".join(sizes), "origin": "", "material": "",
            "mfg_date": "", "url": url}


def save(rows):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows.values())
    os.replace(tmp, PATH)


def main():
    rows = {}
    if os.path.exists(PATH):  # 재개: 기존 행 로드(style_code 키)
        for r in csv.DictReader(open(PATH, encoding="utf-8-sig")):
            if r.get("style_code"):
                rows[r["style_code"]] = r
    cursor = {}
    if os.path.exists(CURSOR):
        cursor = json.load(open(CURSOR))
    pages = cursor.get("_pages", {})
    counts = cursor.get("_counts", {})
    print(f"[adidas] 재개: 기존 {len(rows)}행, 커서 {[(k,v) for k,v in cursor.items() if not k.startswith('_')]}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pr_adidas", headless=False, locale="ko-KR",
            viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        if not clear_challenge(pg):
            print("[adidas] 챌린지 미통과 — 중단")
            ctx.close()
            return rows, pages, counts, False

        capped = False
        for tax, gender in TAXONOMIES:
            start = cursor.get(tax, 0)
            # count 확보(첫 호출)
            total = counts.get(tax)
            if total is None:
                j = fetch_json(pg, f"{BASE}/api/search/tf/taxonomy?query={tax}&start=0")
                if not j:
                    print(f"  [{tax}] count 실패 — 스킵")
                    continue
                total = j.get("itemList", {}).get("count", 0)
                counts[tax] = total
            print(f"  [{tax}] count={total}, start={start}")
            while start < total:
                if len(rows) >= CAP:
                    capped = True
                    break
                url = f"{BASE}/api/search/tf/taxonomy?query={tax}&start={start}"
                j = fetch_json(pg, url)
                if not j:
                    start += VIEW
                    continue
                items = j.get("itemList", {}).get("items", []) or []
                added = 0
                for it in items:
                    row = parse_item(it, gender)
                    if row["style_code"] and row["style_code"] not in rows:
                        rows[row["style_code"]] = row
                        added += 1
                start += VIEW
                pages[tax] = pages.get(tax, 0) + 1
                cursor[tax] = start
                cursor["_pages"] = pages
                cursor["_counts"] = counts
                save(rows)
                json.dump(cursor, open(CURSOR, "w"))
                print(f"    {tax} start={start-VIEW} items={len(items)} +{added} 누적유니크={len(rows)}")
                pg.wait_for_timeout(800)  # 페이싱(EADDRNOTAVAIL 회피)
            if capped:
                print(f"  [cap] {CAP} 도달 — 중단")
                break
        ctx.close()
    save(rows)
    return rows, pages, counts, capped


if __name__ == "__main__":
    try:
        rows, pages, counts, capped = main()
        tp = sum(pages.values())
        print(f"\n[DONE] 유니크 {len(rows)}행, 페이지 {tp} {pages}, counts {counts}, capped={capped}")
        print(f"-> {PATH}")
    except Exception:
        traceback.print_exc()
