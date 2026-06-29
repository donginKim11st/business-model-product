#!/usr/bin/env python3
# FULL CENSUS product extractor for 프로스펙스 official mall (prospecs.com, LS네트웍스, 자체 .do)
# enumeration = internal JSON (/product.do?cmd=getProductAjaxList, POST form), detail = DOM + 고시 info-table
#
# 전수(census) NOTES:
#   GNB_ID=1000000 ("전체") is the master superset of the whole catalog (TOT_CNT=776).
#   Every type-category (신발 1010000 / 아우터 1020000 / 상의 1030000 / 하의 1040000 / 액세서리 1050000),
#   every sport-category (러닝 1060000 / 야구 1070000 / 축구 1080000 / 농구 1090000) and 기타(1100000)
#   are proven strict subsets of 1000000 (not-in-master == 0). The old 5-category union was only 528,
#   missing 248 products, so we enumerate 1000000 alone for a true census.
#   Rows are per-colorway (one per PROD_CD); style_code is a derived NON-UNIQUE column.
import urllib.request, urllib.parse, urllib.error, re, json, csv, time, html as ihtml, os, sys

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
BASE = "https://www.prospecs.com"
LIST_EP = BASE + "/product.do?cmd=getProductAjaxList"
DETAIL_EP = BASE + "/product.do?cmd=getProductDetail&PROD_CD="
ROOT = "/Users/a1101417/Work/business-model/identity"
OUT_CSV = ROOT + "/outputs/extract_brand_prospecs.csv"
OUT_JSON = ROOT + "/outputs/extract_brand_prospecs.json"
STATE_JSON = ROOT + "/outputs/_prospecs_state.json"  # resume checkpoint (enumeration)

