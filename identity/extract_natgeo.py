#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server-side product sample extractor for National Geographic (내셔널지오그래픽)
on N.STATION (nstationmall.com, 더네이쳐홀딩스 직영) — a Vue SPA with a
self-built backend (not cafe24 / godomall / Next.js).

LIST  : /goods/category/{code}            (hook: dom — server-rendered grid)
        -> harvest /goods/detail/{goodsId} links from a spread of leaf
           categories (apparel/bags/shoes/caps/kids), dedup preserving order.
DETAIL: /goods/detail/{goodsId}           (server-rendered embedded JSON + DOM)
        -> var goodsInfo = {...}          : id, name, brandName, price, dcPrice,
                                            category / firstCategory
        -> <dt>스타일</dt><dd>...</dd>      : style_code (품번/모델코드)
        -> 상품고시 <th><b>k</b></th><td><b>v</b></td> : 원산지/제품 소재/제조연월
OPTIONS: /goods/detail/{goodsId}/options  (hook: internal_json — reverse-engineered XHR)
        -> {"options":[{"_name": color, "sub":[{"_name": size, "_price":..}]}]}
           color = options[]._name ; sizes = union of sub[]._name ; price = _price

Notes:
  - price = dcPrice (판매가); coupon price (쿠폰 할인가) ignored.
  - 고시 소재/제조국/제조연월 = "상세페이지 참조" → 이미지(공란, gosi_status=image).
    origin은 별도 '원산지' 행(텍스트, 예: 베트남산)에서 채움.
  - 색상 변형이 서로 다른 goodsId(동일 style_code)로 분리되어 있어 표본에
    동일 style 다른 컬러 행이 섞일 수 있음(컬럼 color로 구분).
