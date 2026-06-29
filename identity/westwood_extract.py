#!/usr/bin/env python3
"""웨스트우드 공식몰(cafe24, westwoodmall.co.kr) 상품 표본 추출 — stdlib만.

후크: JSON-LD(schema.org Product). 리스트=cafe24 카테고리 페이지의
SEO 상품링크(/product/{slug}/{product_no}/category/{cate}/...) 수집 →
상세 /product/detail.html?product_no={id} 의 JSON-LD 파싱.
출력: outputs/extract_brand_westwood.csv (14컬럼 고정 스키마).
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
BASE = "https://westwoodmall.co.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

HEADER = ["source", "brand", "style_code", "name", "color", "price",
          "currency", "category", "gender", "sizes", "origin",
          "material", "mfg_date", "url"]

# 실제 상품 카테고리(머천다이징 버킷 NEW/BEST 제외) → 표시명
CATS = [("231", "MEN"), ("244", "WOMEN"), ("268", "ACC&SHOES"),
        ("262", "NASA"), ("273", "OUTLET")]

MAX_PRODUCTS = 120
MAX_PAGES = 5

_LD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_LINK_RE = re.compile(r'/product/[^"\'>]*?/(\d+)/category/(\d+)/')


def http_get(url, retries=2, timeout=18):
    url = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5 * (i + 1))
    print(f"  ! GET fail {url}: {last}")
    return ""


def parse_jsonld_product(html):
    for blk in _LD_RE.findall(html):
        try:
            d = json.loads(blk)
        except Exception:  # noqa: BLE001
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and it.get("@type") in (
                    "Product", "ProductGroup"):
                return it
    return None


def try_info_notice(html):
    """상품정보제공고시(소재/제조국/제조연월) — 텍스트로 있으면 추출(보통 edibot 이미지)."""
    origin = material = mfg = ""
    m = re.search(r"(?:소재|혼용률)[^가-힣]{0,4}([가-힣A-Za-z0-9 ,.%()/]{2,60})", html)
    if m:
        material = m.group(1).strip()
    m = re.search(r"제조국[^가-힣A-Za-z]{0,4}([가-힣A-Za-z]{2,20})", html)
    if m:
        origin = m.group(1).strip()
    m = re.search(r"제조[연년]월[^0-9]{0,4}(20\d{2}[.\-/]?\s?\d{0,2})", html)
    if m:
        mfg = m.group(1).strip()
    return origin, material, mfg


def normalize(prod, cate_no, cate_name):
    name = (prod.get("name") or "").strip()
    style = ""
    if "_" in name:
        last = name.rsplit("_", 1)[-1].strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-]{3,}", last):
            style = last
            name = name.rsplit("_", 1)[0].strip()
    brand = ""
    b = prod.get("brand")
    if isinstance(b, dict):
        brand = b.get("name", "")
    elif isinstance(b, str):
        brand = b

    offers = prod.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    colors, sizes = [], []
    price = ""
    currency = "KRW"
    full_name = (prod.get("name") or "").strip()
    for o in offers:
        if not isinstance(o, dict):
            continue
        if price == "" and o.get("price"):
            price = o.get("price")
            currency = o.get("priceCurrency", "KRW")
        onm = (o.get("name") or "").strip()
        rest = onm[len(full_name):].strip() if onm.startswith(full_name) else onm
        if not rest:
            continue
        if "-" in rest:
            c, s = rest.rsplit("-", 1)
            c, s = c.strip(), s.strip()
        else:
            c, s = rest, ""
        if c and c not in colors:
            colors.append(c)
        if s and s not in sizes:
            sizes.append(s)
    if price == "" and prod.get("offers"):
        pass

    # gender: 상품명 접두 우선, 없으면 카테고리
    gender = ""
    if name.startswith("남성") or "남성" in name[:4]:
        gender = "남성"
    elif name.startswith("여성") or "여성" in name[:4]:
        gender = "여성"
    elif "공용" in name[:4] or "유니섹스" in name:
        gender = "공용"
    elif cate_name == "MEN":
        gender = "남성"
    elif cate_name == "WOMEN":
        gender = "여성"

    return {
        "source": "westwood",
        "brand": brand,
        "style_code": style,
        "name": name,
        "color": "|".join(colors),
        "price": price,
        "currency": currency,
        "category": cate_name,
        "gender": gender,
        "sizes": "|".join(sizes),
        "origin": "",
        "material": "",
        "mfg_date": "",
        "url": f"{BASE}/product/detail.html?product_no={{}}",
    }


def collect_links():
    """카테고리별로 (product_no, cate_no, cate_name) 수집(최초 발견 카테고리 유지)."""
    seen = {}
    for cate_no, cate_name in CATS:
        if len(seen) >= MAX_PRODUCTS:
            break
        for page in range(1, MAX_PAGES + 1):
            if len(seen) >= MAX_PRODUCTS:
                break
            url = f"{BASE}/product/list.html?cate_no={cate_no}&page={page}"
            html = http_get(url)
            found = []
            for m in _LINK_RE.finditer(html):
                pid, c = m.group(1), m.group(2)
                if pid not in seen:
                    found.append(pid)
                    seen[pid] = (cate_no, cate_name)
            print(f"  {cate_name} p{page}: +{len(found)} (total {len(seen)})")
            if not found:
                break  # 빈 페이지 = 카테고리 끝
            time.sleep(0.3)
    return seen


def main():
    os.makedirs(OUT, exist_ok=True)
    print("[1] 리스트 수집...")
    seen = collect_links()
    pids = list(seen.keys())[:MAX_PRODUCTS]
    print(f"[1] 수집 상품 수: {len(pids)}")

    print("[2] 상세 JSON-LD 파싱...")
    rows = []
    for i, pid in enumerate(pids, 1):
        cate_no, cate_name = seen[pid]
        html = http_get(f"{BASE}/product/detail.html?product_no={pid}")
        if not html:
            continue
        prod = parse_jsonld_product(html)
        if not prod:
            print(f"  [{i}] {pid}: JSON-LD 없음")
            continue
        row = normalize(prod, cate_no, cate_name)
        row["url"] = f"{BASE}/product/detail.html?product_no={pid}"
        origin, material, mfg = try_info_notice(html)
        row["origin"], row["material"], row["mfg_date"] = origin, material, mfg
        rows.append(row)
        if i % 10 == 0 or i == len(pids):
            print(f"  [{i}/{len(pids)}] {row['style_code']} {row['name'][:24]}")
        time.sleep(0.25)

    path = os.path.join(OUT, "extract_brand_westwood.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[3] 저장: {path} ({len(rows)} rows)")

    # 검증 요약
    filled = {k: sum(1 for r in rows if str(r[k]).strip()) for k in HEADER}
    print("[4] 컬럼 채움 현황:")
    for k in HEADER:
        print(f"     {k}: {filled[k]}/{len(rows)}")


if __name__ == "__main__":
    main()
