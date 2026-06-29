#!/usr/bin/env python3
"""
반스(Vans) 공식몰(www.vans.co.kr, 자체몰/VF Korea) 서버측 상품 표본 추출 (stdlib only).

플랫폼: VF Korea 자체몰(Thymeleaf 서버렌더). 후크: data-* 속성 + 고시 DOM 테이블 (JSON-LD 없음).
  · 리스트/카테고리: /category/{path}?page={p}   (무한스크롤, pageSize=25, ?page=N 으로 추가 페이지)
  · 상세:           /PRODUCT/{styleCode}          (예 VN000Z7410Z)
추출 전략:
  · style_code = 상세 URL의 품번(=고시 Model)
  · name  = data-name 속성
  · color = 선택된 색상 스와치  class="variation-color selectable selected" data-color="..."
  · price = <strong data-price="..."> (KRW)
  · sizes = <input data-attributename="*_SIZE" data-value="..."> (DOM 순서, 중복제거)
  · 고시(소재/제조국/제조연월) = info-name 테이블 (Korean 라벨 키 기준 매핑) → 텍스트로 존재(gosi_status=text)
      소재   : 실제값(예 "캔버스(면) 100%")
      제조국 : "상품 택 참조"(플레이스홀더지만 DOM 텍스트라 그대로 채움)
      제조연월: "상품라벨에서 확인"(동일)
출력: outputs/extract_brand_vans.csv  (utf-8-sig)
  헤더: source,brand,style_code,name,color,price,currency,category,gender,sizes,origin,material,mfg_date,url
"""
import csv
import html as ihtml
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
OUT_CSV = os.path.join(OUT, "extract_brand_vans.csv")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE = "https://www.vans.co.kr"

HEADER = ["source", "brand", "style_code", "name", "color", "price",
          "currency", "category", "gender", "sizes", "origin",
          "material", "mfg_date", "url"]

# (category path, category label, gender) — 신발/의류/가방/모자 + 남/여/키즈 다양성
CATS = [
    ("shoes", "신발", "공용"),
    ("men/allclothe", "의류", "남성"),
    ("women/allclothe", "의류", "여성"),
    ("accessories/bags", "가방", "공용"),
    ("accessories/hat", "모자", "공용"),
    ("kids/allshoes", "신발", "키즈"),
]
EST_CATS = ["shoes", "clothing", "accessories", "kids"]  # est_total = 합
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
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def pipe(parts):
    seen, keep = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            keep.append(p)
    return "|".join(keep)


def list_styles(cat_path, page):
    """카테고리 페이지에서 swatch 앵커의 styleCode 수집 + totalCount."""
    st, h = fetch(f"{BASE}/category/{cat_path}?page={page}")
    styles = []
    for m in re.finditer(
            r'data-product-id="\d+"[^>]*href="/PRODUCT/([A-Za-z0-9]+)"', h):
        styles.append(m.group(1))
    tc = ""
    mt = re.search(r'totalCount:(\d+)', h)
    if mt:
        tc = mt.group(1)
    # 중복 제거(순서 유지)
    seen, uniq = set(), []
    for s in styles:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq, tc


def parse_gosi(h):
    """info-name 고시 테이블 → {korean_key: value}."""
    g = {}
    for m in re.finditer(
            r'info-name="[^"]+"[^>]*>([^<]+)</[^>]+>\s*'
            r'<[^>]*class="[^"]*tag-value[^"]*"[^>]*>(.*?)</', h, re.S):
        key = ihtml.unescape(m.group(1).strip())
        val = ihtml.unescape(re.sub(r"\s+", " ",
                             re.sub(r"<[^>]+>", "", m.group(2))).strip())
        if key and key not in g:
            g[key] = val
    return g


