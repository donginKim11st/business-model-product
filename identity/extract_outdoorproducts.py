#!/usr/bin/env python3
"""아웃도어프로덕츠 공식몰(cafe24) 서버측 상품 표본 추출 (stdlib만).

플랫폼: cafe24 (EC_FRONT). 후크: JSON-LD(schema.org Product).
  · 리스트: /product/list.html?cate_no={N}&page={p}  (그리드 28개/페이지)
  · 상세 : /product/{slug}/{id}/  → <script type="application/ld+json"> Product
      - description = 품번(style_code, 컬러간 공유 모델코드)
      - name 끝 괄호 = 디스플레이 컬러
      - offers[] = 컬러-사이즈 변형(price/priceCurrency/item_code url)
  · 상품정보제공고시(소재·제조국·제조년월)는 상세 이미지로만 제공 → 텍스트 미존재(대개 공란).
표본: UNISEX(901) + WOMEN(902), product_no 단위 1행(컬러별), 최대 ~120행.
출력: outputs/extract_brand_outdoorproducts.csv (지정 헤더 정확히).
"""
import csv
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
BASE = "https://outdoorproducts.co.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)

# 상품 유형(category) — 표본 상품명에서 실제 등장한 토큰 기반(긴 키워드 우선 매칭)
CAT_RULES = [
    ("버뮤다", "숏츠"), ("숏츠", "숏츠"), ("쇼츠", "숏츠"), ("반바지", "숏츠"),
    ("조거", "팬츠"), ("팬츠", "팬츠"), ("바지", "팬츠"), ("슬랙스", "팬츠"),
    ("레깅스", "레깅스"),
    ("후드집업", "집업"), ("집업", "집업"), ("후디", "후드"), ("후드", "후드"),
    ("맨투맨", "맨투맨"), ("스웨트셔츠", "맨투맨"), ("스웻", "맨투맨"),
    ("바람막이", "자켓"), ("아노락", "자켓"), ("재킷", "자켓"), ("자켓", "자켓"),
    ("점퍼", "자켓"), ("코치", "자켓"), ("윈드", "자켓"),
    ("베스트", "베스트"), ("조끼", "베스트"),
    ("니트", "니트"), ("카디건", "카디건"),
    ("셋업", "셋업"), ("SET", "셋업"),
    ("슬리브리스", "슬리브리스"), ("민소매", "슬리브리스"), ("나시", "슬리브리스"),
    ("티셔츠", "티셔츠"), ("반팔티", "티셔츠"), ("긴팔티", "티셔츠"),  # '셔츠'보다 먼저
    ("셔츠", "셔츠"),
    ("원피스", "원피스"), ("스커트", "스커트"), ("치마", "스커트"),
    ("바이져", "모자"), ("바이저", "모자"), ("버켓햇", "모자"), ("버킷햇", "모자"),
    ("버켓", "모자"), ("버킷", "모자"), ("캡", "모자"), ("비니", "모자"),
    ("햇", "모자"), ("모자", "모자"),
    ("백팩", "가방"), ("크로스백", "가방"), ("슬링백", "가방"), ("메신저", "가방"),
    ("토트백", "가방"), ("토트", "가방"), ("더플", "가방"), ("힙색", "가방"),
    ("웨이스트", "가방"), ("파우치", "가방"), ("가방", "가방"), ("백", "가방"),
    ("양말", "양말"), ("삭스", "양말"), ("장갑", "장갑"), ("머플러", "머플러"),
    ("벨트", "벨트"), ("타월", "타월"), ("수건", "타월"),
    ("샌들", "신발"), ("슬리퍼", "신발"), ("슈즈", "신발"), ("운동화", "신발"),
    ("티", "티셔츠"),  # 최후 폴백(짧은 키워드)
]


def http_get(url, retries=2, timeout=20):
    url = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def list_ids(cate, pages):
    """카테고리 그리드에서 (product_no, slug) 수집(등장순, 유니크)."""
    pat = re.compile(r'href="/product/([^"]*?)/(\d+)/category/' + str(cate) + r'/display/\d+/"')
    out, seen = [], set()
    for p in pages:
        html = http_get(f"{BASE}/product/list.html?cate_no={cate}&page={p}")
        found = 0
        for m in pat.finditer(html):
            pid = m.group(2)
            if pid not in seen:
                seen.add(pid)
                out.append((pid, m.group(1)))
                found += 1
        if found == 0:
            break
    return out


def first_product_ld(html):
    for b in _LD.findall(html):
        try:
            o = json.loads(b.strip())
        except Exception:  # noqa: BLE001
            continue
        for it in (o if isinstance(o, list) else [o]):
            if isinstance(it, dict) and it.get("@type") in ("Product", "ProductGroup"):
                return it
    return None


