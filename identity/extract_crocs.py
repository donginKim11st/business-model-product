#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server-side product sample extractor for Crocs Korea (Demandware/SFCC SFRA).

Site:   https://www.crocs.co.kr   (Sites-crocs_kr-Site, ko_KR)
List:   SFRA category grid is client-side Algolia -> use Algolia query endpoint as
        the list source (index: production_crocs_kr__products__ko_KR).
Detail: PDP /p/{slug}/{master}.html -> ld+json Product (price KRW, availability,
        rating). Color/sizes/gender come from Algolia (the DOM-swatch equivalent).
Gosi:   상품정보제공고시 (소재/제조국/제조년월) is React-rendered client-side
        (id=react-content); NOT present in server HTML, countryOfOrigin=null in
        embedded state -> origin/material/mfg_date left blank, gosi_status=none.
"""
import re, csv, json, sys, time, urllib.request, urllib.parse

BASE = "https://www.crocs.co.kr"
ALGOLIA_APP = "DKCVU97ADH"
ALGOLIA_KEY = "352ca58732a6d53434bfa329a2f8a8fa"
ALGOLIA_INDEX = "production_crocs_kr__products__ko_KR"
ALGOLIA_URL = f"https://{ALGOLIA_APP}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OUT = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_crocs.csv"
HEADER = ["source","brand","style_code","name","color","price","currency",
          "category","gender","sizes","origin","material","mfg_date","url"]
TARGET = 120
GENDER_MAP = {"unisex":"공용","women":"여성","men":"남성","kids":"키즈",
              "boys":"남아","girls":"여아","baby":"베이비"}
# Product-type whitelist for category fallback (names are clean & 100% present).
# Checked in order; first substring hit wins. Internal merch IDs
# (promo-excl-*, collection-*, Crocs Accessories) leak via __primary_category,
# so derive the real product type from the Korean product name instead.
TYPE_WL = ["보트 슈즈","클로그","샌들","샌달","슬라이드","슬리퍼","스니커","운동화",
           "부츠","뮬","웨지","플립플롭","플립플랍","하이츠","토트백","크로스바디",
           "백팩","배낭","지갑","파우치","폰 케이스","케이스","백스트랩","스트랩",
           "지비츠","참","양말","비니","모자","키링","우산"]
import re as _re
_HANGUL = _re.compile("[가-힣]")


def fetch_text(url, data=None, headers=None, timeout=30):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    h = {"User-Agent": UA,
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
         "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
    if headers:
        h.update(headers)
    body = data.encode("utf-8") if isinstance(data, str) else data
    req = urllib.request.Request(enc, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def algolia_page(page, hits=50):
    payload = json.dumps({"params": f"query=&hitsPerPage={hits}&page={page}"})
    txt = fetch_text(ALGOLIA_URL, data=payload, headers={
        "X-Algolia-Application-Id": ALGOLIA_APP,
        "X-Algolia-API-Key": ALGOLIA_KEY,
        "Content-Type": "application/json",
    })
    return json.loads(txt)


def base_row(h):
    """Build a complete schema row from one Algolia hit (one per master)."""
    name = (h.get("name") or "").strip()
    master = str(h.get("master") or "")
    color = ((h.get("color") or {}).get("name") or "").strip()
    gender = GENDER_MAP.get((h.get("gender") or "").lower(), h.get("gender") or "")
    # category: ai_product_type is the cleanest product-type label;
    # fall back to a name-derived product type, then to clean __primary_category.
    cat = ""
    apt = h.get("ai_product_type")
    if isinstance(apt, list) and apt:
        cat = apt[0]
    if not cat:
        for t in TYPE_WL:
            if t in name:
                cat = "스트랩" if t == "백스트랩" else t
                break
    if not cat:
        pc = (h.get("__primary_category") or {}).get("0", "")
        # accept __primary_category only if it's a real (Hangul) label, not an internal id
        cat = pc if _HANGUL.search(pc or "") else (h.get("primary_category_id") or "")
    # sizes: refinementSizes are KR mm sizes; fall back to sizeVariations ids
    sizes = h.get("refinementSizes") or [s.get("id") for s in (h.get("sizeVariations") or [])]
    sizes = "|".join(str(s) for s in sizes if s)
    # price: Algolia selling price (verified == ld+json offers price); PDP overwrites
    pricing = h.get("pricing") or {}
    price = pricing.get("price")
    if price is None:
        pk = h.get("price") or {}
        price = pk.get("KRW")
    url = h.get("url") or ""
    if url and not url.startswith("http"):
        url = BASE + url
    return {
        "source": "crocs", "brand": "크록스", "style_code": master, "name": name,
        "color": color, "price": str(price) if price is not None else "",
        "currency": "KRW", "category": cat, "gender": gender, "sizes": sizes,
        "origin": "", "material": "", "mfg_date": "", "url": url,
    }


def ld_price(html):
    """Return (selling_price, currency) from ld+json Product offers (min price)."""
    for blk in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>",
                          html, re.S | re.I):
        if '"Product"' not in blk:
            continue
        try:
            d = json.loads(blk)
        except Exception:
            continue
        if isinstance(d, list):
            d = next((x for x in d if x.get("@type") == "Product"), d[0])
        if d.get("@type") != "Product":
            continue
        offers = d.get("offers")
        prices, cur = [], "KRW"
        if isinstance(offers, dict):
            offers = [offers]
        for o in (offers or []):
            p = o.get("price")
            if p is not None:
                try:
                    prices.append(float(p))
                except Exception:
                    pass
            cur = o.get("priceCurrency") or cur
        if prices:
            mn = min(prices)
            return (str(int(mn)) if mn == int(mn) else str(mn)), cur
    return None, None


# ---------------- test mode ----------------
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "test":
    h = json.load(open(sys.argv[2]))["hits"][0]
    r = base_row(h)
    for k in HEADER:
        print(f"{k:11}= {r[k]!r}")
    sys.exit(0)


def main():
    # 1) Collect masters from Algolia (est_total = full index nbHits)
    rows, seen, est_total = [], set(), None
    page = 0
    while len(rows) < TARGET and page < 5:
        try:
            d = algolia_page(page)
        except Exception as e:
            print(f"  algolia page {page} ERR {e}", file=sys.stderr)
            break
        if est_total is None:
            est_total = d.get("nbHits")
            print("est_total (index nbHits) =", est_total)
        hits = d.get("hits", [])
        if not hits:
            break
        for h in hits:
            m = str(h.get("master") or "")
            if not m or m in seen:
                continue
            r = base_row(h)
            if not r["name"] or not r["url"]:
                continue
            seen.add(m)
            rows.append(r)
            if len(rows) >= TARGET:
                break
        page += 1
        time.sleep(0.3)
    print("collected", len(rows), "masters")

    # 2) Enrich price from PDP ld+json (Algolia price is fallback for any failure)
    ok_ld, fail = 0, 0
    for i, r in enumerate(rows, 1):
        try:
            html = fetch_text(r["url"])
            p, cur = ld_price(html)
            if p is not None:
                r["price"] = p
                if cur:
                    r["currency"] = cur
                ok_ld += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"  pdp {r['style_code']} ERR {e}", file=sys.stderr)
        if i % 20 == 0:
            print(f"  ...{i}/{len(rows)} (ld+json ok={ok_ld}, fallback={fail})")
        time.sleep(0.25)
    print(f"ld+json price ok={ok_ld}, Algolia-fallback={fail}")

    # 3) Write CSV (utf-8-sig)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("WROTE", len(rows), "rows ->", OUT)


if __name__ == "__main__":
    main()
