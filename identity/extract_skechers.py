#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server-side product sample extractor for SKECHERS Korea (self-built, Thymeleaf SSR).

List:   /category/{path}            -> collect /product/{SKU} links (color variants
                                       are sibling SKUs ...061/062/063).
Detail: /product/{SKU}              (hook: dom + inline JS objects)
        - var productInfo = { name, model, cateLNm/cateMNm/cateSNm } (\\uXXXX escaped)
        - data-sku-data="[...]"     -> salePrice/retailPrice/upc/quantity (entity-escaped JSON)
        - lowestPrice = {'price':{'amount':...}}
        - <div class="selector-color" ... data-friendly-name / data-color-kor>
        - <label class="variation-size" typeName="250"> -> sizes
        - <dl><dt class="tag-key">소재|원산지|...</dt><dd class="tag-value">...</dd></dl> 고시
"""
import re, csv, json, time, ssl, html as ihtml
import urllib.request, urllib.parse

BASE = "https://www.skecherskorea.co.kr"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
OUT = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_skechers.csv"
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
SAMPLE = 120
PER_CAT = 12  # cap per listing category for a diverse (shoes/apparel/kids/acc) sample

# (category path, url-path gender fallback)
SEED_CATS = [
    ("/category/men/shoes/lifestyle", "남성"),
    ("/category/women/shoes/lifestyle", "여성"),
    ("/category/men/shoes/performs", "남성"),
    ("/category/women/shoes/performs", "여성"),
    ("/category/men/shoes/sandle", "남성"),
    ("/category/women/shoes/boots", "여성"),
    ("/category/women/tech/gowalk", "여성"),
    ("/category/kids/tech", "키즈"),
    ("/category/apparel/men/hoodie&sweatshirts", "남성"),
    ("/category/apparel/women/hoodie&sweatshirts", "여성"),
    ("/category/apparel/men/halft-shirts", "남성"),
    ("/category/apparel/women/longt-shirts", "여성"),
    ("/category/kids/apparel/halft-shirts", "키즈"),
    ("/category/men/acc/bag", "남성"),
    ("/category/women/acc/bag", "여성"),
]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def fetch(url):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(enc, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return r.read().decode("utf-8", errors="replace")


def clean(s):
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def dec(s):
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def detect_gender(blob, fallback):
    b = blob.lower()
    if any(k in b for k in ["키즈", "아동", "주니어", "유아", "남아", "여아", "kid", "junior"]):
        return "키즈"
    if any(k in b for k in ["공용", "남녀공용", "unisex"]):
        return "공용"
    if any(k in b for k in ["여성", "women", "woman"]):
        return "여성"
    if any(k in b for k in ["남성", "men", "man"]):
        return "남성"
    return fallback


def parse_detail(raw, sku, gender_fallback):
    o = {h: "" for h in HEADER}
    o["source"] = "skechers"
    o["brand"] = "스케쳐스"
    o["currency"] = "KRW"
    o["style_code"] = sku
    o["url"] = BASE + "/product/" + sku

    m = re.search(r"var productInfo = \{(.*?)\};", raw, re.S)
    pi = m.group(1) if m else ""

    def f(key):
        mm = re.search(key + r"\s*:\s*'([^']*)'", pi)
        return mm.group(1) if mm else ""

    o["name"] = dec(f("name")) or dec(f("nameEn"))
    mo = re.search(r"model\s*:\s*'([^']*)'", pi)
    if mo and mo.group(1):
        o["style_code"] = mo.group(1)
    cate_l = dec(f("cateLNm"))
    cate_m = dec(f("cateMNm"))
    cate_s = dec(f("cateSNm"))
    o["category"] = " > ".join([x for x in [cate_m, cate_s] if x]) or cate_l
    o["gender"] = detect_gender(" ".join([cate_l, cate_m, o["name"]]), gender_fallback)

    # color: friendly-name primary, color-kor fallback (never leave 기타/blank if kor exists)
    fm = re.search(r'selector-color.*?data-friendly-name="([^"]*)"', raw, re.S)
    km = re.search(r'data-color-kor="([^"]*)"', raw)
    fn = fm.group(1).strip() if fm else ""
    kor = km.group(1).strip() if km else ""
    o["color"] = fn if fn and fn != "기타" else (kor or fn)

    # sizes
    sizes = re.findall(r'class="variation-size[^"]*"[^>]*typeName="([^"]*)"', raw)
    o["sizes"] = "|".join(dict.fromkeys([s.strip() for s in sizes if s.strip()]))

    # price from data-sku-data, fallback lowestPrice
    sd = re.search(r'data-sku-data="(\[.*?\])"\s', raw, re.S)
    if sd:
        try:
            arr = json.loads(ihtml.unescape(sd.group(1)))
            pr = [int(x["salePrice"]) for x in arr if x.get("salePrice")]
            if pr:
                o["price"] = str(min(pr))
        except Exception:
            pass
    if not o["price"]:
        lp = re.search(r"lowestPrice = \{'price':\{'amount':(\d+)", raw)
        if lp:
            o["price"] = lp.group(1)

    # 고시: 소재 / 원산지 / 제조년월
    for k, v in re.findall(
            r'<dt class="tag-key">(.*?)</dt>\s*<dd class="tag-value">(.*?)</dd>', raw, re.S):
        k = clean(k)
        v = clean(v)
        if k == "소재" and not o["material"]:
            o["material"] = v
        elif k == "원산지" and not o["origin"]:
            o["origin"] = v
        elif ("제조년월" in k or "제조연월" in k) and not o["mfg_date"]:
            o["mfg_date"] = v
    return o


def main():
    # 1) collect SKUs from listing pages, remember url-path gender hint per SKU
    sku_gender = {}
    cat_totals = {}
    for path, ghint in SEED_CATS:
        if len(sku_gender) >= SAMPLE:
            break
        try:
            html = fetch(BASE + path)
        except Exception as e:
            print("LIST ERR", path, repr(e))
            continue
        tot = re.search(r'totalCount["\']?\s*[:=]\s*["\']?(\d+)', html)
        cat_totals[path] = int(tot.group(1)) if tot else None
        # real SKUs are alphanumeric (e.g. SL0MPCGY191); skip numeric-only IDs
        found = [s for s in dict.fromkeys(re.findall(r'/product/([A-Za-z0-9]+)', html))
                 if not s.isdigit() and re.search(r'[A-Za-z]', s)]
        added = 0
        for s in found:
            if s not in sku_gender:
                sku_gender[s] = ghint
                added += 1
                if added >= PER_CAT:
                    break
        print(f"LIST {path}: +{len(found)} (total {cat_totals[path]}) -> cumulative {len(sku_gender)}")
        time.sleep(0.3)

    skus = list(sku_gender.keys())[:SAMPLE]
    print(f"\nFetching {len(skus)} detail pages...")

    rows = []
    seen = set()
    fails = 0
    for i, sku in enumerate(skus, 1):
        try:
            raw = fetch(BASE + "/product/" + sku)
        except Exception as e:
            fails += 1
            print(f"  [{i}] DETAIL ERR {sku}: {e!r}")
            continue
        row = parse_detail(raw, sku, sku_gender[sku])
        key = row["style_code"]
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
        if i % 20 == 0:
            print(f"  [{i}] {key} {row['name'][:24]} {row['price']} {row['color']} sz={row['sizes'][:20]}")
        time.sleep(0.3)

    # 3) write CSV
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # 4) verify
    filled = {h: sum(1 for r in rows if r.get(h)) for h in HEADER}
    print(f"\nWROTE {len(rows)} rows -> {OUT}")
    print("fills:", filled)
    print("fails:", fails)
    print("cat_totals:", cat_totals)
    return rows, filled, cat_totals, fails


if __name__ == "__main__":
    main()
