#!/usr/bin/env python3
"""
콜핑(KOLPING) 공식몰 server-side 추출기 — ekolping.co.kr (cafe24, hook=jsonld).

리스트: /product/list2.html?cate_no={N}&page={p}   (이 스토어는 list.html이 404, list2.html 사용)
  상세 링크: /product/<slug>/<id>/category/<cate>/display/
상세  : /product/detail.html?product_no={id}
  · <script type="application/ld+json"> @type=Product → name/brand/offers(price,currency,url,item_code)
  · 기본정보 th/td 테이블: 모델명(style_code)/브랜드/판매가/정가
  · 옵션 select(option1/option2) + var option_name_mapper('색상#$%사이즈') → color/size 정규화
  · breadcrumb(xans-product-headcategory) → category
  · 고시(소재/제조국/제조년월)은 서버측 정적 HTML/AJAX에 텍스트로 미존재 → 공란, gosi_status=image
출력  : outputs/extract_brand_kolping.csv  (지정 스키마, utf-8-sig)
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
BASE = "https://ekolping.co.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

FIELDS = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]

# 다양성 확보용 상위 카테고리 인터리브 (cate_no -> 라벨)
CATS = {
    44: "남성", 45: "여성", 47: "신발", 46: "아동",
    48: "등산", 50: "골프", 49: "캠핑",
}
TARGET = 120
MAX_PAGES = 5


def http_get(url, retries=2, timeout=20):
    url = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            last = e
        except Exception as e:
            last = e
        time.sleep(0.7 * (i + 1))
    print(f"  ! GET fail {url}: {last}", file=sys.stderr)
    return ""


def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def list_ids(cate_no, page):
    h = http_get(f"{BASE}/product/list2.html?cate_no={cate_no}&page={page}")
    if not h:
        return [], ""
    ids = list(dict.fromkeys(re.findall(r"/product/[^\"']*?/(\d+)/category/", h)))
    mc = re.search(r"prdCount[^0-9]*([\d,]+)", h)
    cnt = mc.group(1).replace(",", "") if mc else ""
    return ids, cnt


def jsonld_product(h):
    for b in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', h, re.S):
        try:
            d = json.loads(b.strip())
        except Exception:
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and it.get("@type") in ("Product", "ProductGroup"):
                return it
    return None


def th_td(h):
    """기본정보 th/td 테이블 -> {label: value} (모델명/브랜드/판매가/정가)."""
    out = {}
    for m in re.finditer(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", h, re.S):
        k = clean(m.group(1))
        v = clean(m.group(2))
        if k and k not in out:
            out[k] = v
    return out


def desctable(h):
    """상품정보제공고시 테이블(id=desctable, td2 라벨 / td3 값) -> {label: value}."""
    out = {}
    m = re.search(r'<table[^>]*id="desctable"[^>]*>(.*?)</table>', h, re.S)
    if not m:
        return out
    for r in re.finditer(r'<td[^>]*class="td2"[^>]*>(.*?)</td>\s*<td[^>]*class="td3"[^>]*>(.*?)</td>', m.group(1), re.S):
        k = clean(r.group(1))
        v = clean(r.group(2))
        if k and k not in out:
            out[k] = v
    return out


def norm_mfg(v):
    v = (v or "").strip()
    if re.fullmatch(r"\d{8}", v):
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    if re.fullmatch(r"\d{6}", v):
        return f"{v[:4]}-{v[4:]}"
    m = re.search(r"(20\d{2})[.\-/년 ]+(\d{1,2})", v)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return v


_DASH = re.compile(r"^-+$")


def _clean_opts(raw):
    out = []
    for o in raw:
        t = ihtml.unescape(o).strip()
        if not t or _DASH.match(t):
            continue
        if "선택" in t or "[필수]" in t or "[선택]" in t:
            continue
        if t not in out:
            out.append(t)
    return out


def options(h):
    """옵션 select + option_name_mapper로 color/size 분리. (mapper 순서 = select 순서)"""
    selects = []  # (name, [opts])
    for m in re.finditer(r'<select[^>]*name="(option\d+)"[^>]*>(.*?)</select>', h, re.S):
        opts = _clean_opts(re.findall(r"<option[^>]*>([^<]+)</option>", m.group(2)))
        if opts:
            selects.append((m.group(1), opts))
    mp = re.search(r"var option_name_mapper\s*=\s*'([^']*)'", h)
    labels = mp.group(1).split("#$%") if mp else []

    colors, sizes = [], []
    for i, (_, opts) in enumerate(selects):
        lab = labels[i] if i < len(labels) else ""
        low = lab.lower()
        if any(k in lab for k in ("색상", "색", "칼라", "컬러", "color")) or "color" in low:
            colors = opts
        elif any(k in lab for k in ("사이즈", "치수", "size")) or "size" in low:
            sizes = opts
        else:
            # 라벨 미스 -> 휴리스틱: 숫자/알파벳사이즈 위주면 size, 아니면 color
            if all(re.fullmatch(r"\d{2,3}[A-Z]?|XX?S|S|M|L|XX?L|XXXL|[2-5]XL|FREE|F", o, re.I) for o in opts):
                sizes = sizes or opts
            else:
                colors = colors or opts
    return colors, sizes


def offer_fallback_opts(base_name, offers):
    """옵션 select가 없을 때 offer name('NAME COLOR-SIZE')에서 color/size 복원."""
    colors, sizes = [], []
    for o in offers:
        nm = (o.get("name") or "").strip()
        opt = nm[len(base_name):].strip() if nm.startswith(base_name) else nm
        opt = opt.strip(" -/")
        if not opt:
            continue
        m = re.match(r"^(.*?)[-_/ ]+([0-9]{2,3}[A-Za-z]?|XX?S|S|M|L|XX?L|XXXL|[2-5]XL|FREE|F)$", opt, re.I)
        if m:
            c, s = m.group(1).strip(), m.group(2).strip()
        else:
            c, s = opt, ""
        if c and c not in colors:
            colors.append(c)
        if s and s not in sizes:
            sizes.append(s)
    return colors, sizes


_PROMO = re.compile(r"광고|완판|입고|이벤트|기획|특가|단독|신상|세일|할인|행사|EVENT|BEST", re.I)
_SEASON = {"봄/가을", "봄가을", "여름", "겨울", "간절기", "사계절", "봄", "가을"}


_SIZE_SUFFIX = re.compile(
    r"^(.*\S)[-_/ ]+(\d{2,3}[A-Za-z]?|XX?S|S|M|L|XX?L|XXXL|[2-5]XL|FREE|F)$", re.I)


def split_combined(colors, sizes):
    """'CORAL-110'/'기타-FREE'처럼 색상-사이즈 결합 옵션이면 색상/사이즈로 분리."""
    new_colors, extra = [], list(sizes)
    for c in colors:
        m = _SIZE_SUFFIX.match(c.strip())
        if m:
            new_colors.append(m.group(1).strip())
            extra.append(m.group(2).strip())
        else:
            new_colors.append(c.strip())
    return new_colors, extra


def breadcrumb(h, fallback):
    """헤드카테고리 경로의 마지막 '상품유형' 크럼(프로모션성 제외), 없으면 GNB 라벨."""
    m = re.search(r'xans-product-headcategory[^"]*"\s*>(.*?)</div>', h, re.S)
    crumbs = []
    if m:
        crumbs = [clean(a) for a in re.findall(r"<a[^>]*>(.*?)</a>", m.group(1), re.S)]
        crumbs = [c for c in crumbs if c and c not in ("홈", "현재 위치")]
    for c in reversed(crumbs):
        if len(c) <= 12 and not _PROMO.search(c) and c not in _SEASON:
            return c
    return fallback


def gender_of(name, desc=""):
    txt = f"{name} {desc}"
    if re.search(r"공용|남녀|UNISEX", txt, re.I):
        return "UNISEX"
    male = re.search(r"\(\s*남(아|성|자)?\s*\)|남성|남아|\bMEN\b|\bMALE\b|\bBOY", txt, re.I)
    female = re.search(r"\(\s*여(아|성|자)?\s*\)|여성|여아|\bWOMEN\b|\bWMN\b|\bFEMALE\b|\bGIRL", txt, re.I)
    if male and not female:
        return "MALE"
    if female and not male:
        return "FEMALE"
    return ""


def detail(pid, cate_label):
    h = http_get(f"{BASE}/product/detail.html?product_no={pid}")
    rec = {k: "" for k in FIELDS}
    rec["source"] = "kolping"
    rec["url"] = f"{BASE}/product/detail.html?product_no={pid}"
    if not h:
        return None
    d = jsonld_product(h)
    table = th_td(h)
    gosi = desctable(h)

    name = (d.get("name") if d else "") or table.get("상품명", "")
    name = name.strip()
    rec["name"] = name
    if not name:
        return None

    brand = ""
    if d:
        b = d.get("brand") or {}
        brand = b.get("name") if isinstance(b, dict) else (b or "")
    brand = (brand or table.get("브랜드") or "KOLPING").strip()
    rec["brand"] = brand

    # style_code: 모델명 (기본정보표 > 고시표 > 상품명 내 코드)
    style = (table.get("모델명") or gosi.get("모델명") or "").strip()
    if not style:
        mm = re.search(r"\b([A-Z]{2,4}\d{3,}[A-Z]?)\b", name)
        if mm:
            style = mm.group(1)
    rec["style_code"] = style

    # price/currency: min offer price, fallback 판매가
    offers = []
    if d:
        offers = d.get("offers") or []
        if isinstance(offers, dict):
            offers = [offers]
    prices, currency = [], ""
    for o in offers:
        if isinstance(o, dict) and o.get("price") not in (None, ""):
            try:
                prices.append(float(o["price"]))
            except Exception:
                pass
            currency = o.get("priceCurrency") or currency
    if prices:
        p = min(prices)
        rec["price"] = str(int(p)) if p == int(p) else str(p)
    else:
        sp = table.get("판매가", "")
        m = re.search(r"([\d,]+)", sp)
        if m:
            rec["price"] = m.group(1).replace(",", "")
    rec["currency"] = (currency or ("KRW" if rec["price"] else "")).strip()

    # color/size: 옵션 select 우선 -> 고시 색상 -> offer name
    colors, sizes = options(h)
    if not colors and offers:
        oc, _ = offer_fallback_opts(name, offers)
        colors = oc
    if not colors and gosi.get("색상"):
        colors = [c.strip() for c in re.split(r"[,/]", gosi["색상"]) if c.strip()]
    if not sizes and offers:
        _, osz = offer_fallback_opts(name, offers)
        sizes = osz
    # 단일 결합옵션('색상-사이즈' = "CORAL-110"/"기타-FREE")이면 컬러에서 사이즈 토큰 분리
    colors, sizes = split_combined(colors, sizes)
    rec["color"] = "|".join(dict.fromkeys(colors))
    rec["sizes"] = "|".join(dict.fromkeys(sizes))

    # category: breadcrumb 마지막 상품유형 크럼 > GNB 라벨
    rec["category"] = breadcrumb(h, cate_label)

    rec["gender"] = gender_of(name, (d.get("description") if d else "") or "")

    # 고시(소재/제조국/제조년월): 고시표에 텍스트로 있으면 채움, 없으면 공란(이미지/상세페이지참고)
    material = (gosi.get("소재") or "").strip()
    if material in ("상세페이지참고", "상세참고", "-"):
        material = ""
    origin = (gosi.get("제조국") or gosi.get("원산지") or gosi.get("원산국") or "").strip()
    if origin in ("상세페이지참고", "상세참고", "-"):
        origin = ""
    mfg = norm_mfg(gosi.get("제조연월") or gosi.get("제조년월") or gosi.get("제조일자") or gosi.get("제조일") or "")
    if mfg in ("상세페이지참고", "상세참고", "-"):
        mfg = ""
    rec["material"] = material
    rec["origin"] = origin
    rec["mfg_date"] = mfg
    return rec


def main():
    os.makedirs(OUT, exist_ok=True)
    counts = {}
    collected = []  # (id, label)
    seen = set()
    for page in range(1, MAX_PAGES + 1):
        for cate, label in CATS.items():
            ids, cnt = list_ids(cate, page)
            if page == 1:
                counts[f"{cate}:{label}"] = cnt
            for i in ids:
                if i not in seen:
                    seen.add(i)
                    collected.append((i, label))
            time.sleep(0.2)
        print(f"[list] after page {page}: {len(collected)} unique ids")
        if len(collected) >= TARGET:
            break
    collected = collected[:TARGET]
    print(f"[list] collected {len(collected)} ids; counts={counts}")

    rows = []
    for n, (pid, label) in enumerate(collected, 1):
        try:
            r = detail(pid, label)
        except Exception as e:
            print(f"  ERR pid={pid}: {type(e).__name__} {e}", file=sys.stderr)
            r = None
        if r:
            rows.append(r)
            if n % 10 == 0 or n <= 3:
                print(f"  [{n}/{len(collected)}] {pid} {r['style_code'] or '-':<10} "
                      f"{r['name'][:22]:<22} {r['price']} c={r['color'][:18]} sz={r['sizes'][:18]}")
        else:
            print(f"  [{n}/{len(collected)}] {pid} (skip/no-data)")
        time.sleep(0.2)

    path = os.path.join(OUT, "extract_brand_kolping.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    filled = {k: sum(1 for r in rows if str(r[k]).strip()) for k in FIELDS}
    print(f"\n[done] {len(rows)} rows -> {path}")
    print("[filled]", json.dumps(filled, ensure_ascii=False))
    print("[counts]", json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
