#!/usr/bin/env python3
# Full (전수) product extractor for 월드컵 official mall
# (worldcupshoes.co.kr, GodoMall5). Crawls ALL categories / ALL pages to the
# last page (empty-page termination). No sample cap, no page cap.
# - unique product key = goodsNo (one goods_view page = one product)
# - final CSV dedup by style_code (모델명) but ONLY collapses non-empty codes
# - incremental write + state checkpoint => resumable
import urllib.request, urllib.parse, re, csv, time, html as ihtml, sys, json, os

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
BASE = "https://www.worldcupshoes.co.kr"
ROOT = "/Users/a1101417/Work/business-model/identity"
OUT_CSV = f"{ROOT}/outputs/extract_brand_worldcup.csv"
STATE = f"{ROOT}/outputs/_worldcup_state.json"

# Categories to union. Gender parents give the gender-hint map; 015 전체상품 is the
# nominal superset; 013/014/017 (인기/재고떨이/세일) catch items delisted from the
# main tree. detail fetch is deduped by goodsNo so extra listing pages are cheap.
CATS = [
    ("015",    ""),       # 전체상품
    ("012001", "남성"),   # 남성
    ("012002", "여성"),   # 여성
    ("012003", "아동"),   # 아동
    ("013",    ""),       # 인기상품
    ("014",    ""),       # 재고떨이
    ("017",    ""),       # 여름세일/특가
]
HARD_CAP = 5000

HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]


def fetch(url, timeout=30, retries=3):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.getcode(), r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(1.0 + attempt)
    raise last


def clean(s):
    return ihtml.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or ""))).strip()


def dl_pairs(h):
    out = {}
    for dt, dd in re.findall(r"<dt>\s*(.*?)\s*</dt>\s*<dd[^>]*>(.*?)</dd>", h, re.S):
        k = ihtml.unescape(re.sub(r"<[^>]+>", "", dt)).strip()
        v = clean(dd)
        if k and k not in out:
            out[k] = v
    return out


def gosi_pairs(h):
    i = h.find("상품필수 정보")
    if i < 0:
        return {}, False
    seg = h[i:i + 4000]
    out = {}
    for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", seg, re.S):
        k = ihtml.unescape(re.sub(r"<[^>]+>", "", th)).strip()
        v = clean(td)
        if k and k not in out:
            out[k] = v
    return out, True


def collect_goods():
    """Crawl every listing page of every category to the last (empty) page.
    Returns (ordered unique goodsNo list, gender_hint map, pages_fetched, per_cat_contrib)."""
    order, ghint = [], {}
    seen = set()
    pages_fetched = 0
    contrib = {}
    for code, gender in CATS:
        before = len(order)
        page = 1
        while True:
            url = f"{BASE}/goods/goods_list.php?cateCd={code}&page={page}"
            try:
                st, h = fetch(url)
            except Exception as e:
                print(f"list err {code} p{page}: {e}", file=sys.stderr)
                break
            pages_fetched += 1
            if st != 200:
                print(f"list {code} p{page} status {st}", file=sys.stderr)
                break
            ids = list(dict.fromkeys(re.findall(r"goodsNo=([0-9]+)", h)))
            # detect empty/last page: no goods listed
            page_new = 0
            for g in ids:
                if g not in seen:
                    seen.add(g)
                    order.append(g)
                    ghint.setdefault(g, gender)
                    page_new += 1
                elif gender and not ghint.get(g):
                    ghint[g] = gender
            if not ids:
                break  # empty page -> past the last page
            page += 1
            time.sleep(0.35)
            if page > 2000:  # safety against infinite loop
                print(f"safety stop {code} at page {page}", file=sys.stderr)
                break
        contrib[code] = len(order) - before
        print(f"  cate {code}: +{contrib[code]} new (total {len(order)}), pages~{page}", file=sys.stderr)
    return order, ghint, pages_fetched, contrib


