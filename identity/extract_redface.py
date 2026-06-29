#!/usr/bin/env python3
# Server-side product sample extractor for 레드페이스 official mall (cafe24, hook=jsonld)
import urllib.request, urllib.parse, re, json, csv, time, html as ihtml, os

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BASE = "https://www.theredface.com"
OUT_CSV = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_redface.csv"
OUT_JSON = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_redface.json"

# GNB top categories -> human label
CATS = {24: "MAN남성", 25: "WOMAN여성", 26: "SHOES신발", 27: "EQUIPMENT용품"}
TARGET = 120          # sample cap
MAX_PAGES = 5         # per category

def fetch(url, timeout=30):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")

def notice(h, key):
    m = re.search(r'<tr class="\s*' + re.escape(key) + r'\s+xans-record-">.*?<td>(.*?)</td>', h, re.S)
    if not m:
        return ""
    txt = re.sub(r'<[^>]+>', '', m.group(1))
    txt = ihtml.unescape(txt)
    return re.sub(r'\s+', ' ', txt).strip()

def pipe(v):
    # "블랙/네이비" or "095/100/105" -> "블랙|네이비"
    if not v:
        return ""
    parts = [p.strip() for p in re.split(r'\s*[/,]\s*', v) if p.strip()]
    return "|".join(dict.fromkeys(parts))  # dedupe, keep order

def style_code_from(name, slug):
    # model code = trailing alnum token w/ a digit (e.g. REWMJKMBF310)
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
    rec = {k: "" for k in ["source","brand","style_code","name","color","price","currency",
                            "category","gender","sizes","origin","material","mfg_date","url"]}
    rec["source"] = "redface"
    rec["brand"] = "레드페이스"
    rec["category"] = cate_label
    rec["url"] = url
    # JSON-LD Product
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
    # notice table (상품정보제공고시) — authoritative attrs
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
    # price fallback from notice 판매가
    if not price:
        sp = notice(h, "판매가")
        m = re.search(r'([\d,]+)', sp)
        if m:
            price = m.group(1).replace(",", "")
    rec["price"] = price
    rec["currency"] = currency or ("KRW" if price else "")
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

def main():
    counts = {}
    # collect ids interleaved across categories for diversity
    collected = []  # (id, cate_label)
    seen = set()
    for page in range(1, MAX_PAGES + 1):
        for cate, label in CATS.items():
            ids, cnt = list_ids(cate, page)
            if page == 1:
                counts[label] = cnt
            for i in ids:
                if i not in seen:
                    seen.add(i)
                    collected.append((i, label))
            time.sleep(0.25)
        if len(collected) >= TARGET:
            break
    collected = collected[:TARGET]
    print(f"collected {len(collected)} unique product ids; counts={counts}")

    rows = []
    for n, (pid, label) in enumerate(collected, 1):
        try:
            rec = parse_detail(pid, label)
            rows.append(rec)
            if n % 20 == 0:
                print(f"  parsed {n}/{len(collected)} (last={rec['style_code']} {rec['name'][:24]})")
        except Exception as e:
            print(f"  ERR pid={pid}: {type(e).__name__} {e}")
        time.sleep(0.2)

    cols = ["source","brand","style_code","name","color","price","currency",
            "category","gender","sizes","origin","material","mfg_date","url"]
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"counts": counts, "rows": rows}, f, ensure_ascii=False, indent=2)

    # verification summary
    filled = {c: sum(1 for r in rows if r.get(c)) for c in cols}
    print("WROTE", OUT_CSV, "rows", len(rows))
    print("FILLED", json.dumps(filled, ensure_ascii=False))
    print("EST_TOTAL", counts)

if __name__ == "__main__":
    main()
