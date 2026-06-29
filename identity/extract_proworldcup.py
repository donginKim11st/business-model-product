#!/usr/bin/env python3
"""Server-side product sample extractor for 프로월드컵 official mall.

Domain: 프로월드컵.com  (punycode xn--hy1bj0er3q41hqzh.com), platform = Cafe24, hook = jsonld.

List endpoint:  /product/list.html?cate_no={N}&page={p}
  - SEO product links: /product/{slug}/{product_no}/category/{cate}/display/{n}/
Detail (JSON-LD): /product/detail.html?product_no={id}
  - <script type="application/ld+json"> @type Product
      -> name (base), brand{name}, offers[]
      -> each offer: name = "{base} {color}-{size}", price (KRW), url(?item_code=Pxxxxxxxx....)
  - 상품정보제공고시 (소재/제조국/제조년월) is image-only on this mall -> gosi_status=image (blank cols)

style_code  = cafe24 product code = item_code[:8] (P + 7); fallback = model code in name; else PNO{no}.
Granularity = one row per (product, color) variant; sizes |joined within a color.
Output: outputs/extract_brand_proworldcup.csv  (utf-8-sig, fixed schema below)
"""
import urllib.request, urllib.parse, re, json, csv, time, os

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BASE = "https://xn--hy1bj0er3q41hqzh.com"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "extract_brand_proworldcup.csv")
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
CAP = 120          # cap on PRODUCTS (detail fetches), not output rows
PER_CAT = 6        # products sampled per subcategory

# (cate_no, category label, gender) chosen for product-type / gender diversity
SUBCATS = [
    # SHOES (517) — 공용
    (520, "워킹화", "공용"), (521, "트레킹화", "공용"), (523, "스니커즈", "공용"),
    (558, "런닝화", "공용"), (662, "스포츠화", "공용"), (522, "샌들/슬리퍼", "공용"),
    (524, "방한슈즈", "공용"),
    # KIDS (491) — 아동
    (542, "운동화", "아동"), (589, "축구화/풋살화", "아동"), (515, "부츠/장화", "아동"),
    (541, "샌들/슬리퍼", "아동"), (595, "실내화", "아동"), (780, "방한슈즈", "아동"),
    # MAN (489) — 남성
    (500, "상의", "남성"), (493, "하의", "남성"), (502, "트레이닝셋업", "남성"),
    # WOMAN (490) — 여성
    (503, "상의", "여성"), (625, "하의", "여성"), (504, "트레이닝셋업", "여성"),
    (507, "아우터", "여성"),
    # ACC (498) — 공용
    (511, "가방", "공용"), (512, "모자", "공용"), (513, "양말", "공용"),
    (588, "인솔", "공용"), (673, "계절용품", "공용"), (854, "레저용품", "공용"),
]
TOP_CATS = [489, 490, 491, 498, 517]
MODEL_RE = re.compile(r"[A-Z]{1,4}\d{2,4}-\d{2,4}-[A-Z0-9]+")


