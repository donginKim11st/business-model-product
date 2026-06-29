#!/usr/bin/env python3
"""미즈노 공식몰(kor.mizuno.com, cafe24) 서버측 상품 표본 추출.

리스트: /product/list.html?cate_no=158 (신발) 페이징
상세  : /product/detail.html?product_no={id}  → JSON-LD Product(offers[])
        + 본문 '자체상품코드'(=품번/style_code)
출력  : outputs/extract_brand_mizuno.csv (공통 스키마)
"""
import csv
import json
import os
import re
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADER = ["source", "brand", "style_code", "name", "color", "price",
          "currency", "category", "gender", "sizes", "origin", "material",
          "mfg_date", "url"]
CATE = 158            # 신발(shoes)
CATEGORY_NAME = "신발"
MAX_PAGES = 5
CAP = 120


def get(url, timeout=25, retries=2):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(1.5)
    raise last


def list_product_nos():
    nos, est = [], None
    seen = set()
    for p in range(1, MAX_PAGES + 1):
        html = get(f"https://kor.mizuno.com/product/list.html?cate_no={CATE}&page={p}")
        if est is None:
            m = re.search(r"총\s*<[^>]*>?\s*(\d[\d,]*)", html)
            if m:
                est = m.group(1).replace(",", "")
        page_nos = []
        for n in re.findall(r"product_no=(\d+)", html):
            if n not in seen:
                seen.add(n)
                page_nos.append(n)
        if not page_nos:
            break
        nos.extend(page_nos)
        if len(nos) >= CAP:
            break
        time.sleep(0.3)
    return nos[:CAP], est


def parse_detail(pno):
    url = f"https://kor.mizuno.com/product/detail.html?product_no={pno}"
    html = get(url)
    m = re.search(r"application/ld\+json[\"'][^>]*>(.*?)</script>", html, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(1).strip())
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("@type") not in ("Product", "ProductGroup"):
        return None
    name = (d.get("name") or "").strip()
    offers = d.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    colors, sizes, prices, currency = [], [], [], "KRW"
    for o in offers:
        rest = (o.get("name") or "").replace(name, "").strip()
        if "-" in rest:
            c, sz = rest.rsplit("-", 1)
            c, sz = c.strip(), sz.strip()
        else:
            c, sz = rest, ""
        if c and c not in colors:
            colors.append(c)
        if sz and sz not in sizes:
            sizes.append(sz)
        if o.get("price"):
            prices.append(o["price"])
        if o.get("priceCurrency"):
            currency = o["priceCurrency"]
    # style_code: 본문 '자체상품코드' (완전값) > '모델'
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    sc = re.search(r"자체상품코드\s+([A-Za-z0-9][A-Za-z0-9\-]{4,})", txt)
    if not sc:
        sc = re.search(r"모델\s+([A-Za-z0-9][A-Za-z0-9\-]{4,})", txt)
    style_code = sc.group(1).strip() if sc else ""
    # 상품정보제공고시(있으면)
    def notice(*labels):
        for lb in labels:
            mm = re.search(lb + r"\s*[:：]?\s*([^|<\n]{1,40}?)\s*(?:제조|원산|소재|치수|색상|크기|품질|취급|$)", txt)
            if mm and mm.group(1).strip():
                return mm.group(1).strip()
        return ""
    origin = notice("제조국", "원산지")
    material = notice("제품소재", "소재", "주소재")
    mfg = notice("제조연월", "제조년월", "제조일")
    # gender(있으면): 카테고리 경로에서 남성화/여성화/공용
    g = ""
    if re.search(r"여성화|우먼스|여성용|WOMEN", html, re.I):
        g = "여성"
    elif re.search(r"남성화|맨스|남성용|MEN", html, re.I):
        g = "남성"
    price = str(min(prices)) if prices else ""
    return {
        "source": "mizuno",
        "brand": "미즈노",
        "style_code": style_code,
        "name": name,
        "color": "|".join(colors),
        "price": price,
        "currency": currency,
        "category": CATEGORY_NAME,
        "gender": g,
        "sizes": "|".join(sizes),
        "origin": origin,
        "material": material,
        "mfg_date": mfg,
        "url": url,
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    nos, est = list_product_nos()
    print(f"collected {len(nos)} product_no, est_total={est}")
    rows = []
    for i, pno in enumerate(nos, 1):
        try:
            r = parse_detail(pno)
            if r and r["name"]:
                rows.append(r)
        except Exception as e:  # noqa
            print("  skip", pno, type(e).__name__, e)
        if i % 20 == 0:
            print(f"  {i}/{len(nos)} parsed, rows={len(rows)}")
        time.sleep(0.2)
    path = os.path.join(OUT, "extract_brand_mizuno.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"WROTE {len(rows)} rows -> {path}")
    # field fill summary
    filled = {k: sum(1 for r in rows if str(r.get(k, "")).strip()) for k in HEADER}
    print("FILLED", json.dumps(filled, ensure_ascii=False))


if __name__ == "__main__":
    main()
