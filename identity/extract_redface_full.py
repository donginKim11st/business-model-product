#!/usr/bin/env python3
# Exhaustive (전수) product extractor for 레드페이스 official mall (theredface.com, cafe24, hook=jsonld).
# Crawls ALL pages of every top shopping category until empty page, per-product resumable checkpoint,
# dedup by style_code (fallback product_no). Order: 24->25->26->27->28(OUTLET last) so main-catalog wins.
import urllib.request, urllib.parse, re, json, csv, time, html as ihtml, os

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BASE = "https://www.theredface.com"
ROOT = "/Users/a1101417/Work/business-model/identity/outputs"
OUT_CSV = os.path.join(ROOT, "extract_brand_redface.csv")
OUT_JSON = os.path.join(ROOT, "extract_brand_redface.json")
CKPT = os.path.join(ROOT, "_redface_ckpt.jsonl")   # per-product append checkpoint (resume)

# top shopping categories (OUTLET 28 LAST so the main-catalog full-price entry wins first-seen)
CATS = [(24, "MAN남성"), (25, "WOMAN여성"), (26, "SHOES신발"), (27, "EQUIPMENT용품"), (28, "OUTLET아울렛")]
HARD_CAP = 5000
COLS = ["source", "brand", "style_code", "name", "color", "price", "currency",
        "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]


def fetch(url, timeout=30, retries=3):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(1.0 + attempt)
    raise last


def notice(h, key):
    m = re.search(r'<tr class="\s*' + re.escape(key) + r'\s+xans-record-">.*?<td>(.*?)</td>', h, re.S)
    if not m:
        return ""
    txt = re.sub(r'<[^>]+>', '', m.group(1))
    txt = ihtml.unescape(txt)
    return re.sub(r'\s+', ' ', txt).strip()


def pipe(v):
    if not v:
        return ""
    parts = [p.strip() for p in re.split(r'\s*[/,]\s*', v) if p.strip()]
    return "|".join(dict.fromkeys(parts))


def style_code_from(name, slug):
    if name:
        toks = name.split()
        for t in reversed(toks):
            t2 = t.strip()
            if re.fullmatch(r'[A-Za-z][A-Za-z0-9\-]{3,}', t2) and re.search(r'\d', t2):
                return t2.upper()
    if slug:
        seg = slug.rstrip('/').split('/')[0]
        m = re.search(r'-([A-Za-z]{2,}[A-Za-z0-9]*\d[A-Za-z0-9]*)$', seg)
        if m:
            return m.group(1).upper()
    return ""


def parse_detail(pid, cate_label):
    url = f"{BASE}/product/detail.html?product_no={pid}"
    st, h = fetch(url)
    rec = {k: "" for k in COLS}
    rec["source"] = "redface"
    rec["brand"] = "레드페이스"
    rec["category"] = cate_label
    rec["url"] = url
    name = ""; price = ""; currency = ""
    for b in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', h, re.S):
        try:
            d = json.loads(b.strip())
        except Exception:
            continue
        if isinstance(d, dict) and d.get("@type") in ("Product", "ProductGroup"):
            name = d.get("name", "") or name
            offers = d.get("offers") or []
            if isinstance(offers, dict):
                offers = [offers]
            prices = []
            for o in offers:
                if isinstance(o, dict) and o.get("price") not in (None, ""):
                    try:
                        prices.append(float(o["price"]))
                    except Exception:
                        pass
                    currency = o.get("priceCurrency", currency)
            if prices:
                p = min(prices)
                price = str(int(p)) if p == int(p) else str(p)
            break
    n_name = notice(h, "상품명")
    rec["name"] = name or n_name
    rec["style_code"] = style_code_from(rec["name"] or n_name, "")
    rec["gender"] = notice(h, "성별")
    rec["material"] = notice(h, "소재")
    rec["color"] = pipe(notice(h, "색상"))
    rec["sizes"] = pipe(notice(h, "치수"))
    rec["origin"] = notice(h, "제조국")
    md = notice(h, "제조연월")
    if re.fullmatch(r'\d{8}', md):
        md = f"{md[:4]}-{md[4:6]}-{md[6:]}"
    elif re.fullmatch(r'\d{6}', md):
        md = f"{md[:4]}-{md[4:]}"
    rec["mfg_date"] = md
    if not price:
        sp = notice(h, "판매가")
        m = re.search(r'([\d,]+)', sp)
        if m:
            price = m.group(1).replace(",", "")
    rec["price"] = price
    rec["currency"] = currency or ("KRW" if price else "")
    rec["_pid"] = str(pid)
    return rec


def list_ids(cate, page):
    st, h = fetch(f"{BASE}/product/list.html?cate_no={cate}&page={page}")
    ids = []
    for m in re.findall(r'/product/[^"\']*?/(\d+)/category/', h):
        if m not in ids:
            ids.append(m)
    cnt = ""
    mc = re.search(r'prdCount[^0-9]*([\d,]+)', h)
    if mc:
        cnt = mc.group(1).replace(",", "")
    return ids, cnt


def collect_ids():
    """Return ordered [(pid,label)], per-cat prdCount dict, per-cat collected dict, total pages fetched."""
    order = []          # (pid, label) first-seen order
    seen = set()
    prdcounts = {}
    collected_per_cat = {}
    pages_fetched = 0
    # NOTE: theredface paginates with GAPS (e.g. WOMEN has empty pages 10-11 then resumes
    # 12-16). So do NOT stop at the first empty page. Continue until prdCount reached + an
    # empty page, OR EMPTY_STREAK consecutive empties, OR a hard page ceiling.
    EMPTY_STREAK = 8
    PAGE_CEIL = 120
    for cate, label in CATS:
        page = 1
        cat_ids = set()
        empty_streak = 0
        while True:
            ids, cnt = list_ids(cate, page)
            pages_fetched += 1
            if page == 1:
                prdcounts[label] = cnt
            try:
                target = int(prdcounts.get(label))
            except (TypeError, ValueError):
                target = None
            if not ids:
                empty_streak += 1
            else:
                empty_streak = 0
                for i in ids:
                    cat_ids.add(i)
                    if i not in seen:
                        seen.add(i)
                        order.append((i, label))
            # stop: got everything prdCount promised and hit a blank page boundary
            if target is not None and len(cat_ids) >= target and empty_streak >= 1:
                break
            if empty_streak >= EMPTY_STREAK:
                break
            if page >= PAGE_CEIL:
                break
            page += 1
            time.sleep(0.18)
        collected_per_cat[label] = len(cat_ids)
        print(f"  [{label}] cate_no={cate} pages_scanned={page} collected={len(cat_ids)} prdCount={prdcounts.get(label)}")
    return order, prdcounts, collected_per_cat, pages_fetched


def load_ckpt():
    done = {}
    if os.path.exists(CKPT):
        with open(CKPT, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("_pid"):
                    done[r["_pid"]] = r
    return done


def main():
    os.makedirs(ROOT, exist_ok=True)
    print("Collecting product ids across all categories/pages ...")
    order, prdcounts, collected_per_cat, pages_fetched = collect_ids()
    print(f"TOTAL unique product ids collected: {len(order)}; list pages fetched: {pages_fetched}")

    # ---- gate #1: per-category collected vs prdCount ----
    count_mismatch = {}
    for _, label in CATS:
        want = prdcounts.get(label)
        got = collected_per_cat.get(label, 0)
        try:
            wantn = int(want)
        except (TypeError, ValueError):
            wantn = None
        if wantn is not None and wantn != got:
            count_mismatch[label] = {"prdCount": wantn, "collected": got}
    if count_mismatch:
        print("WARN per-category count mismatch:", json.dumps(count_mismatch, ensure_ascii=False))
    else:
        print("OK per-category collected == prdCount for all categories")

    # ---- resume from checkpoint, parse details (append per product) ----
    done = load_ckpt()
    print(f"checkpoint has {len(done)} already-parsed products")
    capped = False
    parsed_total = len(done)
    ckf = open(CKPT, "a", encoding="utf-8")
    try:
        for n, (pid, label) in enumerate(order, 1):
            if pid in done:
                continue
            if parsed_total >= HARD_CAP:
                capped = True
                print(f"HARD_CAP {HARD_CAP} reached; stopping detail crawl")
                break
            try:
                rec = parse_detail(pid, label)
                done[pid] = rec
                ckf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                ckf.flush()
                parsed_total += 1
                if parsed_total % 25 == 0:
                    print(f"  parsed {parsed_total} (last pid={pid} {rec['style_code']} {rec['name'][:24]})")
            except Exception as e:
                print(f"  ERR pid={pid}: {type(e).__name__} {e}")
            time.sleep(0.18)
    finally:
        ckf.close()

    # ---- dedup by style_code (fallback product_no); first-seen wins (order = main-catalog first) ----
    rows = []
    by_key = {}
    sc_to_pids = {}   # style_code -> set of distinct pids (for collision audit)
    for pid, label in order:
        rec = done.get(pid)
        if not rec:
            continue
        sc = rec.get("style_code", "")
        if sc:
            sc_to_pids.setdefault(sc, set()).add(pid)
        key = sc if sc else ("__pid_" + pid)
        if key in by_key:
            continue
        by_key[key] = True
        rows.append(rec)

    # ---- write final outputs ----
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"prdcounts": prdcounts, "collected_per_cat": collected_per_cat,
                   "pages_fetched": pages_fetched, "unique_ids": len(order),
                   "parsed": len(done), "deduped_rows": len(rows),
                   "count_mismatch": count_mismatch, "capped": capped,
                   "rows": [{k: r.get(k, "") for k in COLS} for r in rows]},
                  f, ensure_ascii=False, indent=2)

    # ---- audits ----
    filled = {c: sum(1 for r in rows if r.get(c)) for c in COLS}
    sc_fill_by_cat = {}
    cat_tot = {}
    for r in rows:
        cat_tot[r["category"]] = cat_tot.get(r["category"], 0) + 1
        if r.get("style_code"):
            sc_fill_by_cat[r["category"]] = sc_fill_by_cat.get(r["category"], 0) + 1
    collisions = {sc: sorted(p) for sc, p in sc_to_pids.items() if len(p) >= 2}

    print("WROTE", OUT_CSV, "deduped_rows", len(rows))
    print("FILLED", json.dumps(filled, ensure_ascii=False))
    print("STYLE_CODE_FILL_BY_CAT", json.dumps({k: f"{sc_fill_by_cat.get(k,0)}/{cat_tot[k]}" for k in cat_tot}, ensure_ascii=False))
    print("STYLE_CODE_COLLISIONS (>=2 distinct pids):", len(collisions))
    for sc, pids in list(collisions.items())[:25]:
        nms = [done[p]["name"][:30] for p in pids if p in done]
        print(f"   {sc}: pids={pids} names={nms}")
    print("PAGES_FETCHED", pages_fetched, "CAPPED", capped, "MISMATCH", count_mismatch)


if __name__ == "__main__":
    main()