MASTER_GNB = "1000000"   # 전체 / ALL  (superset of every category)
PAGE_SIZE = 32
HARD_CAP = 5000          # stop enumerating beyond this; note it
COLS = ["source", "brand", "style_code", "name", "color", "price", "currency",
        "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]


# ----------------------------------------------------------------------------- net
def _open(url, data=None, referer=None, timeout=30):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    headers = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    body = None
    if data is not None:
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        body = urllib.parse.urlencode(data).encode("utf-8")
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(u, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch(url, data=None, referer=None, timeout=30, retries=3):
    last = None
    for i in range(retries):
        try:
            return _open(url, data=data, referer=referer, timeout=timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            last = e
            time.sleep(0.8 * (i + 1))
    raise last


# ----------------------------------------------------------------------------- helpers
def pipe(seq):
    out = []
    for v in seq:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    return "|".join(out)


def style_root(prod_cd):
    # PROD_CD = style root (7ch) + colorway suffix [A-Z]\d{3}  e.g. PH0US26S104 -> PH0US26
    if not prod_cd:
        return ""
    m = re.sub(r'[A-Z]\d{3}$', '', prod_cd)
    return m if m and m != prod_cd else prod_cd


def gender_from(name):
    n = name or ""
    if re.search(r'남여공용|남녀공용|남여|남녀|공용|UNISEX|unisex', n):
        return "공용"
    has_m = bool(re.search(r'남성|\bmen\b|\bMEN\b|\bMan\b', n))
    has_w = bool(re.search(r'여성|\bwomen\b|\bWOMEN\b|\bWoman\b', n))
    if has_m and has_w:
        return "공용"
    if has_w:
        return "여성"
    if has_m:
        return "남성"
    return ""


def strip_tags(s):
    return ihtml.unescape(re.sub(r'<[^>]+>', '', s or '')).strip()


def parse_gosi(html):
    """info-table th/td -> dict of 고시 fields (all text on this mall)."""
    out = {}
    it = re.search(r'<table class="info-table">(.*?)</table>', html, re.S)
    if not it:
        return out
    for k, v in re.findall(r'<th[^>]*>(.*?)</th>\s*<td>(.*?)</td>', it.group(1), re.S):
        out[strip_tags(k)] = strip_tags(v)
    return out


def fmt_mfg(raw):
    raw = (raw or "").strip()
    if re.fullmatch(r'\d{8}', raw):
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    if re.fullmatch(r'\d{6}', raw):
        return f"{raw[0:4]}-{raw[4:6]}"
    return raw


# ----------------------------------------------------------------------------- detail
def parse_detail(prod_cd, fallback_cat):
    url = DETAIL_EP + prod_cd
    h = fetch(url, referer=BASE + "/display.do?cmd=mallMain")
    rec = {k: "" for k in COLS}
    rec["source"] = "prospecs"
    rec["brand"] = "프로스펙스"
    # style_code = 전체 PROD_CD(컬러웨이 단위 고유). 과거 style_root(7자 절단)은
    # 서로 다른 모델(S1/S2/S3...)을 한 코드로 뭉쳐 이름·가격이 어긋났음 → 전체 코드 사용.
    rec["style_code"] = prod_cd
    rec["currency"] = "KRW"
    rec["url"] = url

    # slice from buy-area to avoid JS string-template noise (cart/rec templates carry prd-title too)
    bi = h.find('<div class="buy-area">')
    buy = h[bi:] if bi >= 0 else h

    # name: first prd-title after breadcrumb </ul> inside buy-area
    m = re.search(r'</ul>\s*<p class="prd-title">\s*(.*?)\s*</p>', buy, re.S)
    if not m:
        m = re.search(r'<p class="prd-title">\s*(.*?)\s*</p>', buy, re.S)
    name = strip_tags(m.group(1)) if m else ""
    rec["name"] = name
    rec["gender"] = gender_from(name)

    # category: breadcrumb (신발 > 헤리티지); fallback to list ITEMKIND
    bc = re.search(r'<ul class="breadcrumb">(.*?)</ul>', h, re.S)
    if bc:
        crumbs = [strip_tags(x) for x in re.findall(r'>([^<>]+)</a>', bc.group(1))]
        crumbs = [c for c in crumbs if c and c not in ("HOME", "홈")]
        rec["category"] = " > ".join(crumbs)
    if not rec["category"]:
        rec["category"] = fallback_cat or ""

    # price: sale-price within buy-area (fallback og:price)
    m = re.search(r'class="sale-price">\s*([\d,]+)\s*원', buy)
    if m:
        rec["price"] = m.group(1).replace(",", "")
    else:
        mo = re.search(r'<meta property="og:price"\s*content="([\d.]+)"', h)
        if mo:
            rec["price"] = mo.group(1)

    # sizes: size-btn-wrap button labels (include disabled = out of stock but valid size)
    sw = re.search(r'<div class="size-btn-wrap">(.*?)</div>', buy, re.S)
    sizes = []
    if sw:
        for lab in re.findall(r'<button[^>]*>\s*([^<]+?)\s*</button>', sw.group(1)):
            sizes.append(lab.strip())
    rec["sizes"] = pipe(sizes)

    # 고시 info-table (text)
    g = parse_gosi(h)
    rec["material"] = g.get("제품소재", "")
    rec["origin"] = g.get("제조국", "")
    rec["mfg_date"] = fmt_mfg(g.get("제조연월", ""))
    # color: prefer info-table 색상 (clean, colorway-specific), fallback opt-title
    rec["color"] = g.get("색상", "")
    if not rec["color"]:
        mc = re.search(r'<p class="opt-title">\s*([^<]+?)\s*</p>\s*<div class="prd-color-btn-wrap">', buy, re.S)
        if mc:
            rec["color"] = mc.group(1).strip()
    return rec


# ----------------------------------------------------------------------------- enumerate
def list_page(gnb, page):
    h = fetch(LIST_EP, data={"GNB_ID": gnb, "SORTING_TYPE": "MD_RCMD_REG",
                             "CURRENT_PAGE": str(page), "SEARCH_YN": "Y"},
              referer=BASE + "/display.do?cmd=categoryMain&GNB_ID=" + gnb)
    d = json.loads(h)
    return d.get("TOT_CNT"), (d.get("dataList") or [])


def enumerate_all():
    """Walk GNB 1000000 (전체) to the last page. Returns (ordered list of [PROD_CD, cat_fallback], tot_cnt, pages)."""
    seen = set()
    ordered = []
    tot = None
    page = 1
    pages = 0
    while True:
        t, items = list_page(MASTER_GNB, page)
        if page == 1:
            tot = t
        if not items:
            break
        pages = page
        for it in items:
            cd = it.get("PROD_CD")
            if not cd or cd in seen:
                continue
            seen.add(cd)
            cat = " > ".join([x for x in [it.get("ITEMKIND_NM_FIRST"),
                                          it.get("ITEMKIND_NM_SECOND"),
                                          it.get("ITEMKIND_NM_THIRD")] if x and str(x).strip()])
            ordered.append([cd, cat])
            if len(ordered) >= HARD_CAP:
                break
        if len(ordered) >= HARD_CAP:
            break
        if len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.15)
    return ordered, tot, pages


# ----------------------------------------------------------------------------- main
def load_done_prodcds():
    """Resume: PROD_CDs already written to OUT_CSV (parsed from url column)."""
    done = set()
    if not os.path.exists(OUT_CSV):
        return done
    try:
        with open(OUT_CSV, newline="", encoding="utf-8-sig") as f:
            r = csv.DictReader(f)
            if r.fieldnames != COLS:
                return None  # header mismatch -> caller should start fresh
            for row in r:
                m = re.search(r'PROD_CD=([A-Za-z0-9]+)', row.get("url", ""))
                if m:
                    done.add(m.group(1))
    except Exception:
        return None
    return done


def main():
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)

    # ---- enumeration (with checkpoint) ----
    state = None
    if os.path.exists(STATE_JSON):
        try:
            state = json.load(open(STATE_JSON, encoding="utf-8"))
        except Exception:
            state = None
    if state and state.get("gnb") == MASTER_GNB and state.get("enum"):
        ordered = state["enum"]
        tot = state.get("tot")
        pages = state.get("pages")
        capped = state.get("capped", False)
        print(f"RESUME enum from checkpoint: {len(ordered)} PROD_CDs (TOT_CNT={tot}, pages={pages})")
    else:
        print("ENUMERATE GNB", MASTER_GNB, "(전체)...")
        ordered, tot, pages = enumerate_all()
        capped = len(ordered) >= HARD_CAP
        state = {"gnb": MASTER_GNB, "tot": tot, "pages": pages,
                 "capped": capped, "enum": ordered}
        json.dump(state, open(STATE_JSON, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"ENUMERATED {len(ordered)} unique PROD_CDs (TOT_CNT={tot}, pages={pages}, capped={capped})")

    # ---- resume / fresh decision ----
    done = load_done_prodcds()
    fresh = done is None
    if fresh:
        done = set()
        with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=COLS).writeheader()
        print("FRESH start: wrote header to", OUT_CSV)
    else:
        print(f"RESUME detail: {len(done)} rows already present, continuing append")

    # ---- detail pass (append per product) ----
    todo = [(cd, cat) for cd, cat in ordered if cd not in done]
    print(f"DETAIL pass: {len(todo)} to fetch / {len(ordered)} enumerated")
    written = len(done)
    failed = []
    for n, (cd, cat) in enumerate(todo, 1):
        try:
            rec = parse_detail(cd, cat)
            if rec and rec.get("name"):
                with open(OUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
                    csv.DictWriter(f, fieldnames=COLS).writerow(rec)
                written += 1
            else:
                failed.append(cd)
                print(f"  SKIP {cd}: no name")
        except Exception as e:
            failed.append(cd)
            print(f"  ERR {cd}: {type(e).__name__} {e}")
        if n % 50 == 0:
            print(f"  ... {n}/{len(todo)} (written total={written})")
        time.sleep(0.15)

    # ---- one retry round for failures ----
    if failed:
        print(f"RETRY {len(failed)} failures...")
        retry = failed
        failed = []
        cat_of = {cd: cat for cd, cat in ordered}
        for cd in retry:
            try:
                rec = parse_detail(cd, cat_of.get(cd, ""))
                if rec and rec.get("name"):
                    with open(OUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
                        csv.DictWriter(f, fieldnames=COLS).writerow(rec)
                    written += 1
                else:
                    failed.append(cd)
            except Exception as e:
                failed.append(cd)
                print(f"  RETRY ERR {cd}: {type(e).__name__} {e}")
            time.sleep(0.3)

    # ---- summary ----
    summary = {"gnb": MASTER_GNB, "tot_cnt": tot, "pages": pages,
               "enumerated": len(ordered), "written_rows": written,
               "failed": failed, "capped": capped, "method": "urllib"}
    json.dump(summary, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("WROTE", OUT_CSV, "rows", written, "failed", len(failed))
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