"""
import re, csv, json, time, urllib.request, urllib.parse, urllib.error

BASE = "https://www.nstationmall.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
OUT = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_natgeo.csv"
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
SAMPLE = 120

# diverse leaf categories across product types (verified category-specific)
SEED_CATS = [
    "001002014",  # 남성 상의 반팔티
    "001001013",  # 남성 아우터 자켓/점퍼
    "001003008",  # 남성 하의 롱팬츠
    "002002014",  # 여성 상의 반팔티
    "002025001",  # 여성 원피스
    "002001010",  # 여성 자켓/점퍼
    "003001001",  # 가방 백팩
    "004001001",  # 신발 운동화
    "006016001",  # 용품 모자 볼캡
    "007002007",  # 키즈 반팔티
    "010005004",  # 캐리어 기내용 20형
]

GENDER_MAP = [("남녀공용", "공용"), ("남성", "남성"), ("여성", "여성"),
              ("공용", "공용"), ("키즈", "키즈"), ("아동", "키즈"),
              ("주니어", "키즈"), ("유아", "키즈"), ("KIDS", "키즈")]
# seed-category prefix -> gender fallback
CAT_GENDER = {"001": "남성", "002": "여성", "007": "키즈"}
PLACEHOLDER = {"", "-", "상세페이지 참조", "상세페이지참조", "상세설명참조", "상세페이지 참고",
               "상세설명에 표시", "상세설명에표시", "상세 설명에 표시", "상세페이지 표시",
               "상세정보 참조", "상세정보참조"}


def fetch(url, as_json=False, retries=2):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    hdr = {
        "User-Agent": UA,
        "Accept": "application/json,*/*" if as_json
                  else "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE + "/natgeo",
    }
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(enc, headers=hdr), timeout=30) as r:
                data = r.read().decode("utf-8", errors="replace")
            return json.loads(data) if as_json else data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (attempt + 1))
    raise last


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def parse_goods_info(html):
    """Parse the server-rendered `var goodsInfo = { ... }` JS object (loosely)."""
    m = re.search(r"var\s+goodsInfo\s*=\s*\{(.*?)\}", html, re.S)
    out = {}
    if not m:
        return out
    body = m.group(1)
    for key in ("id", "name", "brandName", "price", "dcPrice",
                "category", "firstCategory", "secondCategory", "thirdCategory"):
        km = re.search(r"\b" + key + r"\s*:\s*('([^']*)'|\"([^\"]*)\"|([\d.]+))", body)
        if km:
            out[key] = km.group(2) or km.group(3) or km.group(4) or ""
    return out


def parse_style(html):
    m = re.search(r"<dt>\s*스타일\s*</dt>\s*<dd>(.*?)</dd>", html, re.S)
    return clean(m.group(1)) if m else ""


def parse_gosi(html):
    """상품정보제공고시 table: <th><b>키</b></th><td><b>값</b></td>."""
    out = {}
    for m in re.finditer(r"<th[^>]*><b>(.*?)</b></th>\s*<td><b>(.*?)</b></td>", html, re.S):
        out[clean(m.group(1))] = clean(m.group(2))
    return out


def gender_of(name, cat_code):
    for token, g in GENDER_MAP:
        if token in name:
            return g
    return CAT_GENDER.get(cat_code[:3], "")


def parse_options(opt):
    """Return (color_str, sizes_str, price) from the /options JSON.
    Tolerates color-only, single FREE size, or empty options."""
    colors, sizes, prices = [], [], []
    for o in opt.get("options", []) or []:
        cname = (o.get("_name") or "").strip()
        if cname and cname not in colors:
            colors.append(cname)
        for s in o.get("sub", []) or []:
            sname = (s.get("_name") or "").strip()
            # "-" is the API's one-size / no-size-axis marker (bags, caps, carriers)
            if sname and sname not in ("-",) and sname not in sizes:
                sizes.append(sname)
            try:
                prices.append(int(float(s.get("_price"))))
            except (TypeError, ValueError):
                pass
    top_price = opt.get("_price")
    try:
        top_price = int(float(top_price))
    except (TypeError, ValueError):
        top_price = min(prices) if prices else None
    return "|".join(colors), "|".join(sizes), top_price


def collect_ids():
    # 1) fetch each seed category's id list
    per_cat = []  # list of (code, [ids...])
    total_distinct = set()
    for code in SEED_CATS:
        try:
            html = fetch(f"{BASE}/goods/category/{code}")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] cat {code} fail: {e}")
            per_cat.append((code, []))
            continue
        ids = list(dict.fromkeys(re.findall(r"/goods/detail/(\d+)", html)))
        per_cat.append((code, ids))
        total_distinct.update(ids)
        print(f"  cat {code}: {len(ids)} ids")
        time.sleep(0.2)
    # 2) round-robin interleave so the sample spans all product types
    pool, seen = [], set()
    idx = 0
    while any(idx < len(ids) for _, ids in per_cat):
        for code, ids in per_cat:
            if idx < len(ids):
                gid = ids[idx]
                if gid not in seen:
                    seen.add(gid)
                    pool.append((gid, code))
        idx += 1
    return pool, len(total_distinct)


def main():
    print("== collecting goodsIds from seed categories ==")
    pool, est_total = collect_ids()
    sample = pool[:SAMPLE]
    print(f"discoverable pool (lower bound) = {est_total}; sampling {len(sample)}")

    rows = []
    filled = {c: 0 for c in HEADER}
    fails = 0
    material_text = 0      # rows with real 제품 소재 text (not placeholder)
    mfg_text = 0
    origin_text = 0
    for i, (gid, cat_code) in enumerate(sample, 1):
        url = f"{BASE}/goods/detail/{gid}"
        try:
            html = fetch(url)
            gi = parse_goods_info(html)
            gosi = parse_gosi(html)
            try:
                opt = fetch(f"{url}/options", as_json=True)
            except Exception:
                opt = {}
            color, sizes, opt_price = parse_options(opt)

            name = (gi.get("name") or "").strip()
            if not name:
                m = re.search(r'property="og:title"\s+content="([^"]*)"', html)
                name = clean(m.group(1)) if m else ""

            price = gi.get("dcPrice") or gi.get("price") or (str(opt_price) if opt_price else "")

            material = gosi.get("제품 소재") or gosi.get("소재") or ""
            mfg = gosi.get("제조연월") or gosi.get("제조년월") or ""
            origin = gosi.get("원산지") or gosi.get("제조국") or ""
            if material in PLACEHOLDER:
                material = ""
            else:
                material_text += 1
            if mfg in PLACEHOLDER:
                mfg = ""
            else:
                mfg_text += 1
            if origin in PLACEHOLDER:
                origin = ""
            elif origin:
                origin_text += 1

            row = {
                "source": "natgeo",
                "brand": (gi.get("brandName") or "내셔널지오그래픽").strip(),
                "style_code": parse_style(html),
                "name": name,
                "color": color,
                "price": str(price).strip(),
                "currency": "KRW",
                "category": (gi.get("firstCategory") or gi.get("category") or "").strip(),
                "gender": gender_of(name, cat_code),
                "sizes": sizes,
                "origin": origin,
                "material": material,
                "mfg_date": mfg,
                "url": url,
            }
            rows.append(row)
            for c in HEADER:
                if row[c]:
                    filled[c] += 1
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  [warn] goods {gid} fail: {e}")
        if i % 20 == 0:
            print(f"  {i}/{len(sample)} done (fails={fails})")
        time.sleep(0.2)

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)

    print(f"\nWROTE {len(rows)} rows -> {OUT}")
    print(f"est_total (discoverable lower bound) = {est_total}; fails = {fails}")
    print("filled per column:")
    for c in HEADER:
        print(f"  {c:11s}: {filled[c]}/{len(rows)}")
    print(f"gosi text rows: material={material_text} mfg_date={mfg_text} origin(text)={origin_text}")


if __name__ == "__main__":
    main()