def parse_detail(goodsNo, gender_hint, h):
    dl = dl_pairs(h)
    gz, gz_present = gosi_pairs(h)

    ot = re.search(r'og:title"\s+content="([^"]*)"', h)
    title = ihtml.unescape(ot.group(1)).strip() if ot else ""
    name, color_title = title, ""
    if " / " in title:
        parts = [p.strip() for p in title.split(" / ")]
        name = parts[0]
        color_title = parts[-1] if len(parts) > 1 else ""
    color = gz.get("색상") or color_title

    brand = dl.get("브랜드") or "월드컵"
    style_code = dl.get("모델명") or ""
    origin = dl.get("원산지") or gz.get("제조국") or ""
    material = gz.get("제품 주소재") or gz.get("주소재") or ""

    desc = dl.get("짧은설명") or ""
    gender = ""
    if re.search(r"남녀|공용|남여", desc):
        gender = "공용"
    elif "여성" in desc:
        gender = "여성"
    elif "남성" in desc:
        gender = "남성"
    elif "아동" in desc or "키즈" in desc:
        gender = "아동"
    if not gender:
        gender = gender_hint or ""
    category = re.sub(r"^(남성|여성|아동|남녀공용|남여공용|공용|남녀|남여)\s*", "", desc).strip()

    fp = re.search(r'set_goods_fixedPrice"[^>]*value="([0-9.]+)"', h)
    price = ""
    if fp:
        try:
            price = str(int(float(fp.group(1))))
        except ValueError:
            price = ""

    sizes = []
    for v in re.findall(r'data-option-value="([^"]*)"', h):
        if "^|^" in v:
            sz = v.split("^|^")[-1].strip()
            if sz and sz not in sizes:
                sizes.append(sz)
    if not sizes:
        for sz in re.split(r"[,/]", gz.get("치수", "")):
            sz = sz.strip()
            if sz and sz not in sizes:
                sizes.append(sz)

    mfg = gz.get("제조연월") or gz.get("제조년월") or ""

    row = {
        "source": "worldcup", "brand": brand, "style_code": style_code,
        "name": name, "color": color, "price": price, "currency": "KRW",
        "category": category, "gender": gender, "sizes": "|".join(sizes),
        "origin": origin, "material": material, "mfg_date": mfg,
        "url": f"{BASE}/goods/goods_view.php?goodsNo={goodsNo}",
    }
    return row


def load_resume():
    """Rebuild seen sets from an existing partial CSV (resume support)."""
    seen_goods, seen_styles, nrows = set(), set(), 0
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                nrows += 1
                m = re.search(r"goodsNo=([0-9]+)", r.get("url", ""))
                if m:
                    seen_goods.add(m.group(1))
                sc = (r.get("style_code") or "").strip()
                if sc:
                    seen_styles.add(sc)
    return seen_goods, seen_styles, nrows


def main():
    resume = os.environ.get("RESUME") == "1"

    # Phase 1: collect goodsNo (reuse cached state if resuming)
    if resume and os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            stt = json.load(f)
        ids, ghint = stt["ids"], stt["ghint"]
        pages_fetched, contrib = stt["pages_fetched"], stt["contrib"]
        print(f"resume: cached {len(ids)} goodsNo", file=sys.stderr)
    else:
        ids, ghint, pages_fetched, contrib = collect_goods()
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump({"ids": ids, "ghint": ghint, "pages_fetched": pages_fetched,
                       "contrib": contrib}, f)
    print(f"collected {len(ids)} unique goodsNo over {pages_fetched} listing pages",
          file=sys.stderr)
    print(f"per-category new contribution: {contrib}", file=sys.stderr)

    # Phase 2: incremental detail crawl + write
    if resume:
        seen_goods, seen_styles, nrows = load_resume()
        mode = "a"
        print(f"resume: {nrows} rows, {len(seen_goods)} goods already done", file=sys.stderr)
    else:
        seen_goods, seen_styles, nrows = set(), set(), 0
        mode = "w"

    empty_style = 0
    capped = False
    with open(OUT_CSV, mode, encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        if mode == "w":
            w.writeheader()
        for n, g in enumerate(ids, 1):
            if g in seen_goods:
                continue
            if nrows >= HARD_CAP:
                capped = True
                break
            url = f"{BASE}/goods/goods_view.php?goodsNo={g}"
            try:
                st, h = fetch(url)
            except Exception as e:
                print(f"detail err {g}: {e}", file=sys.stderr)
                continue
            if st != 200:
                print(f"detail {g} status {st}", file=sys.stderr)
                continue
            row = parse_detail(g, ghint.get(g, ""), h)
            seen_goods.add(g)
            sc = (row["style_code"] or "").strip()
            # dedup: collapse only NON-EMPTY style_codes; keep every empty one
            if sc:
                if sc in seen_styles:
                    continue
                seen_styles.add(sc)
            else:
                empty_style += 1
            w.writerow(row)
            nrows += 1
            f.flush()
            if n % 25 == 0:
                print(f"  {n}/{len(ids)} processed, {nrows} rows written", file=sys.stderr)
            time.sleep(0.3)

    # verification: read back actual data-row count from the written CSV
    after = 0
    with open(OUT_CSV, encoding="utf-8-sig", newline="") as f:
        for _ in csv.DictReader(f):
            after += 1
    print(f"DONE rows_in_csv={after} pages={pages_fetched} empty_style_code={empty_style} capped={capped}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
