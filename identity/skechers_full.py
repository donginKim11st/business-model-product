#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exhaustive (전수) product extractor for SKECHERS Korea (Broadleaf Commerce SSR).

Strategy
  Phase 1  collect ALL grid SKUs across EVERY /category/{path} via ?page=N
           - grid product = <a class="product-url" data-dtr-track="SKU">
           - stop authority = per-category totalCount (data-module-pagination)
           - 23 items/page; empty page = end; retry a 0-grid page below total
  Phase 2  fetch every /product/{SKU} detail, parse, dedup by style_code
           - sibling-color safety net: harvest same-model /product links
  Output   outputs/extract_brand_skechers.csv  (utf-8-sig, exact header)

Resumable via JSONL checkpoints (plain utf-8, append-safe). CSV is written
once at the end from the deduped details JSONL so a durable copy always exists.
Cap: stop at 5000 final deduped rows (noted).
"""
import re, csv, json, time, ssl, os, sys, html as ihtml
import urllib.request, urllib.parse

try:
    from curl_cffi import requests as ccrequests
    HAVE_CC = True
except Exception:
    HAVE_CC = False

BASE = "https://www.skecherskorea.co.kr"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
OUTDIR = "/Users/a1101417/Work/business-model/identity/outputs"
OUT = os.path.join(OUTDIR, "extract_brand_skechers.csv")
SKU_CP = os.path.join(OUTDIR, "_skechers_skus.json")     # {sku: {ghint, cat}}
CAT_CP = os.path.join(OUTDIR, "_skechers_cats.json")     # {cat: {total, collected, pages}}
DET_CP = os.path.join(OUTDIR, "_skechers_details.jsonl") # one parsed row per line
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
CAP = 5000
PAGESIZE = 23

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def fetch(url, ref=None, tries=4):
    """GET with chrome impersonation (curl_cffi) -> urllib fallback, retry/backoff."""
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for t in range(tries):
        # curl_cffi primary
        if HAVE_CC:
            try:
                hd = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
                if ref:
                    hd["Referer"] = ref
                r = ccrequests.get(enc, headers=hd, impersonate="chrome",
                                   timeout=40, verify=False)
                if r.status_code in (429, 503):
                    last = "HTTP %d" % r.status_code
                    time.sleep(2 + 3 * t)
                    continue
                if r.status_code == 200:
                    return r.text
                last = "HTTP %d" % r.status_code
            except Exception as e:
                last = repr(e)
        # urllib fallback
        try:
            hd = {"User-Agent": UA,
                  "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                  "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
            if ref:
                hd["Referer"] = ref
            req = urllib.request.Request(enc, headers=hd)
            with urllib.request.urlopen(req, timeout=40, context=CTX) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
            if e.code in (429, 503):
                time.sleep(2 + 3 * t)
                continue
            if e.code == 404:
                raise
        except Exception as e:
            last = repr(e)
        time.sleep(1 + 2 * t)
    raise RuntimeError("fetch failed %s: %s" % (url, last))


def clean(s):
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def dec(s):
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def detect_gender(blob, fallback):
    b = blob.lower()
    if any(k in b for k in ["키즈", "아동", "주니어", "유아", "남아", "여아", "kid", "junior", "toddler"]):
        return "키즈"
    if any(k in b for k in ["공용", "남녀공용", "unisex"]):
        return "공용"
    if any(k in b for k in ["여성", "women", "woman"]):
        return "여성"
    if any(k in b for k in ["남성", "men", "man"]):
        return "남성"
    return fallback


def ghint_from_path(path):
    p = path.lower()
    if "women" in p:
        return "여성"
    if "men" in p:
        return "남성"
    if "kids" in p:
        return "키즈"
    return ""


GRID_RE = re.compile(r'<a class="product-url"[^>]*data-dtr-track="([A-Za-z0-9]+)"')


def grid_skus(h):
    return GRID_RE.findall(h)


# ---------------- discovery ----------------
# Broadest parent nodes. Verified: a parent category is a strict superset of the
# union of its leaf children (men/shoes=436 >= union(leaves)=435, with 1 product
# present ONLY in the parent). So crawling these roots yields the COMPLETE catalog
# far faster than re-crawling every redundant leaf. Gender roots first so
# gender-specific products get the correct gender hint; line/bucket roots after as
# insurance for anything tagged only to USA/best/new/apparel/outlet.
SEED = [
    "/category/men", "/category/women", "/category/kids",   # gender roots
    "/category/usa",                                          # USA line root
    "/category/apparel",                                      # apparel root (insurance)
    "/category/best", "/category/new",                        # site-wide buckets
    "/category/kids/outlet",                                  # outlet bucket
]


def discover_categories():
    """All /category links (entity-decoded) for completeness logging/audit."""
    h = fetch(BASE)
    h2 = ihtml.unescape(h)
    cats = re.findall(r'href="(/category/[^"#?]+)"', h2)
    out = []
    for c in cats:
        c = re.sub(r"/{2,}", "/", c).rstrip("/")
        if c and c != "/category":
            out.append(c)
    return sorted(set(out))


# ---------------- phase 1: collect SKUs ----------------
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def phase1():
    # Full 151-category sweep: every category is a descendant of the broad roots,
    # so this MOSTLY CONFIRMS the broad set while catching any cross-cutting
    # (tech/act/collection/best/new) product whose primary assignment does not
    # surface under a gender root. Per-category totalCount stop + <<MISMATCH log
    # is the completeness gate; phase 2 must not proceed with unexplained mismatch.
    cats = discover_categories()
    print("PHASE1: crawling %d categories (full sweep)" % len(cats), flush=True)
    sku_map = load_json(SKU_CP, {})
    cat_done = load_json(CAT_CP, {})

    for ci, cat in enumerate(cats, 1):
        if cat in cat_done:
            continue
        ghint = ghint_from_path(cat)
        ref = BASE + cat
        collected = set()
        total = None
        page = 1
        empty_retry = 0
        pages = 0
        while True:
            url = "%s%s?page=%d" % (BASE, cat, page)
            try:
                h = fetch(url, ref)
            except Exception as e:
                print("  LIST ERR %s p%d: %r" % (cat, page, e), flush=True)
                empty_retry += 1
                if empty_retry >= 3:
                    break
                time.sleep(2)
                continue
            if total is None:
                m = re.search(r"totalCount:(\d+)", h)
                total = int(m.group(1)) if m else None
            sk = grid_skus(h)
            pages = page
            if not sk:
                # confirm true end vs transient blank when still below total
                if total and len(collected) < total and empty_retry < 3:
                    empty_retry += 1
                    time.sleep(2)
                    continue
                break
            empty_retry = 0
            new = 0
            for s in sk:
                if s not in collected:
                    collected.add(s)
                    new += 1
                if s not in sku_map:
                    sku_map[s] = {"ghint": ghint, "cat": cat}
            if total and len(collected) >= total:
                break
            if new == 0:
                # no progress -> stop
                break
            page += 1
            time.sleep(0.25)
        cat_done[cat] = {"total": total, "collected": len(collected), "pages": pages}
        ok = (total is None) or (len(collected) == total)
        flag = "" if ok else "  <<MISMATCH"
        print("  [%d/%d] %s  collected=%d total=%s pages=%d uniqSKUs=%d%s"
              % (ci, len(cats), cat, len(collected), total, pages, len(sku_map), flag),
              flush=True)
        save_json(SKU_CP, sku_map)
        save_json(CAT_CP, cat_done)
    print("PHASE1 DONE: %d unique SKUs across %d categories" % (len(sku_map), len(cat_done)),
          flush=True)
    return sku_map, cat_done


# ---------------- phase 2: details ----------------
def parse_detail(raw, sku, gender_fallback):
    o = {h: "" for h in HEADER}
    o["source"] = "skechers"
    o["brand"] = "스케쳐스"
    o["currency"] = "KRW"
    o["style_code"] = sku
    o["url"] = BASE + "/product/" + sku

    m = re.search(r"var productInfo = \{(.*?)\};", raw, re.S)
    pi = m.group(1) if m else ""

    def f(key):
        mm = re.search(key + r"\s*:\s*'([^']*)'", pi)
        return mm.group(1) if mm else ""

    o["name"] = dec(f("name")) or dec(f("nameEn"))
    mo = re.search(r"model\s*:\s*'([^']*)'", pi)
    if mo and mo.group(1):
        o["style_code"] = mo.group(1)
    cate_l = dec(f("cateLNm"))
    cate_m = dec(f("cateMNm"))
    cate_s = dec(f("cateSNm"))
    o["category"] = " > ".join([x for x in [cate_m, cate_s] if x]) or cate_l
    o["gender"] = detect_gender(" ".join([cate_l, cate_m, o["name"]]), gender_fallback)

    fm = re.search(r'selector-color.*?data-friendly-name="([^"]*)"', raw, re.S)
    km = re.search(r'data-color-kor="([^"]*)"', raw)
    fn = fm.group(1).strip() if fm else ""
    kor = km.group(1).strip() if km else ""
    o["color"] = fn if fn and fn != "기타" else (kor or fn)

    sizes = re.findall(r'class="variation-size[^"]*"[^>]*typeName="([^"]*)"', raw)
    o["sizes"] = "|".join(dict.fromkeys([s.strip() for s in sizes if s.strip()]))

    sd = re.search(r'data-sku-data="(\[.*?\])"\s', raw, re.S)
    if sd:
        try:
            arr = json.loads(ihtml.unescape(sd.group(1)))
            pr = [int(x["salePrice"]) for x in arr if x.get("salePrice")]
            if pr:
                o["price"] = str(min(pr))
        except Exception:
            pass
    if not o["price"]:
        lp = re.search(r"lowestPrice = \{'price':\{'amount':(\d+)", raw)
        if lp:
            o["price"] = lp.group(1)

    for k, v in re.findall(
            r'<dt class="tag-key">(.*?)</dt>\s*<dd class="tag-value">(.*?)</dd>', raw, re.S):
        k = clean(k); v = clean(v)
        if k == "소재" and not o["material"]:
            o["material"] = v
        elif k == "원산지" and not o["origin"]:
            o["origin"] = v
        elif ("제조년월" in k or "제조연월" in k) and not o["mfg_date"]:
            o["mfg_date"] = v
    return o


def harvest_siblings(raw, sku):
    """same-model /product links present on the detail page (other colors)."""
    prefix = re.sub(r"\d+$", "", sku)  # strip trailing color digits
    if len(prefix) < 5:
        return []
    found = set(re.findall(r"/product/([A-Za-z0-9]+)", raw))
    return [s for s in found if s.startswith(prefix) and s != sku]


def load_done_details():
    done = {}
    rows = []
    if os.path.exists(DET_CP):
        with open(DET_CP, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                done[r.get("_sku", r.get("style_code"))] = True
                rows.append(r)
    return done, rows


def phase2(sku_map):
    done, _ = load_done_details()
    print("PHASE2: %d SKUs total, %d already done" % (len(sku_map), len(done)), flush=True)
    queue = list(sku_map.keys())
    seen_q = set(queue)
    fails = []
    written = len(done)
    fout = open(DET_CP, "a", encoding="utf-8")
    i = 0
    while i < len(queue):
        sku = queue[i]; i += 1
        if sku in done:
            continue
        if written >= CAP:
            print("CAP %d reached, stopping detail phase" % CAP, flush=True)
            break
        try:
            raw = fetch(BASE + "/product/" + sku, tries=4)
        except Exception as e:
            fails.append(sku)
            print("  DETAIL ERR %s: %r" % (sku, e), flush=True)
            time.sleep(1)
            continue
        gh = sku_map.get(sku, {}).get("ghint", "")
        row = parse_detail(raw, sku, gh)
        row["_sku"] = sku
        fout.write(json.dumps(row, ensure_ascii=False) + "\n")
        fout.flush()
        done[sku] = True
        written += 1
        # sibling safety net
        for sib in harvest_siblings(raw, sku):
            if sib not in seen_q:
                seen_q.add(sib)
                queue.append(sib)
                sku_map.setdefault(sib, {"ghint": gh, "cat": "sibling"})
        if written % 50 == 0:
            print("  [%d] %s %s %s sz=%s" %
                  (written, row["style_code"], row["name"][:24], row["price"],
                   row["sizes"][:18]), flush=True)
            save_json(SKU_CP, sku_map)
        time.sleep(0.2)
    fout.close()
    save_json(SKU_CP, sku_map)
    print("PHASE2 DONE: written=%d fails=%d" % (written, len(fails)), flush=True)
    return fails


# ---------------- finalize CSV ----------------
def write_csv():
    _, rows = load_done_details()
    dedup = {}
    for r in rows:
        key = r.get("style_code") or r.get("_sku")
        if key and key not in dedup:
            dedup[key] = r
    capped = len(dedup) >= CAP
    final = list(dedup.values())[:CAP]
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        for r in final:
            w.writerow({h: r.get(h, "") for h in HEADER})
    fills = {h: sum(1 for r in final if r.get(h)) for h in HEADER}
    print("WROTE %d rows -> %s (capped=%s)" % (len(final), OUT, capped), flush=True)
    print("fills:", fills, flush=True)
    return len(final), capped


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("all", "p1"):
        phase1()
    sku_map = load_json(SKU_CP, {})
    if mode in ("all", "p2"):
        phase2(sku_map)
    n, capped = write_csv()
    cat_done = load_json(CAT_CP, {})
    total_pages = sum((c.get("pages") or 0) for c in cat_done.values())
    mism = [k for k, v in cat_done.items() if v.get("total") and v.get("collected") != v.get("total")]
    print("SUMMARY rows=%d pages=%d cats=%d mismatches=%d capped=%s"
          % (n, total_pages, len(cat_done), len(mism), capped), flush=True)
    if mism:
        print("MISMATCH CATS:", mism, flush=True)


if __name__ == "__main__":
    main()
