#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server-side product sample extractor for PUMA Korea (Demandware / SFCC).

Site:   https://kr.puma.com/kr/ko/
List:   /kr/ko/{category-path}?sz=120        (path-based category grid; Korean paths URL-encoded)
Detail: /kr/ko/pd/{slug}/{model_id}.html     (hook: JSON-LD Product + embedded data)

Hook = jsonld. Each PDP embeds:
  * <script application/ld+json> Product  -> name, sku(EAN), color, price(KRW), brand, model
  * "styleNumber":"<model>_<color>"       -> 품번 (style_code)
  * <li class="material-info-value">...    -> 소재 (고시 material, TEXT)
  * dwvar_<model>_size=<code> value/displayValue objects -> readable sizes
제조국/제조년월 are NOT present in server-side HTML/JSON (manufacturerInfo={},
pumaAccordion has no 제조/원산지) -> left blank, gosi_status="text" (소재 only).
"""
import re, csv, sys, time, json, html as H, urllib.request, urllib.parse

BASE = "https://kr.puma.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
OUT = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_puma.csv"
HEADER = ["source","brand","style_code","name","color","price","currency",
          "category","gender","sizes","origin","material","mfg_date","url"]

# (readable path, gender, category-label) -- gendered categories only (no mixed 신상품)
CATEGORIES = [
    ("/kr/ko/mens/shoes/sneakers", "남성", "신발/스니커즈"),
    ("/kr/ko/여성/신발-2",          "여성", "신발"),
    ("/kr/ko/남성/의류-1",          "남성", "의류"),
    ("/kr/ko/여성/의류-2",          "여성", "의류"),
    ("/kr/ko/남성/용품",            "남성", "용품"),
]
MAX_PAGES_SZ = 120     # grid size param per category
TARGET = 120           # sample cap


def fetch(url, timeout=40):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(enc, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def clean(s):
    s = re.sub(r"<br\s*/?>", " ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", H.unescape(s)).strip()


def first(patterns, text, flags=re.S):
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(1).strip()
    return ""


def parse_product(html, url, gender="", category=""):
    dh = H.unescape(html)
    # --- JSON-LD Product (parse as JSON to honour escaped quotes e.g. 7\" Shorts) ---
    ld = ""
    for blk in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        if "Product" in blk and "@type" in blk:
            ld = blk
            break
    name = sku = color = model = brand_ld = price = currency = ""
    try:
        d = json.loads(ld.strip())
        name = clean(d.get("name", ""))
        sku = str(d.get("sku", "") or "")
        color = clean(str(d.get("color", "") or ""))
        model = str(d.get("model", "") or "")
        b = d.get("brand")
        brand_ld = (b.get("name") if isinstance(b, dict) else b) or ""
        off = d.get("offers") or {}
        if isinstance(off, list):
            off = off[0] if off else {}
        price = str(off.get("price", "") or off.get("lowPrice", "") or "")
        currency = off.get("priceCurrency", "") or ""
    except Exception:
        pass
    # regex fallback (escaped-quote aware) if JSON parse missed anything
    if not name:
        name = clean(first([r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"'], ld))
    if not sku:
        sku = first([r'"sku"\s*:\s*"([^"]+)"'], ld)
    if not color:
        color = clean(first([r'"color"\s*:\s*"((?:[^"\\]|\\.)*)"'], ld))
    if not price:
        price = first([r'"price"\s*:\s*"?([0-9.]+)', r'"lowPrice"\s*:\s*"?([0-9.]+)'], ld)
    currency = currency or "KRW"
    # --- model id from URL (authoritative) ---
    mu = re.search(r"/(\d+)\.html", url)
    model = (mu.group(1) if mu else model) or model
    # --- styleNumber (품번 = model_color) ---
    style = first([r'"styleNumber"\s*:\s*"([0-9A-Za-z_]+)"'], dh) or model
    # --- sizes scoped to this model's SIZE attribute values ---
    # Each variation value object carries swatch URLs with dwvar_<model>_size=<code>.
    # A color swatch object also embeds the *default* size param, so we keep only
    # objects whose own "value" equals the size code in its URL (true size values).
    sizes = []
    szre = re.compile(r"dwvar_" + re.escape(model) + r"_size=([0-9A-Za-z]+)")
    for o in re.findall(r"\{[^{}]*dwvar_" + re.escape(model) + r"_size=[^{}]*\}", dh):
        mv = re.search(r'"value"\s*:\s*"([^"]+)"', o)
        if not mv:
            continue
        v = mv.group(1)
        if v not in set(szre.findall(o)):
            continue  # color swatch carrying a default size param -> skip
        md = re.search(r'"displayValue"\s*:\s*"([^"]+)"', o)
        disp = (md.group(1).strip() if md else v)
        if disp and disp not in sizes:
            sizes.append(disp)
    # --- 고시 material (TEXT) ---
    mats = re.findall(r'<li class="material-info-value">(.*?)</li>', dh, re.S)
    material = "; ".join(clean(m) for m in mats if clean(m))
    return {
        "source": "puma",
        "brand": "푸마",
        "style_code": style,
        "name": name,
        "color": color,
        "price": price,
        "currency": currency,
        "category": category,
        "gender": gender,
        "sizes": "|".join(sizes),
        "origin": "",          # not in server-side HTML/JSON (고시 image/asset only)
        "material": material,  # 소재 = text
        "mfg_date": "",        # not in server-side HTML/JSON
        "url": url,
    }


def cat_count(html):
    m = re.search(r"제품\s*([0-9,]+)\s*개", html)
    return int(m.group(1).replace(",", "")) if m else None


# ---------------- test mode ----------------
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "test":
    row = parse_product(open(sys.argv[2], encoding="utf-8", errors="replace").read(),
                        sys.argv[3] if len(sys.argv) > 3 else "url", "테스트", "테스트")
    for k in HEADER:
        print(f"{k:11}= {row[k]!r}")
    sys.exit(0)


# ---------------- crawl ----------------
def main():
    seen = set()
    counts = {}
    per_cat = []     # list of per-category ordered [(mid,url,gender,category), ...]
    for path, gender, category in CATEGORIES:
        try:
            html = fetch(f"{BASE}{path}?sz={MAX_PAGES_SZ}")
        except Exception as e:
            print(f"  [cat fail] {path}: {e}", file=sys.stderr)
            per_cat.append([])
            continue
        c = cat_count(html)
        counts[category + "/" + gender] = c
        bucket = []
        for href in re.findall(r'/kr/ko/pd/[^"\'\s>]+\.html', html):
            mu = re.search(r"/(\d+)\.html", href)
            if not mu:
                continue
            mid = mu.group(1)
            if mid in seen:
                continue
            seen.add(mid)
            bucket.append((mid, BASE + href, gender, category))
        per_cat.append(bucket)
        print(f"  [cat] {path} count={c} collected={len(bucket)}", file=sys.stderr)
        time.sleep(0.3)

    # round-robin across categories for gender/category diversity, cap at TARGET
    items = []
    idx = 0
    while len(items) < TARGET and any(idx < len(b) for b in per_cat):
        for b in per_cat:
            if idx < len(b):
                items.append(b[idx])
                if len(items) >= TARGET:
                    break
        idx += 1
    items = [(mid, (url, g, cat)) for (mid, url, g, cat) in items]

    rows, ok_n, fail_n = [], 0, 0
    for i, (mid, (url, gender, category)) in enumerate(items, 1):
        try:
            html = fetch(url)
            row = parse_product(html, url, gender, category)
            if row["name"]:
                rows.append(row)
                ok_n += 1
            else:
                fail_n += 1
                print(f"  [pdp empty] {mid}", file=sys.stderr)
        except Exception as e:
            fail_n += 1
            print(f"  [pdp fail] {mid}: {e}", file=sys.stderr)
        if i % 20 == 0:
            print(f"  ... {i}/{len(items)} ok={ok_n} fail={fail_n}", file=sys.stderr)
        time.sleep(0.3)

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)

    filled = {k: sum(1 for r in rows if r[k]) for k in HEADER}
    print(json.dumps({"written": len(rows), "ok": ok_n, "fail": fail_n,
                      "category_counts": counts, "filled": filled},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
