#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full (전수) product extractor for Columbia Korea (Styleship platform).

Removes the 5-page / 120-sample caps of extract_columbia.py and walks every
category (list.asp?cno=N&page=P) to the empty page, unioning product ids (pno),
then fetches every PDP and dedups by style_code.

- Two overlapping taxonomies exist (200-series = product type, 700-series =
  gender/line, 900-series = special/sale).  Parent counts do not perfectly cover
  children (e.g. cno=300=104 vs children sum=109), so the ONLY exhaustive option
  is to crawl ALL cnos and union by pno; dedup absorbs the heavy overlap.
- gosi (origin/material/mfg_date) is served in server HTML via <dt>/<dd>, so
  plain urllib is sufficient (no browser).  Reuses parse_product from the sample
  adapter.
- Resumable: collected pnos are checkpointed per-cno to a JSON file; each parsed
  PDP row is appended+flushed to a progress CSV.  Re-run skips done cnos and
  already-fetched pnos.
- Cap: stops at 5000 rows (recorded in notes if hit).

Output: outputs/extract_brand_columbia.csv  (utf-8-sig, exact header), source=columbia.
"""
import csv, json, os, re, sys, time, urllib.request, urllib.parse

import extract_columbia as ec  # reuse fetch / parse_product / HEADER / BASE / OUT

BASE = ec.BASE
OUT = ec.OUT
HEADER = ec.HEADER
PNOS_CKPT = "/Users/a1101417/Work/business-model/identity/outputs/_columbia_pnos.json"
PROGRESS = "/Users/a1101417/Work/business-model/identity/outputs/_columbia_progress.csv"
HARD_CAP = 5000


def discover_cnos():
    """All category numbers from the site nav (stable site-wide)."""
    html = ec.fetch(f"{BASE}/product/list.asp?cno=200")
    return sorted(set(int(x) for x in re.findall(r"cno=(\d+)", html)))


def load_pnos_ckpt():
    if os.path.exists(PNOS_CKPT):
        d = json.load(open(PNOS_CKPT, encoding="utf-8"))
        return list(dict.fromkeys(d.get("pnos", []))), set(d.get("done_cnos", [])), d.get("page_count", 0)
    return [], set(), 0


def save_pnos_ckpt(pnos, done_cnos, page_count):
    tmp = PNOS_CKPT + ".tmp"
    json.dump({"pnos": pnos, "done_cnos": sorted(done_cnos), "page_count": page_count},
              open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, PNOS_CKPT)


def collect_pnos(cnos):
    """Walk every cno to the empty page; union pnos. Resumable per-cno."""
    pnos_order, done_cnos, page_count = load_pnos_ckpt()
    seen = set(pnos_order)
    print(f"collect resume: {len(seen)} pnos, {len(done_cnos)} cnos done, {page_count} pages walked")
    for cno in cnos:
        if cno in done_cnos:
            continue
        p = 1
        cat_added = 0
        while True:
            try:
                html = ec.fetch(f"{BASE}/product/list.asp?cno={cno}&page={p}")
            except Exception as e:
                print(f"  list cno={cno} p={p} ERR {e}", file=sys.stderr)
                time.sleep(1.0)
                try:
                    html = ec.fetch(f"{BASE}/product/list.asp?cno={cno}&page={p}")
                except Exception as e2:
                    print(f"  list cno={cno} p={p} RETRY ERR {e2}", file=sys.stderr)
                    break
            page_count += 1
            page_pnos = list(dict.fromkeys(re.findall(r"view\.asp\?pno=(\d+)", html)))
            if not page_pnos:  # empty page -> end of this category
                break
            for x in page_pnos:
                if x not in seen:
                    seen.add(x)
                    pnos_order.append(x)
                    cat_added += 1
            p += 1
            time.sleep(0.12)
        done_cnos.add(cno)
        save_pnos_ckpt(pnos_order, done_cnos, page_count)
        print(f"  cno={cno}: {p-1} pages, +{cat_added} new (union={len(pnos_order)})")
    return pnos_order, page_count


def load_progress():
    """Resume PDP fetch: rows keyed by pno already parsed."""
    by_pno = {}
    if os.path.exists(PROGRESS):
        for r in csv.DictReader(open(PROGRESS, encoding="utf-8-sig")):
            pno = (r.get("_pno") or "").strip()
            if pno:
                by_pno[pno] = {k: r.get(k, "") for k in HEADER}
    return by_pno


def main():
    cnos = discover_cnos()
    print(f"discovered {len(cnos)} cnos")
    pnos, page_count = collect_pnos(cnos)
    print(f"collected {len(pnos)} unique pnos across {page_count} list pages")

    by_pno = load_progress()
    print(f"PDP resume: {len(by_pno)} already parsed")

    pfields = HEADER + ["_pno"]
    new_progress = not os.path.exists(PROGRESS)
    pf = open(PROGRESS, "a", newline="", encoding="utf-8-sig")
    pw = csv.DictWriter(pf, fieldnames=pfields)
    if new_progress:
        pw.writeheader()

    capped = False
    todo = [x for x in pnos if x not in by_pno]
    for i, pno in enumerate(todo, 1):
        if len(by_pno) >= HARD_CAP:
            capped = True
            print(f"  HARD_CAP {HARD_CAP} reached -> stop")
            break
        url = f"{BASE}/product/view.asp?pno={pno}"
        try:
            html = ec.fetch(url)
            row = ec.parse_product(html, url)
        except Exception as e:
            print(f"  pno={pno} ERR {e}", file=sys.stderr)
            time.sleep(0.8)
            continue
        if not row.get("name"):
            # skip dead/empty PDP but record nothing
            continue
        by_pno[pno] = row
        pw.writerow({**row, "_pno": pno})
        pf.flush()
        if i % 50 == 0:
            print(f"  ...{i}/{len(todo)} parsed (total {len(by_pno)})")
        time.sleep(0.2)
    pf.close()

    # dedup by style_code (fallback to url for rows missing sku) and write final
    final = {}
    for row in by_pno.values():
        key = row.get("style_code") or row.get("url")
        if key and key not in final:
            final[key] = row
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for row in final.values():
            w.writerow({k: row.get(k, "") for k in HEADER})
    print(f"WROTE {len(final)} rows -> {OUT}")

    # discriminating checks
    n_pno_rows = len(by_pno)
    n_style = len(set(r.get("style_code") for r in by_pno.values() if r.get("style_code")))
    n_empty_sku = sum(1 for r in by_pno.values() if not r.get("style_code"))
    gosi_origin = sum(1 for r in by_pno.values() if r.get("origin"))
    gosi_mat = sum(1 for r in by_pno.values() if r.get("material"))
    print(json.dumps({
        "ok": True, "after": len(final), "pdp_rows": n_pno_rows,
        "unique_pnos": len(pnos), "unique_style_codes": n_style,
        "empty_style_code": n_empty_sku, "list_pages": page_count,
        "cnos": len(cnos), "origin_fill": gosi_origin, "material_fill": gosi_mat,
        "capped": capped,
    }, ensure_ascii=False))
    return True


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        raise
