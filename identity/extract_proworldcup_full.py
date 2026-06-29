#!/usr/bin/env python3
"""FULL (전수) product extractor for 프로월드컵 official mall.

Domain: 프로월드컵.com  (punycode xn--hy1bj0er3q41hqzh.com), platform = Cafe24, hook = jsonld.

Diff vs extract_proworldcup.py (the 120-sample adapter):
  - NO product cap, NO per-category cap, NO single-page sampling.
  - Every category is paginated page=1.. until the category-specific product-link
    regex returns 0 (empty / past-last page).
  - Completeness: the 5 top menus MAN/WOMAN/KIDS/ACC/SHOES partition the catalog
    (prdCount 415+351+203+56+293 = 1318 == the published estimate). We crawl them
    for the master set, AND crawl the curated leaf subcats first so each product
    gets a precise category label + gender (leaf wins via setdefault).
  - Incremental checkpoint: each product's rows are appended to a .work.csv and the
    product_no is appended to a .done.txt, so a crash/kill resumes where it stopped.
  - Output identical schema/granularity to the sample file: one row per (product,color),
    sizes |joined. style_code = cafe24 product code = item_code[:8]; dedup is at the
    product level (one detail fetch per product_no == per style_code).
  - Safety cap: if emitted rows exceed MAX_ROWS (5000) we stop and record it.
"""
import urllib.request, urllib.parse, re, json, csv, time, os, sys

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BASE = "https://xn--hy1bj0er3q41hqzh.com"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs", "extract_brand_proworldcup.csv")
WORK = os.path.join(HERE, "outputs", "_proworldcup_full.work.csv")
DONE = os.path.join(HERE, "outputs", "_proworldcup_full.done.txt")
META = os.path.join(HERE, "outputs", "_proworldcup_full.meta.json")
STAT = os.path.join(HERE, "outputs", "_proworldcup_full.stat.json")
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
MAX_ROWS = 5000
MAX_PAGES = 300   # hard safety stop per category (real max ~7)

# leaf subcats (cate_no, precise label, gender) -- crawled FIRST so labels win
SUBCATS = [
    (520, "워킹화", "공용"), (521, "트레킹화", "공용"), (523, "스니커즈", "공용"),
    (558, "런닝화", "공용"), (662, "스포츠화", "공용"), (522, "샌들/슬리퍼", "공용"),
    (524, "방한슈즈", "공용"),
    (542, "운동화", "아동"), (589, "축구화/풋살화", "아동"), (515, "부츠/장화", "아동"),
    (541, "샌들/슬리퍼", "아동"), (595, "실내화", "아동"), (780, "방한슈즈", "아동"),
    (500, "상의", "남성"), (493, "하의", "남성"), (502, "트레이닝셋업", "남성"),
    (503, "상의", "여성"), (625, "하의", "여성"), (504, "트레이닝셋업", "여성"),
    (507, "아우터", "여성"),
    (511, "가방", "공용"), (512, "모자", "공용"), (513, "양말", "공용"),
    (588, "인솔", "공용"), (673, "계절용품", "공용"), (854, "레저용품", "공용"),
]
# top menus for completeness (coarse label/gender; only fills products not in leaves)
TOPCATS = [
    (489, "의류", "남성"), (490, "의류", "여성"), (491, "키즈", "아동"),
    (498, "액세서리", "공용"), (517, "신발", "공용"),
    # extra brand-line / cross menus, harmless (union by product_no)
    (677, "BAROIN", "공용"), (624, "아우터", "공용"), (598, "아울렛", "공용"), (42, "마스크", "공용"),
]
MODEL_RE = re.compile(r"[A-Z]{1,4}\d{2,4}-\d{2,4}-[A-Z0-9]+")


def get(url, retries=2):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
            return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(0.8 * (attempt + 1))
    raise last


def list_pnos(cate):
    """Paginate cate fully; return (ordered unique product_no list, pages_fetched)."""
    out, seen, page = [], set(), 1
    while page <= MAX_PAGES:
        try:
            h = get(f"{BASE}/product/list.html?cate_no={cate}&page={page}")
        except Exception as e:
            print(f"  list err cate {cate} p{page}: {e}", file=sys.stderr)
            break
        found = re.findall(r"/product/[^\"']*?/(\d+)/category/" + str(cate) + r"/display", h)
        uniq = [p for p in dict.fromkeys(found) if p not in seen]
        if not uniq:
            break          # empty / past last page
        for p in uniq:
            seen.add(p); out.append(p)
        page += 1
        time.sleep(0.15)
    return out, page - 1


