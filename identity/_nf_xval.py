#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-validate sitemap code set against task-named category pages.
Collect /product/{code} (6+ alnum, non-numeric) from category pages incl.
deep pagination, diff against _nf_sitemap_codes.txt. Append any missing.
"""
import re, sys, time, urllib.request, urllib.parse, os
sys.path.insert(0, "/Users/a1101417/Work/business-model/identity")
import extract_northface as nf

BASE = nf.BASE
CODES_FILE = "/Users/a1101417/Work/business-model/identity/outputs/_nf_sitemap_codes.txt"


def fetch(url):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(enc, headers={
        "User-Agent": nf.UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", errors="replace")


def page_codes(h):
    return [c for c in dict.fromkeys(re.findall(r"/product/([A-Za-z0-9]+)", h))
            if not c.isdigit() and len(c) >= 6]


# task-named categories + main aggregators + deep paginated ones
CATS = [
    "lightpadding", "burmuda", "running-vest", "maryjane-sandals",
    "pigment-setup", "online26",
    "activity/hiking/best_pick", "activity/running/best_pick",
    "men", "women", "kids", "shoes", "equipment", "whitelabel",
    "summit-series", "Sale",
]

with open(CODES_FILE, encoding="utf-8") as f:
    known = set(l.strip() for l in f if l.strip())
print("known sitemap codes:", len(known))

found = set()
for cat in CATS:
    cat_seen = set()
    pages = 0
    count = "?"
    for pg in range(1, 60):  # deep enough for Sale
        url = "%s/category/n/%s?page=%d" % (BASE, cat, pg)
        try:
            h = fetch(url)
        except Exception as e:
            print("  FAIL", cat, pg, e)
            break
        if pg == 1:
            m = re.search(r"(\d[\d,]*)\s*개", h)
            count = m.group(1) if m else "?"
        pc = set(page_codes(h))
        new = pc - cat_seen
        if not new:
            break
        cat_seen |= new
        pages = pg
        time.sleep(0.1)
    found |= cat_seen
    miss_here = cat_seen - known
    print("  %-30s count=%-6s pages=%d codes=%d not_in_sitemap=%d"
          % (cat, count, pages, len(cat_seen), len(miss_here)))

missing = sorted(found - known)
print("TOTAL category codes:", len(found))
print("MISSING from sitemap set:", len(missing))
print(missing[:50])

if missing:
    with open(CODES_FILE, "a", encoding="utf-8") as f:
        for c in missing:
            f.write(c + "\n")
    print("APPENDED %d missing codes to %s" % (len(missing), CODES_FILE))
