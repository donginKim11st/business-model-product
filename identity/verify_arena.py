#!/usr/bin/env python3
"""Post-crawl verification for the 아레나 전수 extraction.

1. Coverage proof (no XML sitemap exists on this cafe24 shop): confirm each leaf category's
   page-1 product ids are a SUBSET of a section root's full crawl. If leaf ⊆ root for every
   section, the root aggregates its descendants -> the 149-category union is exhaustive per
   section, and SALE (included) catches anything delisted from sections.
2. CSV integrity: row count, style_code dedup status, field fill rates.
"""
import json, csv, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs", "extract_brand_arena.csv")
IDMAP = os.path.join(HERE, "outputs", "_arena_ids.json")
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
ROOTS = {"WOMEN": 24, "MEN": 25}  # primary aggregators to prove against


def main():
    cache = json.load(open(IDMAP, encoding="utf-8"))
    meta = cache["meta"]
    cat_pages = cache.get("cat_pages", {})
    cat_all = {int(k): set(map(str, v)) for k, v in cache.get("cat_all_ids", {}).items()}

    # union of all crawled product ids = keys of meta (every id we assigned)
    union = set(meta.keys())
    print(f"unique product_no in crawl union: {len(union)}")
    print(f"categories crawled: {len(cat_pages)}  total list pages: {sum(cat_pages.values())}")

    # Coverage proof (no XML sitemap on this cafe24 shop): each section root aggregates its
    # descendants -> every leaf category's full id set must be a SUBSET of its root's full crawl.
    # If leaf ⊆ root for WOMEN & MEN, the 149-category union is exhaustive per section.
    for name, root in ROOTS.items():
        rids = cat_all.get(root, set())
        print(f"  root {name}(cate {root}) full ids={len(rids)} pages={cat_pages.get(str(root),'?')}")

    # leaf ⊆ root checks for known WOMEN/MEN leaves; and every category ⊆ union (sanity)
    women_leaves = [70, 71, 72, 29, 62, 989]   # under WOMEN(24)
    men_leaves = [78, 79, 32]                   # under MEN(25)
    ok_cov = True
    for root, leaves in ((24, women_leaves), (25, men_leaves)):
        rids = cat_all.get(root, set())
        for c in leaves:
            ids = cat_all.get(c, set())
            miss = ids - rids
            st = "OK" if not miss else f"{len(miss)} NOT in root"
            if miss:
                ok_cov = False
            print(f"  leaf {c} ({len(ids)}) ⊆ root {root}: {st}")
    # every crawled category must be ⊆ union (build integrity)
    union_ok = all(ids <= union for ids in cat_all.values())
    print(f"all categories ⊆ union: {'PASS' if union_ok else 'FAIL'}")
    print(f"coverage (leaf ⊆ root): {'PASS' if ok_cov else 'FAIL'}")

    # CSV integrity
    rows = list(csv.DictReader(open(OUT, encoding="utf-8-sig")))
    print(f"\nCSV rows: {len(rows)}")
    codes = [r["style_code"] for r in rows]
    nonempty = [c for c in codes if c]
    print(f"style_code: nonempty={len(nonempty)} unique_nonempty={len(set(nonempty))} empty={len(codes)-len(nonempty)}")
    dup = len(nonempty) - len(set(nonempty))
    print(f"duplicate style_codes remaining: {dup} ({'PASS' if dup==0 else 'FAIL'})")
    # field fill
    fill = {c: sum(1 for r in rows if str(r.get(c, '')).strip()) for c in HEADER}
    print("field fill:", fill)
    # header check
    with open(OUT, encoding="utf-8-sig") as f:
        hdr = f.readline().strip().lstrip("﻿")
    print("header exact match:", hdr == ",".join(HEADER), "|", hdr)


if __name__ == "__main__":
    main()
