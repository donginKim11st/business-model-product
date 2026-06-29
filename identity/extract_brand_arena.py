#!/usr/bin/env python3
"""EXHAUSTIVE (전수) product extractor for 아레나 official mall (arena.co.kr, cafe24, hook=jsonld).

Crawls EVERY category and EVERY page (no sample cap), dedups by style_code, writes full catalog.

List endpoint:   /product/list.html?cate_no={N}&page={p}
  - products in a listing are marked  id="anchorBoxId_{product_no}"  (<=60 / page; default display)
  - empty page (0 anchorBoxId) == past the last page -> stop paginating that category
  - <meta name="description"> = leaf category name ; <meta name="keywords"> ends with the
    section root (WOMEN/MEN/KIDS/EQUIPMENT/SALE) -> gender
Detail (JSON-LD):  /product/detail.html?product_no={id}
  - <script type="application/ld+json"> @type Product -> name, brand, offers[] (price, currency, size names)
  - DOM 상품정보제공고시 (div2=label/div3=value) -> 품번/색상/치수/제품소재/제조국/제조년월
Output: outputs/extract_brand_arena.csv  (schema fixed below, utf-8-sig)

Resumable: Phase-1 id map cached to outputs/_arena_ids.json; Phase-2 appends per product and
skips product_no already present in the CSV. Final pass re-reads the CSV and dedups by style_code.
"""
import urllib.request, urllib.parse, re, json, csv, time, os, sys, html as htmlmod, threading
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BASE = "https://www.arena.co.kr"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs", "extract_brand_arena.csv")
IDMAP = os.path.join(HERE, "outputs", "_arena_ids.json")
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
MAX_ROWS = 5000          # hard ceiling; stop & note if exceeded
PAGE_HARD_CAP = 120      # safety: never loop forever on one category
WORKERS = 10             # concurrent fetches (server is per-connection throttled)
_wlock = threading.Lock()

SECTION_GENDER = {"WOMEN": "여성", "MEN": "남성", "KIDS": "아동", "EQUIPMENT": "공용", "SALE": ""}


def get(url):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(u, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")


def page_ids(html):
    # ordered-unique product_no from listing anchors
    return list(dict.fromkeys(re.findall(r'anchorBoxId_(\d+)', html)))


def meta_of(html):
    d = re.search(r'<meta name="description" content="([^"]*)"', html)
    k = re.search(r'<meta name="keywords" content="([^"]*)"', html)
    desc = htmlmod.unescape(d.group(1)).strip() if d else ""
    kw = htmlmod.unescape(k.group(1)).strip() if k else ""
    section = kw.split(",")[-1].strip() if kw else ""
    return desc, section


def gender_for(desc, section):
    g = SECTION_GENDER.get(section, "")
    if g:
        return g
    # SALE or unknown section: infer from leaf name
    if "아동" in desc or "아동" in section:
        return "아동"
    if desc.startswith("여") or "여성" in desc:
        return "여성"
    if desc.startswith("남") or "남성" in desc:
        return "남성"
    return "공용"


def discover_categories():
    nav = get(f"{BASE}/product/list.html?cate_no=24&page=1")
    cats = sorted(set(int(x) for x in re.findall(r'cate_no=(\d+)', nav)))
    return cats


def _fetch_page1(c):
    try:
        h = get(f"{BASE}/product/list.html?cate_no={c}&page=1")
    except Exception as e:
        print(f"    pre cate {c}: ERR {e}", flush=True)
        return c, None
    desc, section = meta_of(h)
    ids = page_ids(h)
    return c, (desc, section, ids)


def _paginate_rest(c, page1_ids):
    """Fetch page 2.. until an empty page OR a page that adds no new ids (clamp guard)."""
    extra = []
    seen = set(page1_ids)
    pg = 2
    while True:
        if pg > PAGE_HARD_CAP:
            print(f"    WARN cate {c} hit page cap {PAGE_HARD_CAP}", flush=True)
            break
        try:
            h = get(f"{BASE}/product/list.html?cate_no={c}&page={pg}")
        except Exception as e:
            print(f"    cate {c} page {pg}: ERR {e}", flush=True)
            break
        ids = page_ids(h)
        if not ids:
            break
        if set(ids) <= seen:  # clamp/repeat: page adds nothing new -> stop
            break
        seen |= set(ids)
        extra.append((pg, ids))
        pg += 1
    return c, extra


def collect_ids():
    """Phase 1: classify every category by page-1 meta (parallel), paginate full categories
    (parallel), then assign product_no -> meta in tier order so the most specific category wins.
    Tier 1 = specific leaf categories, Tier 2 = section roots (safety net), Tier 3 = SALE (last)."""
    cats = discover_categories()
    print(f"[1] discovered {len(cats)} categories from nav", flush=True)

    pre = {}  # cate -> (desc, section, page1_ids)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for c, res in ex.map(_fetch_page1, cats):
            if res is not None:
                pre[c] = res
    print(f"[1] page-1 meta fetched for {len(pre)} categories", flush=True)

    # paginate categories whose page1 is full (>=60 -> more pages likely)
    full = [c for c, (_, _, ids) in pre.items() if len(ids) >= 60]
    all_pages = {c: {1: ids} for c, (_, _, ids) in pre.items()}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_paginate_rest, c, pre[c][2]) for c in full]
        for fut in futs:
            c, extra = fut.result()
            for pg, ids in extra:
                all_pages[c][pg] = ids
    print(f"[1] paginated {len(full)} multi-page categories", flush=True)

    # flatten per-category id lists (ordered-unique across pages) + record desc/section
    cat_all_ids = {}
    cat_desc, cat_section, cat_pages = {}, {}, {}
    for c, (desc, section, _) in pre.items():
        pages = all_pages[c]
        ids = list(dict.fromkeys(pid for pg in sorted(pages) for pid in pages[pg]))
        cat_all_ids[c] = ids
        cat_desc[c], cat_section[c] = desc, section
        cat_pages[c] = len(pages)

    meta = assign_labels(cat_all_ids, cat_desc, cat_section)
    print(f"[1] assigned labels -> {len(meta)} unique products", flush=True)

    json.dump({"meta": meta, "cat_pages": cat_pages,
               "cat_all_ids": {str(k): v for k, v in cat_all_ids.items()},
               "cat_desc": {str(k): v for k, v in cat_desc.items()},
               "cat_section": {str(k): v for k, v in cat_section.items()}},
              open(IDMAP, "w"), ensure_ascii=False)
    return meta, cat_pages