def categorize(name):
    for kw, cat in CAT_RULES:
        if kw in name:
            return cat
    return ""


def parse_product(pid, slug, gender):
    url = f"{BASE}/product/{slug}/{pid}/"
    html = http_get(url)
    ld = first_product_ld(html)
    if not ld:
        return None, "no-jsonld"
    name = (ld.get("name") or "").strip()
    style = (ld.get("description") or "").strip()
    brand = ((ld.get("brand") or {}).get("name") if isinstance(ld.get("brand"), dict)
             else ld.get("brand")) or "아웃도어프로덕츠"
    offers = ld.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]

    # 컬러: 상품명 끝 괄호(디스플레이 컬러). 폴백: 변형 토큰의 컬러.
    pm = re.search(r'\(([^)]*)\)\s*$', name)
    paren = pm.group(1).strip() if pm else ""

    sizes, vcolors, prices, currency = [], [], [], "KRW"
    for of in offers:
        lab = (of.get("name") or "")
        var = lab[len(name):].strip() if lab.startswith(name) else lab.strip()
        if of.get("price") not in (None, ""):
            prices.append(of["price"])
        if of.get("priceCurrency"):
            currency = of["priceCurrency"]
        if "-" in var:                      # "컬러-사이즈"
            col, size = var.rsplit("-", 1)
            size = size.strip()
            if size and size not in sizes:
                sizes.append(size)
            col = col.strip()
            if col and col not in vcolors:
                vcolors.append(col)
        elif var:                            # 사이즈만(드묾)
            if var not in sizes:
                sizes.append(var)
        # var=="" → 단일옵션(원사이즈 모자 등): 사이즈 없음
    color = paren or ("|".join(vcolors))
    price = prices[0] if prices else ""

    rec = {
        "source": "outdoorproducts",
        "brand": brand,
        "style_code": style,
        "name": name,
        "color": color,
        "price": price,
        "currency": currency,
        "category": categorize(name),
        "gender": gender,
        "sizes": "|".join(sizes),
        "origin": "",        # 상품정보제공고시 = 이미지 제공 → 텍스트 미존재
        "material": "",
        "mfg_date": "",
        "url": url,
    }
    return rec, "ok"


def main():
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    # 표본: UNISEX(901) p1-3 → gender=UNISEX, WOMEN(902) p1-2 → gender=WOMEN (first-seen 우선)
    plan = [("901", [1, 2, 3], "UNISEX"), ("902", [1, 2], "WOMEN")]
    items, seen = [], set()
    for cate, pages, gender in plan:
        for pid, slug in list_ids(cate, pages):
            if pid not in seen:
                seen.add(pid)
                items.append((pid, slug, gender))
    items = items[:cap]
    print(f"표본 product_no {len(items)}개 수집 → 상세 파싱…", file=sys.stderr)

    rows, fails = [], []
    for i, (pid, slug, gender) in enumerate(items):
        try:
            rec, status = parse_product(pid, slug, gender)
            if rec:
                rows.append(rec)
            else:
                fails.append((pid, status))
        except Exception as e:  # noqa: BLE001
            fails.append((pid, str(e)))
            print(f"  [skip] {pid}: {e}", file=sys.stderr)
        if (i + 1) % 25 == 0:
            print(f"  …{i + 1}/{len(items)}", file=sys.stderr)
        time.sleep(0.08)

    os.makedirs(OUT, exist_ok=True)
    cp = os.path.join(OUT, "extract_brand_outdoorproducts.csv")
    with open(cp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)

    # 요약
    filled = {k: sum(1 for r in rows if str(r.get(k, "")).strip()) for k in HEADER}
    print(f"\n{len(rows)} 행 작성 → {cp}", file=sys.stderr)
    print("채움 카운트:", filled, file=sys.stderr)
    print("category 분포:", {c: sum(1 for r in rows if r['category'] == c)
                            for c in sorted({r['category'] for r in rows})}, file=sys.stderr)
    nocat = [r['name'] for r in rows if not r['category']]
    if nocat:
        print("category 미매칭:", nocat[:20], file=sys.stderr)
    if fails:
        print("실패:", fails[:10], file=sys.stderr)
    for r in rows[:8]:
        print(f"  {r['style_code']:14} {r['name'][:26]:28} {str(r['price']):>7} "
              f"{r['color']:10} [{r['category']}/{r['gender']}] sizes={r['sizes']}", file=sys.stderr)


if __name__ == "__main__":
    main()
