#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server-side product sample extractor for THE NORTH FACE Korea (자체/서버렌더).

Site: https://www.thenorthfacekorea.co.kr  (topick commerce platform, SSR static HTML)

List:   /category/n/{gender}/{cat}/...   -> /product/{styleCode} links (grid is SSR).
        NOTE a global "추천 상품" carousel repeats the same 1-2 codes on every page;
        global dedup + per-product breadcrumb gender make this harmless.
Detail: /product/{styleCode}             (hook: DOM / inline JS scalars)
        - inline JS vars: _name, _sku, _price, _retailPrice, _avail,
          _catName/_parentName/_gpName, _gpUrl (gender token), _url
        - DOM attrs: data-color (colorways), data-friendly-name (sizes), og:title (color)
        - 고시: <dt class="tag-key">제품 소재|제조국|제조연월</dt><dd class="tag-value">...</dd>
        ld+json is JS-injected -> absent in static HTML, so parse DOM/JS vars.
"""
import re, csv, json, time, codecs, urllib.request, urllib.parse

BASE = "https://www.thenorthfacekorea.co.kr"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
OUT = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_northface.csv"
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
SAMPLE = 120
PER_SEED = 20

SEEDS = [
    ("men/jacket-vest", "https://www.thenorthfacekorea.co.kr/category/n/men/jacket-vest"),
    ("men/top", "https://www.thenorthfacekorea.co.kr/category/n/men/top"),
    ("women/jacket-vest", "https://www.thenorthfacekorea.co.kr/category/n/women/jacket-vest"),
    ("women/top", "https://www.thenorthfacekorea.co.kr/category/n/women/top"),
    ("kids/top", "https://www.thenorthfacekorea.co.kr/category/n/kids/top"),
    ("shoes", "https://www.thenorthfacekorea.co.kr/category/n/shoes"),
    ("equipment", "https://www.thenorthfacekorea.co.kr/category/n/equipment"),
]

PLACEHOLDERS = {"상단표시", "탭영역 표시", "세탁 및 취급주의사항 탭영역 표시",
                "별도표기", "별도 표기", "상품상세참조", "상품 상세 참조"}
SIZE_WHITELIST = {"FREE", "F", "ONESIZE", "ONE SIZE", "XS", "S", "M", "L",
                  "XL", "XXL", "XXXL", "2XL", "3XL", "4XL"}


def fetch(url):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(enc, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", errors="replace")


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def deesc(s):
    """Decode \\uXXXX / \\/ / \\' inside an inline JS string literal, safely."""
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    s = s.replace("\\/", "/").replace("\\'", "'").replace('\\"', '"')
    return s.strip()


def jsstr(h, name):
    m = re.search(name + r"\s*=\s*'((?:[^'\\]|\\.)*)'", h)
    return deesc(m.group(1)) if m else ""


def jsnum(h, name):
    m = re.search(name + r"\s*=\s*([0-9]+)", h)
    return m.group(1) if m else ""


def gosi(h):
    """Map 고시 dt/dd pairs by substring; skip placeholder values."""
    out = {"material": "", "origin": "", "mfg_date": ""}
    pairs = re.findall(
        r'<dt class="tag-key[^"]*">(.*?)</dt>\s*<dd class="tag-value[^"]*">(.*?)</dd>',
        h, re.S)
    for k, v in pairs:
        k, v = clean(k), clean(v)
        if not v or v in PLACEHOLDERS:
            continue
        if "소재" in k and not out["material"]:
            out["material"] = v
        elif "제조국" in k and not out["origin"]:
            out["origin"] = v
        elif ("제조연월" in k or "제조년월" in k) and not out["mfg_date"]:
            out["mfg_date"] = v
    return out


def sizes_of(h, colors):
    """data-friendly-name minus colorways, kept only if size-like."""
    out = []
    for fn in re.findall(r'data-friendly-name="([^"]+)"', h):
        fn = fn.strip()
        if not fn or fn in colors or fn in out:
            continue
        upper = fn.upper()
        if any(ch.isdigit() for ch in fn) or upper in SIZE_WHITELIST:
            out.append(fn)
    return out