def collect():
    order, label, gender, seen = [], {}, {}, set()
    pages_total = 0
    plan = [("leaf", c, l, g) for c, l, g in SUBCATS] + [("top", c, l, g) for c, l, g in TOPCATS]
    for kind, cate, lab, gen in plan:
        pnos, pages = list_pnos(cate)
        pages_total += pages
        added = 0
        for p in pnos:
            if p not in seen:
                seen.add(p); order.append(p); added += 1
            label.setdefault(p, lab); gender.setdefault(p, gen)
        print(f"  [{kind}] cate {cate:>4} {lab}/{gen}: {len(pnos)} pnos in {pages} pages "
              f"(+{added} new, total {len(order)})", file=sys.stderr)
    return order, label, gender, pages_total


def split_suffix(suf):
    suf = suf.strip()
    if not suf:
        return "", ""
    if "-" in suf:
        c, s = suf.rsplit("-", 1)
        return c.strip(), s.strip()
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

    groups, color_order = {}, []
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
            "source": "proworldcup", "brand": brand, "style_code": style_code,
            "name": name, "color": color, "price": price, "currency": currency,
            "category": label, "gender": gender, "sizes": "|".join(g["sizes"]),
            "origin": "", "material": "", "mfg_date": "",   # 고시 image-only on this mall
            "url": url,
        })
    return rows


def main():
    # ---- phase 1: collect (resume from META if present) ----
    if os.path.exists(META):
        with open(META, encoding="utf-8") as f:
            m = json.load(f)
        order, label, gender = m["order"], m["label"], m["gender"]
        pages_total = m.get("pages_total", 0)
        print(f"[1] loaded {len(order)} product ids from META", file=sys.stderr)
    else:
        print("[1] collecting product ids (all categories, all pages)...", file=sys.stderr)
        order, label, gender, pages_total = collect()
        with open(META, "w", encoding="utf-8") as f:
            json.dump({"order": order, "label": label, "gender": gender,
                       "pages_total": pages_total}, f, ensure_ascii=False)
        print(f"    -> {len(order)} unique products across {pages_total} list pages", file=sys.stderr)

    # ---- phase 2: detail fetch with resume + incremental append ----
    done = set()
    if os.path.exists(DONE):
        with open(DONE, encoding="utf-8") as f:
            done = set(x.strip() for x in f if x.strip())
    rows_emitted = 0
    if os.path.exists(WORK):
        with open(WORK, encoding="utf-8-sig") as f:
            rows_emitted = sum(1 for _ in f) - 1  # minus header
        rows_emitted = max(rows_emitted, 0)

    new_file = not os.path.exists(WORK)
    wf = open(WORK, "a", newline="", encoding="utf-8-sig")
    w = csv.DictWriter(wf, fieldnames=HEADER)
    if new_file:
        w.writeheader()

    capped = False
    todo = [p for p in order if p not in done]
    print(f"[2] detail fetch: {len(todo)} to do, {len(done)} already done, "
          f"{rows_emitted} rows on disk", file=sys.stderr)
    df = open(DONE, "a", encoding="utf-8")
    for i, pid in enumerate(todo, 1):
        try:
            prows = parse_detail(pid, label.get(pid, ""), gender.get(pid, ""))
        except Exception as e:
            print(f"    pid {pid} ERR {e}", file=sys.stderr)
            prows = []
        for r in prows:
            w.writerow(r); rows_emitted += 1
        df.write(pid + "\n")
        df.flush(); wf.flush()
        done.add(pid)
        if i % 50 == 0:
            print(f"    [{i}/{len(todo)}] products done, rows={rows_emitted}", file=sys.stderr)
        if rows_emitted >= MAX_ROWS:
            capped = True
            print(f"    !! MAX_ROWS {MAX_ROWS} reached, stopping early", file=sys.stderr)
            break
        time.sleep(0.18)
    wf.close(); df.close()

    # ---- phase 3: final dedup + canonical write ----
    seen_keys, final = set(), []
    with open(WORK, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = (r["style_code"], r["color"], r["sizes"])
            if key in seen_keys:
                continue
            seen_keys.add(key); final.append(r)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        ww = csv.DictWriter(f, fieldnames=HEADER)
        ww.writeheader(); ww.writerows(final)

    n_products = len(set(r["style_code"] for r in final))
    filled = {c: sum(1 for r in final if str(r.get(c, "")).strip()) for c in HEADER}
    stat = {"after_rows": len(final), "products": n_products, "list_pages": pages_total,
            "ids_collected": len(order), "details_done": len(done), "capped": capped,
            "filled": filled}
    with open(STAT, "w", encoding="utf-8") as f:
        json.dump(stat, f, ensure_ascii=False, indent=2)
    print(f"[3] wrote {len(final)} rows ({n_products} products) -> {OUT}", file=sys.stderr)
    print("STAT", json.dumps(stat, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
