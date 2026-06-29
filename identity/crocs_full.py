#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full (전수) product extractor for Crocs Korea via Algolia index paging.

Removes the 120-sample cap / 5-page limit of extract_crocs.py and walks the
Algolia index (production_crocs_kr__products__ko_KR) to the last page, collecting
every master (style_code).

- List source: Algolia query endpoint (hitsPerPage/page) -> all nbHits masters.
- Row build:   reuses extract_crocs.base_row (category/gender/sizes/price-from-
               Algolia selling price, which matches the existing CSV semantics).
- gosi fields: origin/material/mfg_date are React-rendered client-side and are
               NOT in server HTML; preserved from outputs/gosi_crocs.csv for the
               masters that were previously browser-rendered.
- PDP ld+json: NOT used. crocs.co.kr now rejects urllib's TLS fingerprint
               (TLSV1_ALERT_PROTOCOL_VERSION) and its offers price is the LIST
               price (regression vs Algolia selling price).
- Resumable:   each page is appended to a progress file; rerun skips seen masters.
- Cap:         stops at 5000 rows (records in notes if hit).
"""
import csv, json, os, sys, time, urllib.request, urllib.parse

import extract_crocs as ec  # reuse base_row / algolia config

OUT = ec.OUT
HEADER = ec.HEADER
PROGRESS = "/Users/a1101417/Work/business-model/identity/outputs/_crocs_full_progress.csv"
GOSI = "/Users/a1101417/Work/business-model/identity/outputs/gosi_crocs.csv"
HPP = 100
HARD_CAP = 5000


def algolia_page(page, hits=HPP):
    payload = json.dumps({"params": f"query=&hitsPerPage={hits}&page={page}"})
    txt = ec.fetch_text(ec.ALGOLIA_URL, data=payload, headers={
        "X-Algolia-Application-Id": ec.ALGOLIA_APP,
        "X-Algolia-API-Key": ec.ALGOLIA_KEY,
        "Content-Type": "application/json",
    })
    return json.loads(txt)


def load_gosi():
    """style_code -> (origin, material, mfg_date) from browser-rendered gosi CSV."""
    g = {}
    if os.path.exists(GOSI):
        for r in csv.DictReader(open(GOSI, encoding="utf-8-sig")):
            sc = (r.get("style_code") or "").strip()
            if sc:
                g[sc] = (r.get("origin") or "", r.get("material") or "",
                         r.get("mfg_date") or "")
    return g


def load_progress():
    """Resume: return (rows_dict_by_master, max_page_done)."""
    rows, done_pages = {}, -1
    if os.path.exists(PROGRESS):
        for r in csv.DictReader(open(PROGRESS, encoding="utf-8-sig")):
            sc = r.get("style_code") or ""
            if sc:
                rows[sc] = {k: r.get(k, "") for k in HEADER}
            try:
                done_pages = max(done_pages, int(r.get("_page", -1)))
            except Exception:
                pass
    return rows, done_pages


def main():
    gosi = load_gosi()
    rows, last_done = load_progress()
    print(f"resume: {len(rows)} masters from progress, last page done = {last_done}")

    # progress file with an extra _page column for resumability
    pfields = HEADER + ["_page"]
    new_progress = not os.path.exists(PROGRESS)
    pf = open(PROGRESS, "a", newline="", encoding="utf-8-sig")
    pw = csv.DictWriter(pf, fieldnames=pfields)
    if new_progress:
        pw.writeheader()

    nb_hits = nb_pages = None
    page = last_done + 1
    capped = False
    while True:
        try:
            d = algolia_page(page)
        except Exception as e:
            print(f"  algolia page {page} ERR {e}", file=sys.stderr)
            time.sleep(1.0)
            try:
                d = algolia_page(page)
            except Exception as e2:
                print(f"  algolia page {page} RETRY ERR {e2}", file=sys.stderr)
                pf.close()
                return False, rows, page, nb_hits, nb_pages, capped
        if nb_hits is None:
            nb_hits = d.get("nbHits")
            nb_pages = d.get("nbPages")
            print(f"nbHits={nb_hits} nbPages={nb_pages}")
        hits = d.get("hits", [])
        if not hits:
            print(f"  page {page}: empty -> end")
            break
        added = 0
        for h in hits:
            m = str(h.get("master") or "")
            if not m or m in rows:
                continue
            r = ec.base_row(h)
            if not r["name"] or not r["url"]:
                continue
            o, mat, md = gosi.get(m, ("", "", ""))
            r["origin"], r["material"], r["mfg_date"] = o, mat, md
            rows[m] = r
            pw.writerow({**r, "_page": page})
            added += 1
            if len(rows) >= HARD_CAP:
                capped = True
                break
        pf.flush()
        print(f"  page {page}: +{added} (total {len(rows)})")
        if capped:
            print(f"  HARD_CAP {HARD_CAP} reached -> stop")
            break
        page += 1
        if nb_pages is not None and page >= nb_pages:
            # fetch the final page index range guard: nbPages is count, last idx=nb_pages-1
            if page > (nb_pages - 1):
                # already processed last page (page incremented past it)
                break
        time.sleep(0.25)

    pf.close()

    # final dedup-by-style_code write (utf-8-sig, exact header)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows.values():
            w.writerow({k: r.get(k, "") for k in HEADER})
    print(f"WROTE {len(rows)} rows -> {OUT}")
    return True, rows, page, nb_hits, nb_pages, capped


if __name__ == "__main__":
    ok, rows, last_page, nb_hits, nb_pages, capped = main()
    n_pages = (last_page + 1)
    print(json.dumps({
        "ok": ok, "after": len(rows), "pages_walked": n_pages,
        "nbHits": nb_hits, "nbPages": nb_pages, "capped": capped,
    }, ensure_ascii=False))
