#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FULL exhaustive product extractor for PUMA Korea (Demandware / SFCC).

Strategy (verified by probe):
  * Category grids honour ?sz=N and return the COMPLETE master-product set in one
    request (start-offset pagination yields no new model_ids beyond the first page).
  * Top-level gender grids are supersets of their named sub-categories, e.g.
    여성 제품=584 >> 신발(147)+의류(263)+용품(52). So crawl the top-level grids
    for the universe, then crawl sub-category grids only to label category.
  * Universe sources: 남성 / 여성 / kids / 스포츠 (스포츠 adds ~16 sport-only items).
  * One row per PDP (master product); style_code = full styleNumber (model_color,
    NOT truncated) from the PDP's JSON-LD / dwvar data.

Reuses parse_product() from extract_brand_puma.py unchanged.
"""
import re, csv, sys, time, json, urllib.parse, os
from curl_cffi import requests as creq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_brand_puma import parse_product, HEADER, BASE

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
OUT = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_puma.csv"
LOG = "/Users/a1101417/Work/business-model/identity/outputs/_puma_full.log"
GRID_SZ = 3000
MAX_ROWS = 5000

# Universe + gender (priority order; first grid that contains a model wins gender)
UNIVERSE_GRIDS = [
    ("남성", "/kr/ko/남성"),
    ("여성", "/kr/ko/여성"),
    ("키즈", "/kr/ko/kids"),
    ("",     "/kr/ko/스포츠"),   # catch sport-only equipment/unisex items
]
# Sub-category grids -> category label (gender from UNIVERSE_GRIDS, not these)
CATEGORY_GRIDS = [
    ("신발", "/kr/ko/남성/신발-1"),
    ("신발", "/kr/ko/여성/신발-2"),
    ("의류", "/kr/ko/남성/의류-1"),
    ("의류", "/kr/ko/여성/의류-2"),
    ("용품", "/kr/ko/남성/용품"),
    ("용품", "/kr/ko/여성/용품"),
]


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, file=sys.stderr, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch(url, timeout=90, retries=3):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for a in range(retries):
        try:
            r = creq.get(enc, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            }, impersonate="chrome120", timeout=timeout)
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = str(e)
        time.sleep(1.0 + a)
    raise RuntimeError(last)


def grid_links(html):
    """Return ordered unique [(mid, full_url)] from a category grid."""
    out, seen = [], set()
    for href in re.findall(r'/kr/ko/pd/[^"\'\s>]+\.html', html):
        mu = re.search(r"/(\d+)\.html", href)
        if not mu:
            continue
        mid = mu.group(1)
        if mid in seen:
            continue
        seen.add(mid)
        out.append((mid, BASE + href))
    return out


def recover_style(html, url):
    """If JSON-LD styleNumber is missing, recover the default colorway's
    model_color token (most frequent model_<NN> token in the PDP HTML)."""
    mu = re.search(r"/(\d+)\.html", url)
    if not mu:
        return ""
    model = mu.group(1)
    toks = re.findall(re.escape(model) + r"_(\d{2,3})", html)
    if not toks:
        return ""
    # default colorway dominates the PDP markup -> most frequent suffix
    best = max(set(toks), key=toks.count)
    return f"{model}_{best}"


def main():
    open(LOG, "w").close()
    # ---- 1. universe + gender ----
    gender_map = {}
    url_map = {}
    order = []
    for gender, path in UNIVERSE_GRIDS:
        try:
            html = fetch(f"{BASE}{path}?sz={GRID_SZ}")
        except Exception as e:
            log(f"[grid FAIL] {path}: {e}")
            continue
        links = grid_links(html)
        added = 0
        for mid, url in links:
            if mid not in url_map:
                url_map[mid] = url
                order.append(mid)
                added += 1
            gender_map.setdefault(mid, gender)
        log(f"[universe] {path} links={len(links)} new={added} total={len(order)}")
        time.sleep(0.4)

    # ---- 2. category labels ----
    cat_map = {}
    for category, path in CATEGORY_GRIDS:
        try:
            html = fetch(f"{BASE}{path}?sz={GRID_SZ}")
        except Exception as e:
            log(f"[cat grid FAIL] {path}: {e}")
            continue
        links = grid_links(html)
        for mid, url in links:
            cat_map.setdefault(mid, category)
            if mid not in url_map:           # safety: subcat-only product
                url_map[mid] = url
                order.append(mid)
                gender_map.setdefault(mid, "남성" if "남성" in path else "여성")
        log(f"[category] {path} links={len(links)} labelled-total={len(cat_map)}")
        time.sleep(0.4)

    log(f"=== UNIVERSE: {len(order)} unique PDPs ===")

    # ---- 3. fetch each PDP, parse, checkpoint ----
    rows, ok_n, fail_n = [], 0, 0
    capped = False
    for i, mid in enumerate(order, 1):
        if len(rows) >= MAX_ROWS:
            capped = True
            log(f"[CAP] reached {MAX_ROWS} rows -> stop")
            break
        url = url_map[mid]
        gender = gender_map.get(mid, "")
        category = cat_map.get(mid, "")
        try:
            html = fetch(url)
            row = parse_product(html, url, gender, category)
            if not row["name"]:
                fail_n += 1
                log(f"[empty] {mid}")
                continue
            if "_" not in (row["style_code"] or ""):
                rec = recover_style(html, url)
                if rec:
                    row["style_code"] = rec
            rows.append(row)
            ok_n += 1
        except Exception as e:
            fail_n += 1
            log(f"[pdp FAIL] {mid}: {e}")
        if i % 25 == 0:
            _write(rows)
            log(f"... {i}/{len(order)} ok={ok_n} fail={fail_n} (checkpoint)")
        time.sleep(0.25)

    _write(rows)
    no_color = sum(1 for r in rows if "_" not in (r["style_code"] or ""))
    filled = {k: sum(1 for r in rows if r[k]) for k in HEADER}
    summary = {
        "written": len(rows), "ok": ok_n, "fail": fail_n,
        "universe": len(order), "capped": capped,
        "style_code_without_colorway_suffix": no_color,
        "filled": filled,
    }
    log("SUMMARY " + json.dumps(summary, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _write(rows):
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
