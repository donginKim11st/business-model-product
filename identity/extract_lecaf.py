#!/usr/bin/env python3
# Full-census product extractor for 르까프 official mall (lecafmall.com, cafe24, hook=jsonld)
# Crawls EVERY category x EVERY page (until empty/last page), dedup by style_code.
# Mid-save: each parsed product is appended to a JSONL checkpoint => resumable.
import urllib.request, urllib.parse, re, json, csv, time, html as ihtml, os, sys

try:
    from curl_cffi import requests as cffi_requests  # optional, used as fallback
except Exception:
    cffi_requests = None

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BASE = "https://lecafmall.com"
OUT_DIR = "/Users/a1101417/Work/business-model/identity/outputs"
OUT_CSV = os.path.join(OUT_DIR, "extract_brand_lecaf.csv")
OUT_JSON = os.path.join(OUT_DIR, "extract_brand_lecaf.json")
CKPT_JSONL = os.path.join(OUT_DIR, "_lecaf_full_rows.jsonl")   # mid-save: one parsed product per line
CKPT_IDS = os.path.join(OUT_DIR, "_lecaf_collected_ids.json")  # id->category map (collection phase)

CATE_PROBE_RANGE = range(1, 80)   # auto-discover live categories in this id range
MAX_PAGES = 500                   # hard safety only; real stop = empty/last page
HARD_CAP = 5000                   # stop collecting unique products at this many
COLS = ["source", "brand", "style_code", "name", "color", "price", "currency",
        "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]

SIZE_SET = {"XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "4XL", "FREE", "F", "FR"}


def fetch(url, timeout=30):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        if cffi_requests is not None:
            try:
                r = cffi_requests.get(u, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"},
                                      impersonate="chrome", timeout=timeout)
                return r.status_code, r.text
            except Exception:
                return 0, ""
        return 0, ""


def pipe(seq):
    out = []
    for v in seq:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    return "|".join(out)


def style_code_from(name):
    if not name:
        return ""
    m = re.search(r'\b([A-Z]{2,3}-?\d{2,4}[A-Z]?)\b', name)
    return m.group(1).upper() if m else ""


def gender_from(name):
    n = name or ""
    if re.search(r'남여공용|남녀공용|남여|남녀|공용|UNISEX|unisex', n):
        return "공용"
    has_m = bool(re.search(r'\[?\s*남성\s*\]?|\bmen\b|\bMEN\b', n))
    has_w = bool(re.search(r'\[?\s*여성\s*\]?|\bwomen\b|\bWOMEN\b', n))
    if has_m and has_w:
        return "공용"
    if has_w:
        return "여성"
    if has_m:
        return "남성"
    return ""


def split_variant(pname, oname):
    """offer name = '{pname} {color}-{size}'  ->  (color, size)."""
    v = oname or ""
    if pname and v.startswith(pname):
        v = v[len(pname):]
    v = v.strip().lstrip("-").strip()
    if not v:
        return "", ""
    if "-" in v:
        left, right = v.rsplit("-", 1)
        left = left.strip()
        right = right.strip()
        if right and len(right) <= 8 and (re.search(r'\d', right) or right.upper() in SIZE_SET):
            return left, right
    return v, ""


def cate_label(title):
    """'Shoes - 등산화, 트레킹화 - 르까프' -> 'Shoes>등산화, 트레킹화'; 'Event -  - 르까프' -> 'Event'."""
    t = ihtml.unescape(title or "")
    parts = [p.strip() for p in t.split(" - ")]
    parts = [p for p in parts if p and p != "르까프"]
    if not parts:
        return ""
    if len(parts) >= 2 and parts[1]:
        return f"{parts[0]}>{parts[1]}"
    return parts[0]


def discover_categories():
    """Probe cate_no range; return ordered list of (cate_no, label, prdCount) for live cats."""
    cats = []
    for c in CATE_PROBE_RANGE:
        st, h = fetch(f"{BASE}/product/list.html?cate_no={c}&page=1")
        if st != 200 or not h:
            continue
        mc = re.search(r'prdCount[^0-9]*(\d[\d,]*)', h)
        ids = set(re.findall(r'product_no=(\d+)', h)); ids.discard("0")
        if mc is None and not ids:
            continue  # not a real category page
        cnt = int(mc.group(1).replace(",", "")) if mc else len(ids)
        tt = re.search(r'<title>(.*?)</title>', h, re.S)
        label = cate_label(tt.group(1) if tt else "")
        cats.append((c, label, cnt))
        time.sleep(0.1)
    return cats


def list_ids(cate, page):
    st, h = fetch(f"{BASE}/product/list.html?cate_no={cate}&page={page}")
    ids = []
    for m in re.findall(r'product_no=(\d+)', h):
        if m != "0" and m not in ids:
            ids.append(m)
    return ids


def collect_ids(cats):
    """Crawl every category x every page to end. Returns OrderedDict id->category (first cat wins)."""
    collected = {}
    for cate, label, cnt in cats:
        cat_seen = []
        for page in range(1, MAX_PAGES + 1):
            ids = list_ids(cate, page)
            if not ids:
                break                       # empty page -> end of category
            new = [i for i in ids if i not in cat_seen]
            if not new:
                break                       # page repeats (cafe24 clamps beyond last) -> end
            cat_seen += new
            for i in new:
                if i not in collected:
                    collected[i] = label
            if len(collected) >= HARD_CAP:
                print(f"  HARD_CAP {HARD_CAP} reached during collection")
                return collected, True
            time.sleep(0.2)
        print(f"  cate {cate} ({label}): listed {len(cat_seen)} (prdCount={cnt})")
    return collected, False


def parse_detail(pid, cate_label_):
    url = f"{BASE}/product/detail.html?product_no={pid}"
    st, h = fetch(url)
    rec = {k: "" for k in COLS}
    rec["source"] = "lecaf"
    rec["brand"] = "르까프"
    rec["category"] = cate_label_
    rec["url"] = url
    if st != 200 or not h:
        return None

    blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', h, re.S)
    obj = None
    for b in blocks:
        try:
            d = json.loads(b.strip())
        except Exception:
            continue
        if isinstance(d, dict) and d.get("@type") in ("Product", "ProductGroup"):
            obj = d
            break
    if not obj:
        return None

    name = ihtml.unescape(obj.get("name", "") or "")
    rec["name"] = name
    rec["style_code"] = style_code_from(name)
    rec["gender"] = gender_from(name)

    br = obj.get("brand")
    if isinstance(br, dict) and br.get("name"):
        rec["brand"] = br["name"]

    offers = obj.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    prices = []
    currency = ""
    colors, sizes = [], []
    for o in offers:
        if not isinstance(o, dict):
            continue
        try:
            p = float(o.get("price"))
        except (TypeError, ValueError):
            p = None
        if p is not None and p > 0:
            prices.append(p)
        currency = o.get("priceCurrency", currency) or currency
        c, s = split_variant(name, ihtml.unescape(o.get("name", "") or ""))
        if c:
            colors.append(c)
        if s:
            sizes.append(s)
    rec["color"] = pipe(colors)
    rec["sizes"] = pipe(sizes)
    if prices:
        p = min(prices)
        rec["price"] = str(int(p)) if p == int(p) else str(p)
    rec["currency"] = currency or ("KRW" if rec["price"] else "")
    # origin/material/mfg_date: 고시 is image-only on this mall -> left blank
    return rec


def load_checkpoint():
    done = {}   # product_no -> rec
    if os.path.exists(CKPT_JSONL):
        with open(CKPT_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                pid = d.get("_pid")
                if pid:
                    done[pid] = d
    return done


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    fresh = "--resume" not in sys.argv
    if fresh:
        for p in (CKPT_JSONL, CKPT_IDS):
            if os.path.exists(p):
                os.remove(p)

    print("== discovering categories ==")
    cats = discover_categories()
    print(f"found {len(cats)} live categories: " +
          ", ".join(f"{c}:{lbl or '?'}({n})" for c, lbl, n in cats))

    print("== collecting product ids (all pages) ==")
    collected, capped = collect_ids(cats)
    with open(CKPT_IDS, "w", encoding="utf-8") as f:
        json.dump(collected, f, ensure_ascii=False)
    pages_note = sum(1 for _ in cats)
    print(f"collected {len(collected)} unique product ids across {len(cats)} categories")

    done = load_checkpoint()
    print(f"resume: {len(done)} already parsed")

    n = 0
    with open(CKPT_JSONL, "a", encoding="utf-8") as ck:
        for pid, label in collected.items():
            n += 1
            if pid in done:
                continue
            try:
                rec = parse_detail(pid, label)
                if rec:
                    rec["_pid"] = pid
                    done[pid] = rec
                    ck.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    ck.flush()
                else:
                    print(f"  SKIP pid={pid}: no JSON-LD Product")
            except Exception as e:
                print(f"  ERR pid={pid}: {type(e).__name__} {e}")
            if n % 25 == 0:
                print(f"  parsed {n}/{len(collected)} (kept {len(done)})")
            time.sleep(0.15)

    # ---- final dedup by style_code (fallback to url for empty codes) ----
    rows = []
    seen_keys = set()
    for pid, rec in done.items():
        key = (rec.get("style_code") or "").strip() or ("URL:" + rec.get("url", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        rows.append({k: rec.get(k, "") for k in COLS})

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    counts = {f"{c}:{lbl}": n for c, lbl, n in cats}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"categories": counts, "unique_products": len(done),
                   "rows_after_style_dedup": len(rows), "capped": capped,
                   "rows": rows}, f, ensure_ascii=False, indent=2)

    filled = {c: sum(1 for r in rows if r.get(c)) for c in COLS}
    print("WROTE", OUT_CSV, "rows", len(rows))
    print("FILLED", json.dumps(filled, ensure_ascii=False))
    print("CATEGORIES", len(cats), "UNIQUE_PRODUCTS", len(done), "CAPPED", capped)


if __name__ == "__main__":
    main()