STD_SECTIONS = {"WOMEN", "MEN", "KIDS", "EQUIPMENT", "SALE"}


def cat_quality(desc, section):
    """3=specific leaf w/ gender section (best), 2=collection/theme (named, no gender),
    1=section root page, 0=generic/empty aggregator (never used for labeling if avoidable)."""
    if (not desc) or ("공식홈" in desc) or ("Beyond The Water" in desc) or section in ("아레나코리아", ""):
        return 0
    if section in STD_SECTIONS:
        return 1 if desc in STD_SECTIONS else 3
    return 2  # COLLECTIONS / LEMON / other theme sections: keep the name, gender unknown


def assign_labels(cat_all_ids, cat_desc, cat_section):
    """Pick, for each product, the highest-quality category it appears in (ties -> smallest
    cate_no). Returns product_no -> [category_label, gender]. Order-independent."""
    best_q = {}
    meta = {}
    for c in sorted(cat_all_ids, key=int):
        desc, section = cat_desc[c], cat_section[c]
        q = cat_quality(desc, section)
        label = desc if q > 0 else "기타"
        gender = gender_for(desc, section) if q > 0 else "공용"
        for pid in cat_all_ids[c]:
            if pid not in best_q or q > best_q[pid]:
                best_q[pid] = q
                meta[pid] = [label, gender]
    return meta


def parse_detail(pid, label, gender):
    html = get(f"{BASE}/product/detail.html?product_no={pid}")
    blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    if not blocks:
        return None, "no_jsonld"
    try:
        d = json.loads(blocks[0].strip())
    except Exception:
        return None, "bad_jsonld"
    if d.get("@type") not in ("Product", "ProductGroup"):
        return None, "not_product"
    name = (d.get("name") or "").strip()
    brand = d.get("brand")
    brand = brand.get("name") if isinstance(brand, dict) else brand
    if brand and "아레나" not in brand:
        return None, "brand_mismatch"
    offers = d.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    prices = [o.get("price") for o in offers if o.get("price")]
    price = min(prices) if prices else ""
    currency = (offers[0].get("priceCurrency") if offers else "") or "KRW"

    pairs = re.findall(r'class="line_div div2">(.*?)</div>\s*<div class="line_div div3">(.*?)</div>', html, re.S)
    info = {re.sub(r"\s+", " ", k).strip(): re.sub(r"\s+", " ", v).strip() for k, v in pairs}

    style_code = info.get("상품코드(품번)", "")
    if not style_code:
        m = re.search(r'/arena_new/\d+/([A-Z0-9]+)/', html)
        style_code = m.group(1) if m else ""
    color = info.get("색상", "")
    material = info.get("제품소재", "")
    origin = info.get("제조국", "")
    mfg_date = info.get("제조년월", "")

    sizes = ""
    chisu = info.get("치수", "")
    if chisu:
        sizes = "|".join(s.strip() for s in chisu.split(",") if s.strip())
    elif len(offers) > 1:
        toks = []
        for o in offers:
            on = (o.get("name") or "").strip()
            tok = on[len(name):].strip() if on.startswith(name) else (on.split()[-1] if on else "")
            if tok and tok not in toks:
                toks.append(tok)
        sizes = "|".join(toks)

    return {
        "source": "arena", "brand": "아레나", "style_code": style_code, "name": name,
        "color": color, "price": price, "currency": currency, "category": label,
        "gender": gender, "sizes": sizes, "origin": origin, "material": material,
        "mfg_date": mfg_date, "url": f"{BASE}/product/detail.html?product_no={pid}",
    }, "ok"