def get(url):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(u, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")


def est_total():
    total = 0
    for c in TOP_CATS:
        try:
            html = get(f"{BASE}/product/list.html?cate_no={c}&page=1")
            m = re.search(r"prdCount[^0-9]*(\d+)", html)
            n = int(m.group(1)) if m else 0
            total += n
            print(f"  prdCount cate {c}: {n}")
        except Exception as e:
            print(f"  est err cate {c}: {e}")
    return total


def collect():
    order, meta = [], {}
    for cate, label, gender in SUBCATS:
        try:
            html = get(f"{BASE}/product/list.html?cate_no={cate}&page=1")
        except Exception as e:
            print(f"  list err cate {cate}: {e}")
            continue
        pnos = re.findall(r"/product/[^\"']*?/(\d+)/category/" + str(cate) + r"/display", html)
        local = 0
        for pid in pnos:
            if pid in meta:
                continue
            meta[pid] = (label, gender)
            order.append(pid)
            local += 1
            if local >= PER_CAT:
                break
        print(f"  cate {cate:>4} {label}/{gender}: +{local} (total {len(order)})")
        if len(order) >= CAP:
            break
        time.sleep(0.15)
    return order[:CAP], meta


def split_suffix(suf):
    """offer-name suffix -> (color, size)."""
    suf = suf.strip()
    if not suf:
        return "", ""
    if "-" in suf:
        c, s = suf.rsplit("-", 1)
        return c.strip(), s.strip()
    # no dash: digits -> size, else color
    if re.fullmatch(r"\d+(\.\d+)?", suf):
        return "", suf
    return suf, ""


def parse_detail(pid, label, gender):
    html = get(f"{BASE}/product/detail.html?product_no={pid}")
    blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    d = None
    for b in blocks:
        try:
            obj = json.loads(b.strip())
        except Exception:
            continue
        if obj.get("@type") == "Product":
            d = obj
            break
    if d is None:
        return []
    name = (d.get("name") or "").strip()
    brand = d.get("brand")
    brand = brand.get("name") if isinstance(brand, dict) else brand
    brand = (brand or "프로월드컵").strip()
    offers = d.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]

    # style_code: cafe24 product code (item_code[:8]) > model code in name > PNO
    style_code = ""
    for o in offers:
        u = o.get("url") or ""
        if "item_code=" in u:
            code = u.split("item_code=")[-1].split("&")[0]
            if len(code) >= 8:
                style_code = code[:8]
                break
    if not style_code:
        m = MODEL_RE.search(name)
        style_code = m.group(0) if m else f"PNO{pid}"

    currency = (offers[0].get("priceCurrency") if offers else "") or "KRW"

    # group offers by color -> {sizes, prices}
    groups = {}
    color_order = []
    for o in offers:
        on = (o.get("name") or "").strip()
        suf = on[len(name):].strip() if on.startswith(name) else on
        color, size = split_suffix(suf)
        if color not in groups:
            groups[color] = {"sizes": [], "prices": []}
            color_order.append(color)
        g = groups[color]
        if size and size not in g["sizes"]:
            g["sizes"].append(size)
        pr = o.get("price")
        if pr not in (None, "", 0):
            try:
                g["prices"].append(int(float(pr)))
            except Exception:
                pass

    url = f"{BASE}/product/detail.html?product_no={pid}"
    rows = []
    for color in color_order:
        g = groups[color]
        price = min(g["prices"]) if g["prices"] else ""
        rows.append({
            "source": "proworldcup",
            "brand": brand,
            "style_code": style_code,
            "name": name,
            "color": color,
            "price": price,
            "currency": currency,
            "category": label,
            "gender": gender,
            "sizes": "|".join(g["sizes"]),
            "origin": "",       # 고시 image-only
            "material": "",     # 고시 image-only
            "mfg_date": "",     # 고시 image-only
            "url": url,
        })
    return rows


def main():
    print("[0] estimating catalog size (prdCount per top category)...")
    total = est_total()
    print(f"    est_total ~= {total}")

    print("[1] collecting product ids from subcategory lists...")
    ids, meta = collect()
    print(f"    -> {len(ids)} unique product samples (cap {CAP})")

    print("[2] fetching detail pages + parsing JSON-LD Product...")
    rows = []
    ok_products = 0
    for i, pid in enumerate(ids, 1):
        label, gender = meta[pid]
        try:
            prows = parse_detail(pid, label, gender)
        except Exception as e:
            print(f"    [{i}/{len(ids)}] pid {pid} ERR {e}")
            prows = []
        if prows:
            ok_products += 1
            rows.extend(prows)
            if i % 20 == 0 or i == len(ids):
                last = prows[0]
                print(f"    [{i}/{len(ids)}] products_ok={ok_products} rows={len(rows)} "
                      f"last={last['style_code']} {last['name'][:24]}")
        time.sleep(0.25)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    print(f"[3] wrote {len(rows)} rows ({ok_products} products) -> {OUT}")

    filled = {c: sum(1 for r in rows if str(r.get(c, "")).strip()) for c in HEADER}
    print("[4] fields filled:", filled)
    brands = {}
    for r in rows:
        brands[r["brand"]] = brands.get(r["brand"], 0) + 1
    print("    brands:", brands)
    return rows, filled, total, ok_products


if __name__ == "__main__":
    main()
