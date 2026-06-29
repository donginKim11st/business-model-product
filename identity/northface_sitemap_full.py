#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FULL (전수) catalog crawler for THE NORTH FACE Korea via the official sitemap.

Why this exists / fail diagnosis
--------------------------------
The deliverable CSV (outputs/extract_brand_northface.csv, 120 rows) was produced
by extract_northface.py, which is a SAMPLE extractor: it fetches only the first
grid page of 7 seed categories (PER_SEED=20) and hard-caps at SAMPLE=120 with
NO pagination at all. A later northface_full.py did implement ?page=N
pagination, but its Phase-1 collect run was interrupted (only 4 of ~50
categories crawled -> 1638 codes; detail phase never ran), so the 120-row CSV
was never replaced. The category-enumeration approach is also inherently lossy:
it requires hand-listing every category slug and double-crawls the persistent
"추천 상품" carousel.

Fix
---
robots.txt -> sitemap index -> NF-sitemap-products.xml enumerates every live
colorway-level /product/{code} URL exhaustively (2748 codes). This is the
gold-standard source. We fetch detail for each code with the proven
extract_northface.parse_detail logic.

Design
------
Phase 1: download product sitemap, extract <loc> codes (colorway-level), union
         any extra codes ever found by the old crawl (provable superset). Save
         to _nf_sitemap_codes.txt.
Phase 2: ThreadPoolExecutor fetch /product/{code} for each code not yet done.
         Each success is appended to _nf_rows.jsonl (keyed by the CRAWLED code,
         not the re-parsed _sku, so two colorways can never collapse). Every
         attempted code is recorded in _nf_sitemap_done.txt -> fully resumable.
Final:   rebuild CSV from _nf_rows.jsonl, dedup on crawled code, exact header,
         utf-8-sig. Cap MAX_ROWS=5000 (set has 2755, won't hit).
"""
import os, re, csv, sys, json, time, random, threading, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO = "/Users/a1101417/Work/business-model/identity"
sys.path.insert(0, REPO)
import extract_northface as nf  # parse_detail, fetch, BASE, HEADER, UA

BASE = nf.BASE
HEADER = nf.HEADER
UA = nf.UA

OUT = os.path.join(REPO, "outputs", "extract_brand_northface.csv")
CODES_FILE = os.path.join(REPO, "outputs", "_nf_sitemap_codes.txt")
ROWS_JSONL = os.path.join(REPO, "outputs", "_nf_rows.jsonl")
DONE_FILE = os.path.join(REPO, "outputs", "_nf_sitemap_done.txt")
STATUS = os.path.join(REPO, "outputs", "_nf_sitemap_status.json")
OLD_COLLECTED = os.path.join(REPO, "outputs", "_nf_codes.txt")

SITEMAP = BASE + "/sitemap/NF-sitemap-products.xml"
import os as _os
WORKERS = int(_os.environ.get("NF_WORKERS", "8"))
MAX_ROWS = 5000
RETRIES = int(_os.environ.get("NF_RETRIES", "3"))


def fetch(url):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(enc, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def load_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def collect_codes():
    """Sitemap <loc> codes (colorway-level) + extras from old crawl (superset)."""
    if os.path.exists(CODES_FILE):
        codes = load_lines(CODES_FILE)
        print("codes loaded from cache:", len(codes), flush=True)
        return codes
    sm = fetch(SITEMAP)
    # only real product <loc> entries; image URLs hold numeric internal ids we skip
    sitemap = list(dict.fromkeys(
        re.findall(r"<loc>\s*https?://[^<]*?/product/([A-Za-z0-9]+)\s*</loc>", sm)))
    sset = set(sitemap)
    extras = [c for c in load_lines(OLD_COLLECTED)
              if c and not c.isdigit() and c not in sset]
    codes = sitemap + extras
    with open(CODES_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(codes) + "\n")
    print("sitemap codes: %d, extra (old-crawl) codes: %d, total: %d"
          % (len(sitemap), len(extras), len(codes)), flush=True)
    return codes


def detail_phase(codes):
    done = set(load_lines(DONE_FILE))
    todo = [c for c in codes if c not in done]
    rf = open(ROWS_JSONL, "a", encoding="utf-8")
    dfile = open(DONE_FILE, "a", encoding="utf-8")
    existing_rows = sum(1 for _ in open(ROWS_JSONL, encoding="utf-8")) if os.path.exists(ROWS_JSONL) else 0
    state = {"written": existing_rows, "stubs": 0, "fails": 0, "n": 0, "capped": False}
    lock = threading.Lock()
    print("detail: total=%d done=%d todo=%d rows_so_far=%d"
          % (len(codes), len(done), len(todo), existing_rows), flush=True)

    def work(c):
        last = None
        for attempt in range(RETRIES):
            try:
                return c, nf.parse_detail(c), None
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, e)
                # exponential backoff + jitter; 429 needs real cooldown
                time.sleep(min(20.0, 1.5 * (2 ** attempt)) + random.uniform(0, 0.7))
        return c, None, last

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, c) for c in todo]
        for fut in as_completed(futs):
            c, row, err = fut.result()
            with lock:
                if err:
                    state["fails"] += 1
                    print("  FAIL", c, err, flush=True)
                    # do NOT mark done on hard failure -> retried on resume
                else:
                    if row:
                        if state["written"] < MAX_ROWS:
                            rf.write(json.dumps({"code": c, "row": row},
                                                ensure_ascii=False) + "\n")
                            rf.flush()
                            state["written"] += 1
                        else:
                            state["capped"] = True
                    else:
                        state["stubs"] += 1
                    dfile.write(c + "\n")
                    dfile.flush()
                state["n"] += 1
                if state["n"] % 100 == 0:
                    print("  ...%d/%d written=%d stubs=%d fails=%d"
                          % (state["n"], len(todo), state["written"],
                             state["stubs"], state["fails"]), flush=True)
    rf.close()
    dfile.close()
    return state


def build_csv():
    """Dedup on crawled code (colorway-unique) and write final CSV."""
    rows, seen = [], set()
    with open(ROWS_JSONL, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue  # tolerate a torn final line from a crash
            code = obj["code"]
            if code in seen:
                continue
            seen.add(code)
            rows.append(obj["row"])
    rows.sort(key=lambda r: r.get("style_code", ""))
    rows = rows[:MAX_ROWS]
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HEADER})
    return len(rows)


def main():
    t0 = time.time()
    print("=== PHASE 1: collect codes from sitemap ===", flush=True)
    codes = collect_codes()
    print("=== PHASE 2: detail fetch ===", flush=True)
    st = detail_phase(codes)
    print("=== build CSV ===", flush=True)
    final = build_csv()
    filled = {}
    with open(OUT, encoding="utf-8-sig") as f:
        rr = list(csv.DictReader(f))
        for k in HEADER:
            filled[k] = sum(1 for r in rr if r.get(k))
    status = {
        "before": 120,
        "after": final,
        "total_codes": len(codes),
        "written": st["written"],
        "stubs": st["stubs"],
        "fails": st["fails"],
        "capped": st["capped"],
        "elapsed_sec": round(time.time() - t0, 1),
        "filled": filled,
    }
    with open(STATUS, "w", encoding="utf-8") as sf:
        json.dump(status, sf, ensure_ascii=False, indent=2)
    print("DONE", json.dumps(status, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
