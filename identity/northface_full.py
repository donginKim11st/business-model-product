#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FULL (전수) catalog crawler for THE NORTH FACE Korea.

Two resumable phases:
  Phase 1 (collect): walk a coverage set of categories, paginate each via
      ?page=N until a page adds zero NEW codes *within that category*
      (the persistent 추천-carousel codes make the empty page add 0 -> stop).
      Codes accumulate in _nf_codes.txt; finished categories in _nf_cats_done.txt.
  Phase 2 (detail): fetch /product/{code} for every unique code, append a row
      per product to the CSV (flush each), marking attempted codes in
      _nf_done.txt so a restart resumes. Caps at 5000 rows.

Reuses parse_detail/gosi/sizes_of/color_of from extract_northface unchanged
(the existing 120-row CSV proves they work). northface is plain SSR -> urllib.
"""
import os, re, csv, sys, json, time, threading, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO = "/Users/a1101417/Work/business-model/identity"
sys.path.insert(0, REPO)
import extract_northface as nf  # parse_detail, fetch, BASE, HEADER, ...

BASE = nf.BASE
OUT = os.path.join(REPO, "outputs", "extract_brand_northface.csv")
CODES_FILE = os.path.join(REPO, "outputs", "_nf_codes.txt")
CATS_DONE = os.path.join(REPO, "outputs", "_nf_cats_done.txt")
DONE_FILE = os.path.join(REPO, "outputs", "_nf_done.txt")
STATUS = os.path.join(REPO, "outputs", "_nf_status.json")
HEADER = nf.HEADER
MAX_ROWS = 5000
MAX_PAGES = 120  # safety cap; Sale ~40pg is the deepest

UA = nf.UA


def fetch(url):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(enc, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", errors="replace")


def page_codes(h):
    # alnum /product/{code} links, order-preserved, drop pure-numeric carousel ids
    return [c for c in dict.fromkeys(re.findall(r"/product/([A-Za-z0-9]+)", h))
            if not c.isdigit()]


# --- coverage set: bare aggregators + activity leaves + standalone landings ---
BARE = ["men", "women", "kids", "shoes", "equipment", "whitelabel",
        "summit-series", "Sale"]
ACTIVITY = [
    "activity/camping/bestpick", "activity/camping/equipment",
    "activity/camping/inner", "activity/camping/jacket", "activity/camping/kids",
    "activity/camping/pants", "activity/camping/shoes", "activity/camping/tent",
    "activity/climbing/equipment", "activity/climbing/inner",
    "activity/climbing/kids", "activity/climbing/pants", "activity/climbing/shoes",
    "activity/hiking/best_pick", "activity/hiking/dryvent",
    "activity/hiking/equipment", "activity/hiking/goretexjacket",
    "activity/hiking/inner", "activity/hiking/jacket", "activity/hiking/kids",
    "activity/hiking/pants", "activity/hiking/shoes", "activity/hiking/windbreaker",
    "activity/running/best_pick", "activity/running/equipment",
    "activity/running/inner", "activity/running/jacket", "activity/running/pants",
    "activity/running/shoes", "activity/trailrunning/best_pick",
    "activity/trailrunning/equipment", "activity/trailrunning/goretexjacket",
    "activity/trailrunning/inner", "activity/trailrunning/jacket",
    "activity/trailrunning/pants", "activity/trailrunning/shoes",
]
STANDALONE = ["burmuda", "lightpadding", "maryjane-sandals", "online26",
              "pigment-setup", "running-vest"]
CATS = BARE + ACTIVITY + STANDALONE


def load_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def collect():
    seen = []  # global ordered unique codes
    sset = set(load_lines(CODES_FILE))
    seen.extend([c for c in load_lines(CODES_FILE)])
    done_cats = set(load_lines(CATS_DONE))
    counts = {}
    total_pages = 0
    cf = open(CODES_FILE, "a", encoding="utf-8")
    dcf = open(CATS_DONE, "a", encoding="utf-8")
    for cat in CATS:
        if cat in done_cats:
            print("  cat DONE(skip)", cat)
            continue
        cat_seen = set()
        added = 0
        for pg in range(1, MAX_PAGES + 1):
            url = "%s/category/n/%s?page=%d" % (BASE, cat, pg)
            try:
                h = fetch(url)
            except Exception as e:
                print("  page FAIL", cat, pg, type(e).__name__, e)
                time.sleep(1.0)
                continue
            if pg == 1:
                m = re.search(r"(\d[\d,]*)\s*개", h)
                counts[cat] = m.group(1) if m else "?"
            pc = page_codes(h)
            new = [c for c in pc if c not in cat_seen]
            total_pages += 1
            if not new:
                break
            for c in new:
                cat_seen.add(c)
                if c not in sset:
                    sset.add(c)
                    seen.append(c)
                    cf.write(c + "\n")
                    added += 1
            cf.flush()
            time.sleep(0.15)
        print("  cat %-34s count=%-6s pages=%d new_global=%d total_unique=%d"
              % (cat, counts.get(cat, "?"), pg, added, len(seen)), flush=True)
        dcf.write(cat + "\n")
        dcf.flush()
    cf.close()
    dcf.close()
    return seen, counts, total_pages


WORKERS = 8


def detail_phase(codes):
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
    state = {"written": existing_rows, "fails": 0, "stubs": 0,
             "capped": False, "n": 0}
    lock = threading.Lock()
    todo = [c for c in codes if c not in done]
    print("detail: %d codes total, %d already done, %d todo, %d rows in csv"
          % (len(codes), len(done), len(todo), existing_rows), flush=True)

    def work(c):
        try:
            return c, nf.parse_detail(c), None
        except Exception as e:
            return c, None, "%s: %s" % (type(e).__name__, e)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, c) for c in todo]
        for fut in as_completed(futs):
            c, row, err = fut.result()
            with lock:
                if err:
                    state["fails"] += 1
                    print("  detail FAIL", c, err, flush=True)
                elif row:
                    if state["written"] < MAX_ROWS:
                        w.writerow(row)
                        f.flush()
                        state["written"] += 1
                    else:
                        state["capped"] = True
                else:
                    state["stubs"] += 1
                df.write(c + "\n")
                df.flush()
                state["n"] += 1
                if state["n"] % 100 == 0:
                    print("  ...%d/%d written=%d stubs=%d fails=%d"
                          % (state["n"], len(todo), state["written"],
                             state["stubs"], state["fails"]), flush=True)
    f.close()
    df.close()
    return state["written"], state["stubs"], state["fails"], state["capped"]


def dedup_csv():
    rows = []
    with open(OUT, encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            rows.append(r)
    seen = set()
    out = []
    for r in rows:
        k = r.get("style_code") or r.get("url")
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in HEADER})
    return len(out)


def main():
    t0 = time.time()
    print("=== PHASE 1: collect codes ===")
    codes, counts, total_pages = collect()
    print("unique codes collected:", len(codes), "pages crawled:", total_pages)
    print("=== PHASE 2: detail fetch ===")
    written, stubs, fails, capped = detail_phase(codes)
    print("=== dedup CSV ===")
    final = dedup_csv()
    status = {
        "after": final,
        "before": 120,
        "unique_codes": len(codes),
        "pages_crawled": total_pages,
        "categories": len(CATS),
        "stubs": stubs,
        "fails": fails,
        "capped": capped,
        "elapsed_sec": round(time.time() - t0, 1),
        "counts": counts,
    }
    with open(STATUS, "w", encoding="utf-8") as sf:
        json.dump(status, sf, ensure_ascii=False, indent=2)
    print("DONE", json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
