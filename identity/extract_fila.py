#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FULL product extractor for FILA Korea (Shopify) — 전수(全數).

List:   /products.json?limit=250&page={p}   (loop until empty page; no cap)
        -> variants[] price/sku/option, vendor=brand, product_type=category,
           color & gender from tags.
Detail: /products/{handle}              (DOM 상품고시정보)
        -> 제품소재/제조국/제조년월 from <h3 class="sub__content-heading"> pairs.

Resumable: rewrites the full CSV after each page (checkpoint). A re-run reads the
existing CSV, keeps every style_code already present (with its gosi), and only
fetches detail pages for new products. Dedup by style_code. Hard cap 5000.
"""
import os, re, csv, json, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://www.fila.co.kr"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
OUT = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_fila.csv"
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
PAGE_CAP = 500     # safety only; real stop is an empty page
ROW_CAP = 5000     # task: stop at 5000 if catalog is huge
WORKERS = 8

GENDER_MAP = [("남녀공용", "공용"), ("남성", "남성"), ("여성", "여성"),
              ("공용", "공용"), ("키즈", "키즈"), ("아동", "키즈"),
              ("주니어", "키즈"), ("유아", "키즈")]


def fetch(url, decode=True, retries=2):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(enc, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            return data.decode("utf-8", errors="replace") if decode else data
        except Exception as e:
            last = e
            time.sleep(0.6 * (attempt + 1))
    raise last


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def gosi(html):
    """Map all 상품고시 heading->value pairs by substring (keys vary by category)."""
    out = {"material": "", "origin": "", "mfg_date": ""}
    pairs = re.findall(
        r'sub__content-heading">(.*?)</h3>\s*<div class="sub__content-text">\s*<p>(.*?)</p>',
        html, re.S)
    for k, v in pairs:
        k = clean(k)
        v = clean(v)
        if "소재" in k and not out["material"]:
            out["material"] = v
        elif "제조국" in k and not out["origin"]:
            out["origin"] = v
        elif "제조년월" in k and not out["mfg_date"]:
            out["mfg_date"] = v
    return out


def color_of(tags):
    for t in tags:
        if t.lower().startswith("color:"):
            parts = t.split(":")
            if len(parts) >= 2 and parts[1].strip():
                return parts[1].strip()
    return ""


def gender_of(tags):
    tagset = set(tags)
    for token, g in GENDER_MAP:
        if token in tagset:
            return g
    return ""


def sizes_of(p):
    size_vals = []
    for opt in p.get("options", []):
        if opt.get("name", "").strip().lower() in ("size", "사이즈"):
            size_vals = [str(x).strip() for x in opt.get("values", [])]
            break
    if not size_vals:
        seen, size_vals = set(), []
        for v in p["variants"]:
            o = (v.get("option1") or "").strip()
            if o and o not in seen:
                seen.add(o)
                size_vals.append(o)
    return "|".join(size_vals)


def price_of(p):
    vals = []
    for v in p["variants"]:
        try:
            vals.append(int(float(v["price"])))
        except (TypeError, ValueError):
            pass
    return str(min(vals)) if vals else ""


def base_row(p):
    handle = p["handle"]
    return {
        "source": "fila",
        "brand": p.get("vendor") or "FILA",
        "style_code": handle.upper(),
        "name": p.get("title", "").strip(),
        "color": color_of(p.get("tags", [])),
        "price": price_of(p),
        "currency": "KRW",
        "category": p.get("product_type", "").strip(),
        "gender": gender_of(p.get("tags", [])),
        "sizes": sizes_of(p),
        "origin": "", "material": "", "mfg_date": "",
        "url": f"{BASE}/products/{handle}",
    }


def enrich(p):
    row = base_row(p)
    try:
        row.update(gosi(fetch(row["url"])))
    except Exception as e:
        print(f"  [warn] detail fail {p['handle']}: {e}")
    return row


def load_existing():
    rows = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8-sig", newline="") as f:
                rd = csv.DictReader(f)
                if rd.fieldnames and rd.fieldnames[:len(HEADER)] == HEADER:
                    for r in rd:
                        sc = (r.get("style_code") or "").strip()
                        if sc:
                            rows[sc] = {c: r.get(c, "") for c in HEADER}
        except Exception as e:
            print(f"[warn] could not read existing CSV ({e}); starting fresh")
    return rows


def write_csv(rows_by_sc):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows_by_sc.values())
    os.replace(tmp, OUT)


def main():
    rows_by_sc = load_existing()
    print(f"resume: {len(rows_by_sc)} rows already present")
    page = 1
    pages_seen = 0
    capped = False
    while page <= PAGE_CAP:
        try:
            data = json.loads(fetch(BASE + f"/products.json?limit=250&page={page}"))
        except Exception as e:
            print(f"page {page}: fetch error {e}; stopping")
            break
        prods = data.get("products", [])
        if not prods:
            print(f"page {page}: empty -> end of catalog")
            break
        pages_seen = page
        new_prods = [p for p in prods if p["handle"].upper() not in rows_by_sc]
        if new_prods:
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                futs = [ex.submit(enrich, p) for p in new_prods]
                for fut in as_completed(futs):
                    r = fut.result()
                    rows_by_sc[r["style_code"]] = r
        write_csv(rows_by_sc)  # checkpoint per page
        print(f"page {page}: listed {len(prods)}, +{len(new_prods)} new, total {len(rows_by_sc)}")
        if len(rows_by_sc) >= ROW_CAP:
            capped = True
            print(f"row cap {ROW_CAP} reached -> stop")
            break
        page += 1

    if capped and len(rows_by_sc) > ROW_CAP:
        rows_by_sc = dict(list(rows_by_sc.items())[:ROW_CAP])
        write_csv(rows_by_sc)

    write_csv(rows_by_sc)
    filled = {c: sum(1 for r in rows_by_sc.values() if r.get(c)) for c in HEADER}
    gosi_hits = sum(1 for r in rows_by_sc.values()
                    if r.get("material") or r.get("origin") or r.get("mfg_date"))
    print(f"\nDONE total {len(rows_by_sc)} rows, pages_seen={pages_seen}, capped={capped}")
    print("filled per column:")
    for c in HEADER:
        print(f"  {c:11s}: {filled[c]}/{len(rows_by_sc)}")
    print(f"gosi rows: {gosi_hits}/{len(rows_by_sc)}")


if __name__ == "__main__":
    main()
