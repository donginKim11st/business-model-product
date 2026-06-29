#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exhaustive (전수) product extractor for 미즈노 official mall (kor.mizuno.com, cafe24).

Removes the 5-page / 120-sample cap of mizuno_extract.py (women's-shoes only) and
crawls EVERY product-bearing category x EVERY page to the last page, with a global
product_no dedup set so each product detail is fetched exactly once.

List:   /product/list.html?cate_no=N&page=P   (paginate per category until the page
        repeats/empties; cafe24 clamps page>last to the last page, so we stop when
        the id set repeats). Categories are discovered dynamically from the on-page
        category nav (containers with 0 products are skipped naturally).
Detail: /product/detail.html?product_no=ID    -> JSON-LD Product/ProductGroup
        (name/color/sizes/price/currency) + body '자체상품코드' (style_code).

gender:   from membership in MEN(154)/WOMEN(157)/JUNIOR(1230) category sets +
          (W)/(M) name fallback.
category: product-type bucket (신발/의류/가방/양말/모자/장갑/보호대/용품) from the
          titles of every category a product appears in (highest-priority wins).
gosi:     origin/material/mfg_date are NOT in server HTML (image/OCR-only). They are
          preserved by style_code from gosi_mizuno.csv + the existing CSV; blank for
          newly-discovered products (matches the crocs_full precedent).

Resumable: enumeration cached to _mizuno_enum.json; per-product checkpoint appended
to _mizuno_ckpt.jsonl. Dedup by style_code (fallback product_no). Hard cap 5000.
"""
import csv
import gzip
import html as ihtml
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE = "https://kor.mizuno.com"
ROOT = "/Users/a1101417/Work/business-model/identity/outputs"
OUT_CSV = os.path.join(ROOT, "extract_brand_mizuno.csv")
ENUM_CACHE = os.path.join(ROOT, "_mizuno_enum.json")
CKPT = os.path.join(ROOT, "_mizuno_ckpt.jsonl")
GOSI_CSV = os.path.join(ROOT, "gosi_mizuno.csv")

HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
SEED_CATE = 54          # widest category; only used to harvest the full cate_no list
HARD_CAP = 5000
WORKERS = 10            # detail-page concurrency
ENUM_WORKERS = 8        # category-enumeration concurrency
SALE_KEYS = ("OUTLET", "EVENT", "특가", "USED", "GIFT", "HOT SUMMER", "LOOKBOOK")

MEN_CATE, WOMEN_CATE, JUNIOR_CATE = "154", "157", "1230"

# product-type bucket -> title keywords (checked in this priority order)
BUCKETS = [
    ("신발", ["신발", "샌들", "슬라이드", "트레일", "멀티인도어"]),
    ("가방", ["가방"]),
    ("양말", ["양말"]),
    ("모자", ["모자"]),
    ("장갑", ["장갑"]),
    ("보호대", ["보호대", "밴드"]),
    ("의류", ["의류", "상의", "하의", "반팔", "긴팔", "슬리브리스", "쇼츠", "롱팬츠",
            "맨투맨", "후드", "바람막이", "자켓", "베스트", "패딩", "다운", "타이즈",
            "피스테", "아노락", "스커트", "티셔츠", "피케"]),
    ("용품", ["용품", "인솔", "골프공", "ETC", "보호"]),
]


def fetch(url, timeout=30, retries=3):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(0.8 + attempt)
    raise last


def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def cate_title(h):
    m = re.search(r"<title>(.*?)</title>", h, re.S)
    if not m:
        return ""
    t = ihtml.unescape(m.group(1))
    return t.replace("SPORTS", "").replace("MIZUNO", "").strip(" -|")


# ---------------------------------------------------------------- enumeration
def list_page(cate, page):
    h = fetch(f"{BASE}/product/list.html?cate_no={cate}&page={page}")
    ids = []
    seen = set()
    for n in re.findall(r"product_no=(\d+)", h):
        if n not in seen:
            seen.add(n)
            ids.append(n)
    mc = re.search(r"prdCount[^0-9]*([\d,]+)", h)
    cnt = int(mc.group(1).replace(",", "")) if mc else None
    return ids, cnt, h


def discover_cate_nos():
    _, _, h = list_page(SEED_CATE, 1)
    return sorted({int(x) for x in re.findall(r"cate_no=(\d+)", h)})


def crawl_category(cate):
    """Crawl one category to its last page. Returns
    (cstr, title, prdcount, ordered_ids, pages)."""
    cstr = str(cate)
    page = 1
    prev = None
    ordered_ids = []
    seen = set()
    prdcount = None
    title = ""
    while True:
        try:
            ids, cnt, h = list_page(cate, page)
        except Exception as e:  # noqa
            print(f"  [warn] cate {cate} page {page}: {e}")
            break
        if page == 1:
            prdcount = cnt
            title = cate_title(h)
        if not ids:
            break
        if prev is not None and ids == prev:   # cafe24 clamp -> stop
            break
        for i in ids:
            if i not in seen:
                seen.add(i)
                ordered_ids.append(i)
        prev = ids
        page += 1
    return cstr, title, prdcount, ordered_ids, page - 1


def enumerate_catalog():
    """Walk every category (in parallel) to the last page. Returns:
       order        : [(product_no, first_cate_title)] first-seen order
       member       : {product_no: set(cate_no_str)}
       cat_titles   : {cate_no_str: product-type-title}
       per_cat      : {cate_no_str: {'title','prdCount','collected'}}
       pages_fetched: int
    """
    cate_nos = discover_cate_nos()
    print(f"discovered {len(cate_nos)} cate_no anchors")
    results = {}
    pages_fetched = 0
    with ThreadPoolExecutor(max_workers=ENUM_WORKERS) as ex:
        futs = {ex.submit(crawl_category, c): c for c in cate_nos}
        for fut in as_completed(futs):
            cstr, title, prdcount, ids, pages = fut.result()
            pages_fetched += pages
            if ids:
                results[cstr] = (title, prdcount, ids, pages)
                print(f"  cate {cstr:>4} [{title[:18]:<18}] pages={pages} "
                      f"collected={len(ids)} prdCount={prdcount}")

    # deterministic merge: main-catalog categories before sale/outlet/event,
    # then by cate_no ascending -> first-seen winner is the main-catalog entry.
    def sort_key(cstr):
        title = results[cstr][0]
        is_sale = any(k in title.upper() for k in SALE_KEYS)
        return (1 if is_sale else 0, int(cstr))

    order, seen = [], set()
    member = {}
    cat_titles = {}
    per_cat = {}
    for cstr in sorted(results, key=sort_key):
        title, prdcount, ids, _ = results[cstr]
        cat_titles[cstr] = title
        per_cat[cstr] = {"title": title, "prdCount": prdcount, "collected": len(ids)}
        for i in ids:
            member.setdefault(i, set()).add(cstr)
            if i not in seen:
                seen.add(i)
                order.append((i, title))
    return order, member, cat_titles, per_cat, pages_fetched


# ------------------------------------------------------------------- detail
def parse_detail(pno):
    url = f"{BASE}/product/detail.html?product_no={pno}"
    h = fetch(url)
    rec = {k: "" for k in HEADER}
    rec["source"] = "mizuno"
    rec["brand"] = "미즈노"
    rec["url"] = url
    rec["currency"] = "KRW"
    # JSON-LD
    name = ""
    colors, sizes, prices = [], [], []
    for blk in re.findall(
            r'application/ld\+json["\'][^>]*>(.*?)</script>', h, re.S):
        try:
            d = json.loads(blk.strip())
        except Exception:
            continue
        if not (isinstance(d, dict) and d.get("@type") in ("Product", "ProductGroup")):
            continue
        name = (d.get("name") or "").strip()
        offers = d.get("offers") or []
        if isinstance(offers, dict):
            offers = [offers]
        for o in offers:
            if not isinstance(o, dict):
                continue
            rest = (o.get("name") or "").replace(name, "").strip()
            if "-" in rest:
                c, sz = rest.rsplit("-", 1)
                c, sz = c.strip(), sz.strip()
            else:
                c, sz = rest, ""
            if c and c not in colors:
                colors.append(c)
            if sz and sz not in sizes:
                sizes.append(sz)
            if o.get("price") not in (None, ""):
                try:
                    prices.append(float(o["price"]))
                except (TypeError, ValueError):
                    pass
            if o.get("priceCurrency"):
                rec["currency"] = o["priceCurrency"]
        break
    rec["name"] = name
    rec["color"] = "|".join(colors)
    rec["sizes"] = "|".join(sizes)
    if prices:
        p = min(prices)
        rec["price"] = str(int(p)) if p == int(p) else str(p)
    # style_code: 자체상품코드 > 모델
    txt = clean(h)
    sc = re.search(r"자체상품코드\s+([A-Za-z0-9][A-Za-z0-9\-]{4,})", txt)
    if not sc:
        sc = re.search(r"모델\s+([A-Za-z0-9][A-Za-z0-9\-]{4,})", txt)
    rec["style_code"] = sc.group(1).strip() if sc else ""
    rec["_pid"] = str(pno)
    return rec


# ------------------------------------------------------------------- gosi
def load_gosi():
    g = {}
    # 1) dedicated OCR gosi file
    if os.path.exists(GOSI_CSV):
        with open(GOSI_CSV, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                sc = (r.get("style_code") or "").strip()
                if sc and (r.get("origin") or r.get("material") or r.get("mfg_date")):
                    g[sc] = {"origin": r.get("origin", "").strip(),
                             "material": r.get("material", "").strip(),
                             "mfg_date": r.get("mfg_date", "").strip()}
    # 2) existing CSV (don't lose anything already curated)
    if os.path.exists(OUT_CSV):
        try:
            with open(OUT_CSV, encoding="utf-8-sig", newline="") as f:
                rd = csv.DictReader(f)
                if rd.fieldnames and rd.fieldnames[:len(HEADER)] == HEADER:
                    for r in rd:
                        sc = (r.get("style_code") or "").strip()
                        if sc and sc not in g and (
                                r.get("origin") or r.get("material") or r.get("mfg_date")):
                            g[sc] = {"origin": r.get("origin", "").strip(),
                                     "material": r.get("material", "").strip(),
                                     "mfg_date": r.get("mfg_date", "").strip()}
        except Exception:
            pass
    return g


# ----------------------------------------------------------- gender/category
def gender_of(pno, member):
    cats = member.get(pno, set())
    if JUNIOR_CATE in cats:
        return "키즈"
    return ""


def gender_full(rec, pno, member):
    cats = member.get(pno, set())
    if JUNIOR_CATE in cats:
        return "키즈"
    nm = rec.get("name", "")
    if re.search(r"\(\s*W\s*\)", nm):
        return "여성"
    if re.search(r"\(\s*M\s*\)", nm):
        return "남성"
    w, m = WOMEN_CATE in cats, MEN_CATE in cats
    if w and not m:
        return "여성"
    if m and not w:
        return "남성"
    if m and w:
        return "공용"
    return ""


def category_of(pno, member, cat_titles):
    titles = [cat_titles.get(c, "") for c in member.get(pno, set())]
    for bucket, kws in BUCKETS:
        for t in titles:
            if any(k in t for k in kws):
                return bucket
    return "기타"


# ------------------------------------------------------------------ checkpt
def load_ckpt():
    done = {}
    if os.path.exists(CKPT):
        with open(CKPT, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("_pid"):
                    done[r["_pid"]] = r
    return done


def write_csv(rows):
    tmp = OUT_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, OUT_CSV)


def main():
    os.makedirs(ROOT, exist_ok=True)

    # ---- 1. enumerate (cached) ----
    if os.path.exists(ENUM_CACHE):
        with open(ENUM_CACHE, encoding="utf-8") as f:
            E = json.load(f)
        order = [tuple(x) for x in E["order"]]
        member = {k: set(v) for k, v in E["member"].items()}
        cat_titles = E["cat_titles"]
        per_cat = E["per_cat"]
        pages_fetched = E["pages_fetched"]
        print(f"loaded enum cache: {len(order)} unique product_no, "
              f"{pages_fetched} list pages")
    else:
        print("Enumerating all categories x pages ...")
        order, member, cat_titles, per_cat, pages_fetched = enumerate_catalog()
        with open(ENUM_CACHE, "w", encoding="utf-8") as f:
            json.dump({"order": order,
                       "member": {k: sorted(v) for k, v in member.items()},
                       "cat_titles": cat_titles, "per_cat": per_cat,
                       "pages_fetched": pages_fetched}, f, ensure_ascii=False)
        print(f"ENUM: {len(order)} unique product_no across {pages_fetched} list pages")

    # ---- 전수 gate: per-category prdCount == collected ----
    mismatch = {}
    for c, info in per_cat.items():
        want, got = info.get("prdCount"), info.get("collected")
        if isinstance(want, int) and want != got:
            mismatch[c] = {"title": info["title"], "prdCount": want, "collected": got}
    if mismatch:
        print(f"WARN per-category count mismatch in {len(mismatch)} categories:")
        for c, mm in mismatch.items():
            print(f"   cate {c} [{mm['title']}] prdCount={mm['prdCount']} collected={mm['collected']}")
    else:
        print("OK per-category collected == prdCount for ALL categories")

    # ---- 2. detail crawl (resumable, concurrent) ----
    done = load_ckpt()
    print(f"checkpoint: {len(done)} products already parsed")
    todo = [(pno, lbl) for pno, lbl in order if pno not in done]
    capped = len(done) >= HARD_CAP
    if capped:
        print(f"HARD_CAP {HARD_CAP} already reached in checkpoint")
        todo = []

    lock = threading.Lock()
    ckf = open(CKPT, "a", encoding="utf-8")
    parsed = len(done)
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(parse_detail, pno): pno for pno, _ in todo}
            for fut in as_completed(futs):
                pno = futs[fut]
                try:
                    rec = fut.result()
                except Exception as e:  # noqa
                    print(f"  ERR pid={pno}: {type(e).__name__} {e}")
                    continue
                with lock:
                    done[pno] = rec
                    ckf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    ckf.flush()
                    parsed += 1
                    if parsed % 50 == 0:
                        print(f"  parsed {parsed}/{len(order)} "
                              f"(last {rec['style_code']} {rec['name'][:24]})")
                    if parsed >= HARD_CAP:
                        capped = True
                        print(f"HARD_CAP {HARD_CAP} reached; cancelling rest")
                        for f2 in futs:
                            f2.cancel()
                        break
    finally:
        ckf.close()

    # ---- 3. assemble rows in first-seen order; assign gender/category/gosi ----
    gosi = load_gosi()
    print(f"gosi available for {len(gosi)} style_codes")
    rows = []
    by_key = {}
    sc_to_pids = {}
    for pno, _ in order:
        rec = done.get(pno)
        if not rec or not rec.get("name"):
            continue
        rec["gender"] = gender_full(rec, pno, member)
        rec["category"] = category_of(pno, member, cat_titles)
        sc = rec.get("style_code", "")
        if sc:
            sc_to_pids.setdefault(sc, set()).add(pno)
            g = gosi.get(sc)
            if g:
                rec["origin"] = g["origin"]
                rec["material"] = g["material"]
                rec["mfg_date"] = g["mfg_date"]
        key = sc if sc else ("__pid_" + pno)
        if key in by_key:
            continue
        by_key[key] = True
        rows.append({k: rec.get(k, "") for k in HEADER})
        if len(rows) >= HARD_CAP:
            break

    write_csv(rows)

    # ---- 4. audit ----
    filled = {c: sum(1 for r in rows if r.get(c)) for c in HEADER}
    cat_tot, cat_sc = {}, {}
    for r in rows:
        cat_tot[r["category"]] = cat_tot.get(r["category"], 0) + 1
        if r["style_code"]:
            cat_sc[r["category"]] = cat_sc.get(r["category"], 0) + 1
    collisions = {sc: sorted(p) for sc, p in sc_to_pids.items() if len(p) >= 2}

    print(f"\nWROTE {len(rows)} rows -> {OUT_CSV}")
    print("FILLED", json.dumps(filled, ensure_ascii=False))
    print("STYLE_CODE_FILL_BY_CAT",
          json.dumps({k: f"{cat_sc.get(k,0)}/{cat_tot[k]}" for k in cat_tot},
                     ensure_ascii=False))
    print("GENDER", json.dumps(
        {g: sum(1 for r in rows if r["gender"] == g)
         for g in sorted({r["gender"] for r in rows})}, ensure_ascii=False))
    print(f"STYLE_CODE_COLLISIONS (>=2 distinct pids): {len(collisions)}")
    print(f"UNIQUE_PRODUCT_NO={len(order)}  PARSED={len(done)}  "
          f"DEDUPED_ROWS={len(rows)}  PAGES_FETCHED={pages_fetched}  "
          f"CAPPED={capped}  MISMATCH_CATS={len(mismatch)}")
    summary = {"after": len(rows), "unique_product_no": len(order),
               "parsed": len(done), "pages_fetched": pages_fetched,
               "capped": capped, "mismatch_cats": len(mismatch),
               "filled": filled}
    with open(os.path.join(ROOT, "_mizuno_full.stat.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