def done_product_nos():
    """product_no already written to the CSV (for resume)."""
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                m = re.search(r'product_no=(\d+)', row.get("url", ""))
                if m:
                    done.add(m.group(1))
    return done


def append_row(row):
    """Thread-safe append; writes header+BOM only when the file does not yet exist."""
    with _wlock:
        new = not os.path.exists(OUT)
        with open(OUT, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=HEADER)
            if new:
                w.writeheader()
            w.writerow(row)


def final_dedup():
    """Re-read whole CSV, dedup by style_code (fallback url), rewrite with one header+BOM."""
    rows = []
    with open(OUT, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    seen = set()
    out = []
    for r in rows:
        key = r.get("style_code") or ("URL:" + r.get("url", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in HEADER})
    return len(rows), len(out)


def main():
    if os.path.exists(IDMAP) and "--recollect" not in sys.argv:
        cache = json.load(open(IDMAP, encoding="utf-8"))
        meta = {k: v for k, v in cache["meta"].items()}
        cat_pages = cache.get("cat_pages", {})
        print(f"[1] loaded cached id map: {len(meta)} products, "
              f"{sum(cat_pages.values())} list pages", flush=True)
    else:
        meta, cat_pages = collect_ids()

    total_pages = sum(cat_pages.values())
    all_ids = list(meta.keys())
    capped = False
    if len(all_ids) > MAX_ROWS:
        print(f"[!] {len(all_ids)} products > {MAX_ROWS} cap; truncating queue", flush=True)
        all_ids = all_ids[:MAX_ROWS]
        capped = True

    done = done_product_nos()
    todo = [pid for pid in all_ids if pid not in done]
    print(f"[2] {len(all_ids)} unique products; {len(done)} already in CSV; "
          f"detail-fetching {len(todo)} with {WORKERS} workers...", flush=True)

    drops = {"no_jsonld": 0, "bad_jsonld": 0, "not_product": 0, "brand_mismatch": 0, "err": 0}
    counters = {"ok": 0, "n": 0}

    def work(pid):
        label, gender = meta[pid]
        try:
            row, reason = parse_detail(pid, label, gender)
        except Exception as e:
            row, reason = None, "err"
            print(f"    pid {pid} ERR {e}", flush=True)
        with _wlock:
            counters["n"] += 1
            n = counters["n"]
        if row:
            append_row(row)
            with _wlock:
                counters["ok"] += 1
                ok = counters["ok"]
            if ok % 50 == 0:
                print(f"    [{n}/{len(todo)}] written ok={ok} last={row['style_code']} {row['name'][:22]}", flush=True)
        else:
            with _wlock:
                drops[reason] = drops.get(reason, 0) + 1
        time.sleep(0.05)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(work, todo))
    ok = counters["ok"]

    before_dedup, after_dedup = final_dedup()
    print(f"[3] CSV rows before dedup={before_dedup} after style_code dedup={after_dedup}", flush=True)
    print(f"[4] list_pages={total_pages} unique_products={len(meta)} "
          f"fetched_ok={ok} drops={drops} capped={capped}", flush=True)
    # machine-readable summary line for the runner
    print("RESULT_JSON " + json.dumps({
        "after": after_dedup, "list_pages": total_pages,
        "unique_products": len(meta), "drops": drops, "capped": capped,
        "categories": len(cat_pages),
    }), flush=True)


if __name__ == "__main__":
    main()
