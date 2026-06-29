#!/usr/bin/env python3
"""
잔스포츠 공식몰(jansport.co.kr, cafe24) 서버측 상품 표본 추출 (stdlib only).

플랫폼: cafe24. 후크: JSON-LD Product.
  · 리스트/카테고리: /product/list.html?cate_no={N}&page={p}
  · 상세:           /product/detail.html?product_no={pid}
추출 전략:
  · JSON-LD Product → name / price / priceCurrency
  · SPECIFICATION 블록 "스타일: <품번>" → style_code, "용량: <NL>" → capacity
  · 옵션 select(option_title="사이즈") → sizes (가방이라 대개 ONESIZE)
  · color = 상품명 끝의 영문 컬러웨이 라벨 (JanSport: "<한글모델> <COLORWAY>")
  · 고시(소재/제조국/제조년월): 상세 HTML 텍스트에 없음 → 이미지 추정(gosi_status=image), 공란
출력: outputs/extract_brand_jansport.csv  (utf-8-sig)
  헤더: source,brand,style_code,name,color,price,currency,category,gender,sizes,origin,material,mfg_date,url
"""
import csv
import html as ihtml
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
OUT_CSV = os.path.join(OUT, "extract_brand_jansport.csv")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE = "https://jansport.co.kr"

HEADER = ["source", "brand", "style_code", "name", "color", "price",
          "currency", "category", "gender", "sizes", "origin",
          "material", "mfg_date", "url"]

# product-type categories (interleaved for category diversity)
CATS = {44: "백팩", 130: "크로스백", 59: "미니백", 88: "여행용",
        60: "악세사리", 64: "랩탑가방"}
EST_CAT = 63          # 전체상품 — used for est_total
TARGET = 120
MAX_PAGES = 5


def fetch(url, timeout=30, retries=2):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def list_ids(cate, page):
    st, h = fetch(f"{BASE}/product/list.html?cate_no={cate}&page={page}")
    ids = []
    for m in re.findall(r'/product/[^"\']*?/(\d+)/category/', h):
        if m not in ids:
            ids.append(m)
    cnt = ""
    mc = re.search(r'prdCount[^0-9]*([\d,]+)', h)
    if mc:
        cnt = mc.group(1).replace(",", "")
    return ids, cnt


def pipe(parts):
    seen, keep = set(), []
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            keep.append(p)
    return "|".join(keep)


def options(h):
    out = []
    for m in re.findall(r'<select[^>]*option_title="[^"]*"[^>]*>(.*?)</select>',
                        h, re.S):
        for o in re.findall(r'<option[^>]*>(.*?)</option>', m):
            t = ihtml.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', o))).strip()
            if not t or '옵션을 선택' in t or set(t) <= set('- '):
                continue
            out.append(t)
    return out


def color_from_name(name):
    # JanSport: "<한글 모델명> <COLORWAY(영문 대문자)>"  -> 끝의 영문 라벨
    m = re.search(r'([A-Z][A-Z0-9]*(?:\s+[A-Z0-9]+){0,4})\s*$', name.strip())
    return m.group(1).strip() if m else ""


def parse_detail(pid, cate_label):
    url = f"{BASE}/product/detail.html?product_no={pid}"
    st, h = fetch(url)
    rec = {k: "" for k in HEADER}
    rec["source"] = "jansport"
    rec["brand"] = "잔스포츠"
    rec["category"] = cate_label
    rec["url"] = url

    # --- JSON-LD Product ---
    name = ""
    price = ""
    currency = ""
    for b in re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            h, re.S):
        try:
            d = json.loads(b.strip())
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict) and d.get("@type") in ("Product", "ProductGroup"):
            name = d.get("name", "") or name
            offers = d.get("offers") or []
            if isinstance(offers, dict):
                offers = [offers]
            prices = []
            for o in offers:
                if isinstance(o, dict) and o.get("price") not in (None, ""):
                    try:
                        prices.append(float(o["price"]))
                    except Exception:  # noqa: BLE001
                        pass
                    currency = o.get("priceCurrency", currency)
            if prices:
                p = min(prices)
                price = str(int(p)) if p == int(p) else str(p)
            break
    name = re.sub(r'\s+', ' ', ihtml.unescape(name)).strip()
    rec["name"] = name
    rec["price"] = price
    rec["currency"] = currency or ("KRW" if price else "")

    # --- style_code (SPECIFICATION "스타일: JS0A...") ---
    ms = re.search(r'스타일\s*:\s*([A-Za-z0-9\-]+)', h)
    rec["style_code"] = ms.group(1).strip().upper() if ms else ""

    # --- sizes: 옵션(사이즈) + 용량(capacity) ---
    sizes = options(h)
    mc = re.search(r'용량\s*:\s*([0-9.]+\s*L)', h)
    if mc:
        sizes = sizes + [mc.group(1).replace(" ", "")]
    rec["sizes"] = pipe(sizes)

    # --- color (상품명 컬러웨이 영문 라벨) ---
    rec["color"] = color_from_name(name)

    # --- gender (대개 유니섹스; 키즈/주니어만 표기) ---
    if re.search(r'키즈|주니어|유아|아동', name):
        rec["gender"] = "아동"

    # --- 고시(소재/제조국/제조년월): 상세 텍스트에 없음 → 이미지 추정, 공란 ---
    return rec


def main():
    os.makedirs(OUT, exist_ok=True)

    # est_total from 전체상품
    est_total = ""
    try:
        _, est_total = list_ids(EST_CAT, 1)
    except Exception as e:  # noqa: BLE001
        print("[est] 실패:", e, file=sys.stderr)

    collected = []           # (id, cate_label)
    seen = set()
    cat_counts = {}
    for page in range(1, MAX_PAGES + 1):
        added = 0
        for cate, label in CATS.items():
            try:
                ids, cnt = list_ids(cate, page)
            except Exception as e:  # noqa: BLE001
                print(f"[list] cate={cate} p={page} 실패: {e}", file=sys.stderr)
                continue
            if page == 1:
                cat_counts[label] = cnt
            for i in ids:
                if i not in seen:
                    seen.add(i)
                    collected.append((i, label))
                    added += 1
            time.sleep(0.25)
        print(f"[list] page {page}: 누적 {len(collected)}", file=sys.stderr)
        if len(collected) >= TARGET or added == 0:
            break

    collected = collected[:TARGET]
    print(f"수집 product_no {len(collected)}개 | est_total(전체상품)={est_total} "
          f"| cat_counts={cat_counts}", file=sys.stderr)

    rows, fail = [], 0
    for n, (pid, label) in enumerate(collected, 1):
        try:
            rows.append(parse_detail(pid, label))
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[detail] {pid} 실패: {e}", file=sys.stderr)
        if n % 20 == 0:
            print(f"[detail] {n}/{len(collected)} ...", file=sys.stderr)
        time.sleep(0.2)

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    filled = {k: sum(1 for r in rows if str(r.get(k, "")).strip()) for k in HEADER}
    print(f"\n=== 완료 === 행수 {len(rows)} (실패 {fail}) | est_total={est_total}")
    print("채워진 컬럼:", {k: v for k, v in filled.items() if v})
    print("경로:", OUT_CSV)
    # 샘플 3행 점검
    for r in rows[:3]:
        print("SAMPLE:", {k: r[k] for k in
              ("style_code", "name", "color", "price", "category", "sizes")})


if __name__ == "__main__":
    main()
