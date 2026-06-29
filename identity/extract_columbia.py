#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server-side product sample extractor for Columbia Korea (Styleship platform).
List:   /product/list.asp?cno={cno}&page={p}
Detail: /product/view.asp?pno={pno}   (hook: JSON-LD Product + DOM supplement)
"""
import re, csv, json, sys, time, urllib.request, urllib.parse

BASE = "https://www.columbiakorea.co.kr"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OUT = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_columbia.csv"
HEADER = ["source","brand","style_code","name","color","price","currency",
          "category","gender","sizes","origin","material","mfg_date","url"]

def fetch(url):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(enc, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

def first(patterns, html, flags=re.S):
    for p in patterns:
        m = re.search(p, html, flags)
        if m:
            return m.group(1).strip()
    return ""

def notice(html, key):
    """Read <dt>key</dt><dd>value</dd> from 상품고시정보."""
    m = re.search(r"<dt>\s*" + re.escape(key) + r"\s*</dt>\s*<dd>(.*?)</dd>", html, re.S)
    if not m:
        return ""
    val = re.sub(r"<[^>]+>", " ", m.group(1))
    val = re.sub(r"\s+", " ", val).strip()
    return val

def gender_from_name(name):
    for pref, g in (("남성","남성"),("여성","여성"),("남녀공용","공용"),
                    ("공용","공용"),("키즈","키즈"),("아동","키즈"),("주니어","키즈")):
        if name.startswith(pref) or name.startswith(pref+" "):
            return g
    if "키즈" in name or "아동" in name:
        return "키즈"
    return ""

def parse_product(html, url):
    # --- JSON-LD Product block (regex straight out of text; avoids /*..*/ json hazard) ---
    ld = ""
    for blk in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html, re.S|re.I):
        if '"@type"' in blk and "Product" in blk:
            ld = blk
            break
    name = first([r'"name"\s*:\s*"([^"]+)"'], ld)
    sku  = first([r'"sku"\s*:\s*"([^"]+)"'], ld)
    price = first([r'"lowPrice"\s*:\s*"?([0-9.]+)', r'"price"\s*:\s*"?([0-9.]+)'], ld)
    currency = first([r'"priceCurrency"\s*:\s*"([A-Z]{3})"'], ld)
    # --- JS-var fallbacks (present on every PDP) ---
    if not name:  name = first([r"_NB_PD\s*=\s*'([^']*)'"], html)
    if not sku:   sku  = first([r"_NB_PC\s*=\s*'([^']*)'"], html)
    if not price: price = first([r"_NB_AMT\s*=\s*'([0-9]+)'"], html)
    if not currency: currency = "KRW"
    category = first([r"_NB_CT\s*=\s*'([^']*)'"], html)
    # --- color: active swatch / option-color label ---
    color = first([
        r'option-category _color.*?<p class="txt">\s*([^<]+?)\s*<',
        r'<li class="on">\s*<a[^>]*data-color-name="([^"]+)"',
        r'data-color-name="([^"]+)"',
    ], html)
    color = re.sub(r"\s+", " ", color).strip()
    # --- sizes: data-size values inside _size block ---
    sizes = ""
    ms = re.search(r"option-category _size(.*?)</div>\s*</div>", html, re.S)
    seg = ms.group(1) if ms else html
    sz = re.findall(r'data-size="([^"]+)"', seg)
    if sz:
        seen=[]
        for s in sz:
            if s not in seen: seen.append(s)
        sizes = "|".join(seen)
    # --- 상품고시정보 ---
    material = notice(html, "제품소재") or notice(html, "소재")
    origin   = notice(html, "제조국")
    mfg_date = notice(html, "제조년월")
    gender = gender_from_name(name)
    return {
        "source":"columbia","brand":"컬럼비아","style_code":sku,"name":name,
        "color":color,"price":price,"currency":currency,"category":category,
        "gender":gender,"sizes":sizes,"origin":origin,"material":material,
        "mfg_date":mfg_date,"url":url,
    }

# ---------------- test mode ----------------
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "test":
    html = open(sys.argv[2], encoding="utf-8", errors="replace").read()
    row = parse_product(html, "https://www.columbiakorea.co.kr/product/view.asp?pno=21204")
    for k in HEADER:
        print(f"{k:12}= {row[k]!r}")
    sys.exit(0)

# ---------------- crawl ----------------
CATEGORIES = [  # top-level parents for diversity
    (200,"아우터"),(250,"상의"),(300,"하의"),(350,"신발"),
    (400,"가방"),(420,"모자"),(450,"용품"),
]
MAX_PAGES = 5
TARGET = 120

def cat_count(cno):
    try:
        html = fetch(f"{BASE}/product/list.asp?cno={cno}")
        m = re.search(r"([0-9,]+)\s*개의?\s*상품", html)
        return int(m.group(1).replace(",","")) if m else None
    except Exception:
        return None

def collect_pnos():
    order=[]; seen=set()
    for cno,_ in CATEGORIES:
        for p in range(1, MAX_PAGES+1):
            if len(seen) >= TARGET: break
            try:
                html = fetch(f"{BASE}/product/list.asp?cno={cno}&page={p}")
            except Exception as e:
                print(f"  list cno={cno} p={p} ERR {e}", file=sys.stderr); break
            pnos = re.findall(r"view\.asp\?pno=(\d+)", html)
            new=[x for x in dict.fromkeys(pnos) if x not in seen]
            if not new:  # empty page -> next category
                break
            for x in new:
                if len(seen) >= TARGET: break
                seen.add(x); order.append(x)
            time.sleep(0.3)
        if len(seen) >= TARGET: break
    return order

def main():
    # est_total (approximate, excludes overlap & sale)
    est=0; parts=[]
    for cno,nm in CATEGORIES:
        c=cat_count(cno)
        if c: est+=c; parts.append(f"{nm}={c}")
        time.sleep(0.2)
    print("est_total ~", est, "(", ", ".join(parts), ")")
    pnos = collect_pnos()
    print("collected", len(pnos), "unique pno")
    rows=[]
    for i,pno in enumerate(pnos,1):
        url=f"{BASE}/product/view.asp?pno={pno}"
        try:
            html=fetch(url)
            row=parse_product(html, url)
            if row["name"]:
                rows.append(row)
        except Exception as e:
            print(f"  pno={pno} ERR {e}", file=sys.stderr)
        if i%20==0: print(f"  ...{i}/{len(pnos)}")
        time.sleep(0.25)
    with open(OUT,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows: w.writerow(r)
    print("WROTE", len(rows), "rows ->", OUT)

if __name__ == "__main__":
    main()