def parse_detail(style, cat_label, gender):
    url = f"{BASE}/PRODUCT/{style}"
    st, h = fetch(url)
    rec = {k: "" for k in HEADER}
    rec["source"] = "vans"
    rec["brand"] = "반스"
    rec["style_code"] = style.upper()
    rec["category"] = cat_label
    rec["url"] = url

    # name
    mn = re.search(r'data-name="([^"]*)"', h)
    rec["name"] = ihtml.unescape(re.sub(r"\s+", " ", mn.group(1)).strip()) if mn else ""

    # color: 선택된 스와치 data-color
    mc = re.search(r'class="variation-color selectable selected"[^>]*data-color="([^"]+)"', h)
    if not mc:
        mc = re.search(r'data-color="([^"]+)"[^>]*class="variation-color selectable selected"', h)
    color = mc.group(1).strip() if mc else ""

    # price
    mp = re.search(r'data-price="([0-9]+)"', h)
    rec["price"] = mp.group(1) if mp else ""
    rec["currency"] = "KRW" if rec["price"] else ""

    # sizes: data-attributename="*_SIZE" 인 input 의 data-value
    sizes = []
    for m in re.finditer(r'<input\b[^>]*\bdata-attributename="[A-Z_]*SIZE"[^>]*>', h):
        mv = re.search(r'data-value="([^"]+)"', m.group(0))
        if mv:
            sizes.append(mv.group(1).strip())
    rec["sizes"] = pipe(sizes)

    # 고시 (Korean 라벨 키 매핑)
    g = parse_gosi(h)
    for k, v in g.items():
        if "소재" in k and not rec["material"]:
            rec["material"] = v
        elif "제조국" in k and not rec["origin"]:
            rec["origin"] = v
        elif ("제조연월" in k or "제조년월" in k) and not rec["mfg_date"]:
            rec["mfg_date"] = v
    # color 보강: 스와치 없으면 고시 '색상' 컬러웨이
    if not color:
        color = g.get("색상", "")
    rec["color"] = color

    # gender 보강: 이름에 키즈/주니어/유아 있으면 키즈
    if gender == "공용" and re.search(r"키즈|주니어|유아|아동|토들러|TODDLER|KIDS", rec["name"], re.I):
        gender = "키즈"
    rec["gender"] = gender
    return rec


def main():
    os.makedirs(OUT, exist_ok=True)

    # est_total = 주요 카테고리 totalCount 합
    est_parts = {}
    for c in EST_CATS:
        try:
            _, tc = list_styles(c, 1)
            est_parts[c] = int(tc) if tc else 0
        except Exception as e:  # noqa: BLE001
            print(f"[est] {c} 실패: {e}", file=sys.stderr)
        time.sleep(0.2)
    est_total = sum(est_parts.values())

    # 수집 (페이지 인터리브)
    collected = []          # (style, cat_label, gender)
    seen = set()
    cat_tc = {}
    for page in range(1, MAX_PAGES + 1):
        added = 0
        for cat_path, label, gender in CATS:
            try:
                styles, tc = list_styles(cat_path, page)
            except Exception as e:  # noqa: BLE001
                print(f"[list] {cat_path} p{page} 실패: {e}", file=sys.stderr)
                continue
            if page == 1:
                cat_tc[cat_path] = tc
            for s in styles:
                if s not in seen:
                    seen.add(s)
                    collected.append((s, label, gender))
                    added += 1
            time.sleep(0.25)
        print(f"[list] page {page}: 누적 {len(collected)}", file=sys.stderr)
        if len(collected) >= TARGET or added == 0:
            break

    collected = collected[:TARGET]
    print(f"수집 styleCode {len(collected)}개 | est_total={est_total} "
          f"({est_parts}) | cat_totalCount={cat_tc}", file=sys.stderr)

    rows, fail = [], 0
    for n, (style, label, gender) in enumerate(collected, 1):
        try:
            rows.append(parse_detail(style, label, gender))
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[detail] {style} 실패: {e}", file=sys.stderr)
        if n % 20 == 0:
            print(f"[detail] {n}/{len(collected)} ...", file=sys.stderr)
        time.sleep(0.18)

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    filled = {k: sum(1 for r in rows if str(r.get(k, "")).strip()) for k in HEADER}
    print(f"\n=== 완료 === 행수 {len(rows)} (실패 {fail}) | est_total={est_total}")
    print("채워진 컬럼:", {k: v for k, v in filled.items() if v})
    print("경로:", OUT_CSV)
    for r in rows[:4]:
        print("SAMPLE:", {k: r[k] for k in
              ("style_code", "name", "color", "price", "category",
               "gender", "sizes", "material", "origin", "mfg_date")})


if __name__ == "__main__":
    main()
