#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FULL (전수) catalog crawler for 웨스트우드 (westwoodmall.co.kr, cafe24).

Reuses westwood_extract's http_get / parse_jsonld_product / normalize /
try_info_notice unchanged (the existing 120-row CSV proves they work).

Two resumable phases:
  Phase 1 (collect): walk all 7 listing categories, paginate via
      ?cate_no=N&page=P until a page adds zero NEW product_no *within that
      category* (cafe24 returns the last page for out-of-range page=N, and
      carousels inject repeats -> the within-category rule handles both).
      Real categories first so their labels win; NEW/BEST last only add
      genuinely-unique stragglers. Ids accumulate in _westwood_ids.json
      ({pid: cate_name}, insertion-ordered); finished cats in
      _westwood_cats_done.txt.
  Phase 2 (detail): fetch /product/detail.html?product_no={pid} for every
      unique pid, append one CSV row per product (flush each), marking
      attempted pids in _westwood_done.txt so a restart resumes. origin/
      material/mfg_date are left-joined from gosi_westwood.csv by style_code.
      Caps at 5000 rows.
"""
import os
import re
import csv
import sys
import json
import time

REPO = "/Users/a1101417/Work/business-model/identity"
sys.path.insert(0, REPO)
import westwood_extract as ww  # http_get, parse_jsonld_product, normalize, ...

BASE = ww.BASE
HEADER = ww.HEADER
OUT = os.path.join(REPO, "outputs", "extract_brand_westwood.csv")
IDS_FILE = os.path.join(REPO, "outputs", "_westwood_ids.json")
CATS_DONE = os.path.join(REPO, "outputs", "_westwood_cats_done.txt")
DONE_FILE = os.path.join(REPO, "outputs", "_westwood_done.txt")
STATUS = os.path.join(REPO, "outputs", "_westwood_status.json")
GOSI = os.path.join(REPO, "outputs", "gosi_westwood.csv")

MAX_ROWS = 5000
MAX_PAGES = 60  # safety cap; NEW (~22 pages) is the deepest

# real categories first (labels win on first-seen), NEW/BEST last for stragglers
CATS = [("231", "MEN"), ("244", "WOMEN"), ("268", "ACC&SHOES"),
        ("262", "NASA"), ("273", "OUTLET"), ("225", "NEW"), ("230", "BEST")]


def load_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def load_gosi():
    m = {}
    if not os.path.exists(GOSI):
        return m
    with open(GOSI, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sc = (r.get("style_code") or "").strip()
            if sc:
                m[sc] = {
                    "origin": (r.get("origin") or "").strip(),
                    "material": (r.get("material") or "").strip(),
                    "mfg_date": (r.get("mfg_date") or "").strip(),
                }
    return m


def collect():
    """Return ordered dict {pid: cate_name} of all unique products."""
    ids = {}
    if os.path.exists(IDS_FILE):
        with open(IDS_FILE, encoding="utf-8") as f:
            ids = json.load(f)
    done_cats = set(load_lines(CATS_DONE))
    counts = {}          # cate_name -> unique within that category (전수 check)
    total_pages = 0
    dcf = open(CATS_DONE, "a", encoding="utf-8")
    for cate_no, cate_name in CATS:
        if cate_name in done_cats:
            print("  cat DONE(skip)", cate_name)
            continue
        cat_seen = set()
        added = 0
        pg = 0
        for pg in range(1, MAX_PAGES + 1):
            url = "%s/product/list.html?cate_no=%s&page=%d" % (BASE, cate_no, pg)
            html = ww.http_get(url)
            page_ids = []
            for mm in ww._LINK_RE.finditer(html):
                pid = mm.group(1)
                if pid not in page_ids:
                    page_ids.append(pid)
            new = [p for p in page_ids if p not in cat_seen]
            total_pages += 1
            if not new:
                break
            for p in new:
                cat_seen.add(p)
                if p not in ids:
                    ids[p] = cate_name
                    added += 1
            with open(IDS_FILE, "w", encoding="utf-8") as f:
                json.dump(ids, f, ensure_ascii=False)
            time.sleep(0.2)
        counts[cate_name] = len(cat_seen)
        print("  cat %-10s cate_no=%s pages=%d cat_unique=%d new_global=%d "
              "total_unique=%d"
              % (cate_name, cate_no, pg, len(cat_seen), added, len(ids)))
        dcf.write(cate_name + "\n")
        dcf.flush()
    dcf.close()
    return ids, counts, total_pages


def detail_phase(ids, gosi):
    done = set(load_lines(DONE_FILE))
    fresh = not os.path.exists(DONE_FILE) or not os.path.exists(OUT)
    existing_rows = 0
    if not fresh and os.path.exists(OUT):
        with open(OUT, encoding="utf-8-sig") as f:
            existing_rows = max(0, sum(1 for _ in f) - 1)
    f = open(OUT, "w" if fresh else "a", newline="", encoding="utf-8-sig")
    w = csv.DictWriter(f, fieldnames=HEADER)
    if fresh:
        w.writeheader()
    df = open(DONE_FILE, "a", encoding="utf-8")
    written = existing_rows
    fails = stubs = joined = 0
    capped = False
    todo = [p for p in ids if p not in done]
    print("detail: %d products total, %d already done, %d todo, %d rows in csv"
          % (len(ids), len(done), len(todo), written))
    for i, pid in enumerate(todo, 1):
        if written >= MAX_ROWS:
            capped = True
            print("CAP reached at", MAX_ROWS)
            break
        cate_name = ids[pid]
        try:
            html = ww.http_get("%s/product/detail.html?product_no=%s"
                               % (BASE, pid))
            prod = ww.parse_jsonld_product(html) if html else None
            if not prod:
                stubs += 1
            else:
                row = ww.normalize(prod, "", cate_name)
                row["url"] = ("%s/product/detail.html?product_no=%s"
                              % (BASE, pid))
                o, mat, mfg = ww.try_info_notice(html)
                row["origin"], row["material"], row["mfg_date"] = o, mat, mfg
                g = gosi.get(row["style_code"])
                if g:
                    if g["origin"]:
                        row["origin"] = g["origin"]
                    if g["material"]:
                        row["material"] = g["material"]
                    if g["mfg_date"]:
                        row["mfg_date"] = g["mfg_date"]
                    joined += 1
                w.writerow(row)
                f.flush()
                written += 1
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("  detail FAIL", pid, type(e).__name__, e)
        df.write(pid + "\n")
        df.flush()
        if i % 50 == 0:
            print("  ...%d/%d written=%d stubs=%d fails=%d gosi_joined=%d"
                  % (i, len(todo), written, stubs, fails, joined))
        time.sleep(0.25)
    f.close()
    df.close()
    return written, stubs, fails, joined, capped


def dedup_csv():
    rows = []
    with open(OUT, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    seen = set()
    out = []
    empty_sc = 0
    for r in rows:
        sc = (r.get("style_code") or "").strip()
        if not sc:
            empty_sc += 1
        k = sc or r.get("url")
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in HEADER})
    return len(out), empty_sc


def main():
    t0 = time.time()
    gosi = load_gosi()
    print("gosi rows:", len(gosi))
    print("=== PHASE 1: collect ===")
    ids, counts, total_pages = collect()
    print("unique products:", len(ids), "pages crawled:", total_pages)
    print("=== PHASE 2: detail ===")
    written, stubs, fails, joined, capped = detail_phase(ids, gosi)
    print("=== dedup CSV ===")
    final, empty_sc = dedup_csv()
    status = {
        "after": final,
        "before": 120,
        "unique_products": len(ids),
        "pages_crawled": total_pages,
        "categories": len(CATS),
        "cat_counts": counts,
        "stubs": stubs,
        "fails": fails,
        "gosi_joined": joined,
        "empty_style_code": empty_sc,
        "capped": capped,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(STATUS, "w", encoding="utf-8") as sf:
        json.dump(status, sf, ensure_ascii=False, indent=2)
    print("DONE", json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