def color_of(h, name):
    """og:title = name + ' ' + COLOR; fall back to first data-color."""
    m = re.search(r'og:title"\s+content="([^"]+)"', h)
    title = m.group(1).strip() if m else ""
    if title and name and title.upper().startswith(name.upper()):
        c = title[len(name):].strip()
        if c:
            return c
    cols = re.findall(r'data-color="([^"]+)"', h)
    return cols[0].strip() if cols else ""


GENDER = [("/men", "남성"), ("/women", "여성"), ("/kids", "키즈")]


def gender_of(gp_url, gp_name, name=""):
    if gp_name in ("남성", "여성"):
        return gp_name
    if gp_name in ("키즈", "아동", "주니어"):
        return "키즈"
    for tok, g in GENDER:
        if tok in gp_url:
            return g
    # whitelabel/equipment fallback -> 공용, but honour unambiguous W'S/M'S name tags
    n = name.upper()
    if re.match(r"W'?S\b", n):
        return "여성"
    if re.match(r"M'?S\b", n):
        return "남성"
    return "공용"


def parse_detail(code):
    url = BASE + "/product/" + code
    h = fetch(url)
    name = jsstr(h, "_name")
    if not name:
        kor = re.findall(r'data-kor-name="([^"]+)"', h)
        name = kor[0] if kor else ""
    if not name:
        return None  # discontinued/stub page (~2KB, no product data)
    colors = set(re.findall(r'data-color="([^"]+)"', h))
    g = gosi(h)
    row = {
        "source": "northface",
        "brand": "노스페이스",
        "style_code": jsstr(h, "_sku") or code,
        "name": name,
        "color": color_of(h, name),
        "price": jsnum(h, "_price") or jsnum(h, "_retailPrice"),
        "currency": "KRW",
        "category": jsstr(h, "_catName") or jsstr(h, "_parentName"),
        "gender": gender_of(jsstr(h, "_gpUrl"), jsstr(h, "_gpName"), name),
        "sizes": "|".join(sizes_of(h, colors)),
        "origin": g["origin"],
        "material": g["material"],
        "mfg_date": g["mfg_date"],
        "url": jsstr(h, "_url") or url,
    }
    return row


def collect_codes():
    codes, counts = [], {}
    for label, seed in SEEDS:
        try:
            h = fetch(seed)
        except Exception as e:
            print("  seed FAIL", label, e)
            continue
        m = re.search(r'(\d[\d,]*)\s*개', h)
        counts[label] = m.group(1) if m else "?"
        alnum = [c for c in dict.fromkeys(re.findall(r'/product/([A-Za-z0-9]+)', h))
                 if not c.isdigit()]
        taken = alnum[:PER_SEED]
        print("  seed", label, "grid alnum=%d count=%s take=%d" %
              (len(alnum), counts[label], len(taken)))
        for c in taken:
            if c not in codes:
                codes.append(c)
        time.sleep(0.3)
    return codes[:SAMPLE + 12], counts  # extra to backfill discontinued stubs


def main():
    print("collecting codes ...")
    codes, counts = collect_codes()
    print("unique candidate codes:", len(codes))
    rows, fails = [], 0
    for i, c in enumerate(codes, 1):
        try:
            r = parse_detail(c)
            if r:
                rows.append(r)
            else:
                print("  skip stub", c)
        except Exception as e:
            fails += 1
            print("  detail FAIL", c, type(e).__name__, e)
        if i % 20 == 0:
            print("  ...%d/%d (fails=%d)" % (i, len(codes), fails))
        if len(rows) >= SAMPLE:
            break
        time.sleep(0.35)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    filled = {k: sum(1 for r in rows if r[k]) for k in HEADER}
    print("WROTE", len(rows), "rows ->", OUT, "fails=", fails)
    print("seed counts:", counts)
    print("filled:", filled)
    # gosi status
    has_text = any(r["material"] or r["origin"] for r in rows)
    print("GOSI_STATUS:", "text" if has_text else "none")


if __name__ == "__main__":
    main()
