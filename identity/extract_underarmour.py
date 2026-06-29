#!/usr/bin/env python3
"""언더아머(SFCC/Demandware) 서버측 추출 — curl_cffi(크롬 TLS위장)로 418 우회.
listing(검색)에서 PDP 링크 수집 → 각 PDP의 ld+json(Product/ProductGroup) + 고시 DOM.
출력: outputs/extract_brand_underarmour.csv (official_extract 공통 14컬럼)"""
import csv
import html as H
import json
import os
import re
import time
import urllib.parse
from curl_cffi import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
BASE = "https://www.underarmour.co.kr"
COLS = ["source", "brand", "style_code", "name", "color", "price", "currency",
        "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]


def get(u):
    u = urllib.parse.quote(u, safe=":/?=&%#+,")
    return requests.get(u, impersonate="chrome", timeout=30,
                        headers={"Accept-Language": "ko-KR,ko;q=0.9"}).text


def flat(x):
    return x if isinstance(x, list) else [x]


def gosi(body):
    out = {}
    for lab, val in re.findall(r"<(?:th|dt)[^>]*>\s*([^<]{2,12})\s*</(?:th|dt)>\s*"
                               r"<(?:td|dd)[^>]*>(.*?)</(?:td|dd)>", body, re.S):
        v = re.sub(r"<[^>]+>", " ", H.unescape(val))
        v = re.sub(r"\s+", " ", v).strip()
        if v:
            out[lab.strip()] = v
    return out


def parse_pdp(url):
    body = get(url)
    prod = grp = None
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', body, re.S):
        try:
            j = json.loads(m.group(1).strip())
        except Exception:
            continue
        for n in flat(j):
            if not isinstance(n, dict):
                continue
            if n.get("@type") == "Product":
                prod = n
            elif n.get("@type") == "ProductGroup":
                grp = n
    src = prod or grp
    if not src:
        return None
    offers = flat(src.get("offers") or (grp.get("offers") if grp else []) or [])
    price = ""
    for o in offers:
        if isinstance(o, dict) and (o.get("price") or o.get("lowPrice")):
            price = o.get("price") or o.get("lowPrice")
            break
    sizes = []
    if grp:
        for v in flat(grp.get("hasVariant") or []):
            if isinstance(v, dict) and v.get("size"):
                sizes.append(str(v["size"]))
    if not sizes:
        sizes = re.findall(r'data-attr="size"[^>]*data-attr-value="([^"]+)"', body)[:30]
    g = gosi(body)
    brand = src.get("brand")
    brand = brand.get("name", "") if isinstance(brand, dict) else (brand or "")
    return {
        "name": src.get("name", ""), "sku": src.get("sku") or src.get("mpn") or "",
        "brand": brand, "color": src.get("color", "") or g.get("색상", ""),
        "price": str(price).replace(".0", ""),
        "currency": (offers[0].get("priceCurrency") if offers and isinstance(offers[0], dict) else "") or "KRW",
        "sizes": sorted(set(sizes)),
        "origin": g.get("제조국", "") or g.get("원산지", ""),
        "material": g.get("소재", "") or g.get("제품소재", ""),
        "mfg_date": g.get("제조연월", "") or g.get("제조년월", ""),
        "url": url,
    }


def main():
    queries = ["shoes", "신발", "men", "women", "apparel", "의류", "accessories",
               "운동화", "티셔츠", "후디", "팬츠", "백팩"]
    links, seen = [], set()
    for q in queries:
        try:
            body = get(f"{BASE}/ko-kr/search?q={urllib.parse.quote(q)}")
        except Exception as e:
            print(f"  검색 '{q}' 실패: {str(e)[:50]}")
            continue
        for u in re.findall(r'/ko-kr/p/[^"\' >]+\.html', body):
            full = BASE + u
            if full not in seen:
                seen.add(full)
                links.append(full)
        print(f"  '{q}' → 누적 {len(links)}")
        time.sleep(0.3)
    links = links[:120]
    rows = []
    for i, u in enumerate(links):
        try:
            d = parse_pdp(u)
            if not d or not d["name"]:
                continue
            gender = "MEN" if "/men" in u or "남성" in d["name"] else \
                     ("WOMEN" if "/women" in u or "여성" in d["name"] else "")
            rows.append({"source": "underarmour", "brand": "언더아머",
                         "style_code": d["sku"], "name": d["name"], "color": d["color"],
                         "price": d["price"], "currency": d["currency"], "category": "",
                         "gender": gender, "sizes": "|".join(d["sizes"]),
                         "origin": d["origin"], "material": d["material"],
                         "mfg_date": d["mfg_date"], "url": d["url"]})
        except Exception as e:
            print(f"  PDP 실패 …{u[-28:]}: {str(e)[:40]}")
        if (i + 1) % 30 == 0:
            print(f"  …{i+1}/{len(links)}")
        time.sleep(0.25)
    path = os.path.join(OUT, "extract_brand_underarmour.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    filled = {c: sum(1 for r in rows if r[c]) for c in ("color", "price", "sizes", "origin", "material", "mfg_date")}
    print(f"언더아머 {len(rows)}행 → {path}")
    print("  채움:", filled)


if __name__ == "__main__":
    main()
