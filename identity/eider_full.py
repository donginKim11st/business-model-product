#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FULL (전수) extraction of the EIDER (아이더) official mall on K.VILLAGE.

Spine  : POST /search/result  with searchBrandLCode=1008 (whole brand), paginated
         to the empty page  -> every color-SKU (goodsCd).  ~2426 SKUs.
         (The 6 medium categories only cover ~743, so they are NOT the spine; they
          are used only to build a best-effort goodsCd -> category map.)
Detail : GET /goods/api/gvnt/{goodsCd}  (상품정보제공고시: material/origin/mfg_date
         /color/sizes).  Fetched in parallel, resumable via a JSONL progress file.

Output : outputs/extract_brand_eider.csv   (overwrite, utf-8-sig)
Header  : source,brand,style_code,name,color,price,currency,category,gender,sizes,
          origin,material,mfg_date,url
dedup by style_code (goodsCd). source="eider".
"""
import csv, json, os, re, time, threading
import urllib.request, urllib.error, urllib.parse
from http.cookiejar import CookieJar
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://www.k-village.co.kr"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "outputs", "extract_brand_eider.csv")
LIST_JSONL = os.path.join(HERE, "outputs", "_eider_list.jsonl")     # per-page append
GVNT_JSONL = os.path.join(HERE, "outputs", "_eider_gvnt.jsonl")     # resumable detail
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

CAP = 5000            # hard cap on rows; brand is ~2426 so this should not trigger
PAGE = 100            # list page size
WORKERS = 8           # parallel gvnt fetches

# product-type medium categories (searchBrandMCode) -> readable label.
# These cover only ~743 of ~2426 SKUs (mostly current-season, no OUTLET);
# remaining SKUs fall back to a keyword classifier on the goods name.
CATEGORIES = [
    ("1037", "자켓/아우터"),
    ("1038", "상의"),
    ("1040", "하의"),
    ("1042", "신발"),
    ("1043", "가방"),
    ("1044", "모자/액세서리"),
]

cj = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
_lock = threading.Lock()


def q(url):
    return urllib.parse.quote(url, safe=":/?=&%#+,")


def http_get(url, opn=None, tries=4):
    o = opn or opener
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(q(url), headers={
                "User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                "Referer": BASE + "/eider",
                "Accept": "application/json, text/html, */*"})
            with o.open(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(0.4 * (i + 1))
    raise last


def http_post_json(url, body, csrf, tries=4):
    last = None
    for i in range(tries):
        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(q(url), data=data, headers={
                "User-Agent": UA, "Content-Type": "application/json; charset=utf-8",
                "X-CSRF-TOKEN": csrf, "X-Requested-With": "XMLHttpRequest",
                "Referer": BASE + "/eider", "Accept": "application/json"}, method="POST")
            with opener.open(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(0.5 * (i + 1))
    raise last


def get_csrf():
    html = http_get(BASE + "/eider")
    m = re.search(r'name="_csrf"\s+content="([^"]+)"', html)
    return m.group(1) if m else None


def clean_sizes(raw):
    if not raw:
        return ""
    parts = []
    for tok in raw.split(","):
        tok = re.sub(r"\[[^\]]*\]", "", tok).strip()
        if tok:
            parts.append(tok)
    return "|".join(parts)


def gender_of(goods_nm, goods_cd):
    nm = goods_nm or ""
    if re.search(r"여성|여아|우먼|WOMEN", nm, re.I):
        return "여성"
    if re.search(r"남성|남아|MEN", nm, re.I):
        return "남성"
    if re.search(r"아동|키즈|주니어|KIDS|JR", nm, re.I):
        return "아동"
    c = (goods_cd or "  ")[1:2].upper()
    return {"M": "남성", "W": "여성", "U": "공용", "K": "아동"}.get(c, "")


def color_from_dspy(pc_dspy):
    if pc_dspy:
        m = re.search(r"\(([^()]+)\)\s*$", pc_dspy.strip())
        if m:
            return m.group(1).strip()
    return ""


# keyword classifier (fallback when goods is not in the medium-category map)
_CAT_RULES = [
    ("신발",        r"슬라이드|샌들|샌달|슬리퍼|부츠|운동화|등산화|트레킹화|런닝화|러닝화|스니커|아쿠아슈즈|[가-힣A-Za-z]화\b|슈즈|SHOES|SANDAL|SLIDE"),
    ("가방",        r"백팩|배낭|가방|크로스백|숄더백|토트|힙색|웨이스트백|파우치|더플|색\b|BAG|PACK"),
    ("모자/액세서리", r"모자|캡|비니|버킷|바이저|장갑|양말|벨트|스카프|머플러|넥워머|넥게이터|토시|아대|밴드|타올|타월|용품|매트|텐트|체어|의자|랜턴|스틱|아이젠|게이터|PET|CAP|HAT|GLOVE|SOCK"),
    ("하의",        r"팬츠|바지|쇼츠|반바지|레깅스|스커트|치마|슬랙스|PANT|SHORT|LEGGING|SKIRT"),
    ("자켓/아우터",  r"자켓|재킷|아우터|베스트|조끼|점퍼|코트|패딩|다운|바람막이|윈드|플리스|후리스|집업|블레이저|파카|JACKET|VEST|DOWN|COAT|PARKA|FLEECE|WINDBREAK"),
    ("상의",        r"티셔츠|티셔트|반팔|긴팔|맨투맨|후드|스웨트|셔츠|폴로|탑\b|티\b|TEE|SHIRT|HOOD|POLO|CREW|SWEAT|TOP"),
]


def classify(name):
    nm = name or ""
    for label, pat in _CAT_RULES:
        if re.search(pat, nm, re.I):
            return label
    return ""


# ---------- list spine ----------
def fetch_list(csrf):
    """Paginate whole brand to the empty page; append each page to LIST_JSONL."""
    est = None
    items = []
    seen = set()
    open(LIST_JSONL, "w").close()  # fresh (list fetch is cheap, ~25 pages)
    page = 1
    while True:
        res = http_post_json(BASE + "/search/result", {
            "searchType": "search", "searchTerm": "", "displaySize": PAGE,
            "pageNumber": page, "searchLine": "N", "searchSort": "date",
            "searchBrandLCode": "1008"}, csrf)
        r = res["response"]
        if est is None:
            est = r.get("totalSize")
        got = r.get("searchResult") or []
        if not got:
            break
        with open(LIST_JSONL, "a", encoding="utf-8") as f:
            for g in got:
                cd = g.get("goodsCd")
                if not cd or cd in seen:
                    continue
                seen.add(cd)
                rec = {
                    "goodsCd": cd,
                    "goodsNm": (g.get("goodsNm") or "").strip(),
                    "pcDspyNm": g.get("pcDspyNm") or "",
                    "price": g.get("sellPrice") or g.get("tagPrice") or "",
                }
                items.append(rec)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"  list page {page}: +{len(got)} (total {len(items)})", flush=True)
        if len(items) >= CAP:
            print(f"  CAP {CAP} reached; stopping list pagination", flush=True)
            break
        page += 1
        time.sleep(0.1)
    return items, est, page


# ---------- category map ----------
def build_cat_map(csrf):
    cmap = {}
    counts = {}
    for cm, label in CATEGORIES:
        page = 1
        n = 0
        while True:
            try:
                res = http_post_json(BASE + "/search/result", {
                    "searchType": "search", "searchTerm": "", "displaySize": PAGE,
                    "pageNumber": page, "searchLine": "N", "searchSort": "date",
                    "searchBrandMCode": cm}, csrf)
            except Exception as e:
                print("  cat", cm, "err:", e, flush=True)
                break
            got = res["response"].get("searchResult") or []
            if not got:
                break
            for g in got:
                cd = g.get("goodsCd")
                if cd and cd not in cmap:
                    cmap[cd] = label
                    n += 1
            page += 1
            time.sleep(0.05)
        counts[label] = n
    print("  category map:", counts, "->", len(cmap), "mapped", flush=True)
    return cmap


# ---------- gvnt detail (resumable, parallel) ----------
def parse_gvnt(govs):
    out = {"material": "", "origin": "", "mfg_date": "", "color": "", "sizes": ""}
    for it in govs:
        nm = it.get("commCdNm") or ""
        val = (it.get("goodsGvntValue") or "").strip()
        if not val:
            continue
        if "소재" in nm and not out["material"]:
            out["material"] = re.sub(r"\s+", " ", val)
        elif "제조국" in nm and not out["origin"]:
            out["origin"] = val
        elif ("제조연월" in nm or "제조일" in nm) and not out["mfg_date"]:
            out["mfg_date"] = val
        elif nm == "색상" and not out["color"]:
            out["color"] = val
        elif nm == "사이즈" and not out["sizes"]:
            out["sizes"] = clean_sizes(val)
    return out


def load_gvnt_cache():
    cache = {}
    if os.path.exists(GVNT_JSONL):
        with open(GVNT_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    cache[rec["goodsCd"]] = rec
                except Exception:
                    pass
    return cache


def fetch_one_gvnt(cd):
    """Independent opener per call (thread-safe). Returns a record dict."""
    o = urllib.request.build_opener()
    try:
        raw = http_get(BASE + "/goods/api/gvnt/" + cd, opn=o)
        govs = (json.loads(raw).get("response") or {}).get("governments") or []
        d = parse_gvnt(govs)
        status = "ok" if (d["material"] or d["origin"] or d["sizes"]) else "empty"
        return {"goodsCd": cd, "status": status, **d}
    except Exception as e:
        return {"goodsCd": cd, "status": "err", "material": "", "origin": "",
                "mfg_date": "", "color": "", "sizes": "", "_err": str(e)[:120]}


def enrich(codes):
    cache = load_gvnt_cache()
    # resume: keep ok/empty; redo missing or err
    todo = [c for c in codes if cache.get(c, {}).get("status") not in ("ok", "empty")]
    print(f"  gvnt: {len(codes)} codes, {len(codes)-len(todo)} cached ok/empty, "
          f"{len(todo)} to fetch", flush=True)
    fh = open(GVNT_JSONL, "a", encoding="utf-8")
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_one_gvnt, c): c for c in todo}
        for fut in as_completed(futs):
            rec = fut.result()
            cache[rec["goodsCd"]] = rec
            with _lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
            done += 1
            if done % 200 == 0:
                print(f"    gvnt {done}/{len(todo)}", flush=True)
    fh.close()
    # one retry pass for errors
    errs = [c for c in codes if cache.get(c, {}).get("status") == "err"]
    if errs:
        print(f"  gvnt retry pass for {len(errs)} errors", flush=True)
        fh = open(GVNT_JSONL, "a", encoding="utf-8")
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(fetch_one_gvnt, c): c for c in errs}
            for fut in as_completed(futs):
                rec = fut.result()
                cache[rec["goodsCd"]] = rec
                with _lock:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
        fh.close()
    return cache


# ---------- assemble ----------
FIELDS = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    csrf = get_csrf()
    if not csrf:
        print("FATAL: no csrf (blocked?)")
        return {"ok": False}
    print("csrf ok", flush=True)

    print("[1/4] list spine (whole brand)...", flush=True)
    items, est, last_page = fetch_list(csrf)
    print(f"  list done: {len(items)} SKUs, est_total={est}, last_page={last_page}", flush=True)

    print("[2/4] category map (6 medium cats)...", flush=True)
    cmap = build_cat_map(csrf)

    print("[3/4] gvnt detail enrichment...", flush=True)
    codes = [it["goodsCd"] for it in items]
    cache = enrich(codes)

    print("[4/4] assemble CSV...", flush=True)
    rows = []
    seen = set()
    for it in items:
        cd = it["goodsCd"]
        if cd in seen:
            continue
        seen.add(cd)
        gv = cache.get(cd, {})
        name = it["goodsNm"]
        color = gv.get("color") or color_from_dspy(it["pcDspyNm"])
        cat = cmap.get(cd) or classify(name)
        rows.append({
            "source": "eider", "brand": "아이더", "style_code": cd, "name": name,
            "color": color, "price": it["price"], "currency": "KRW",
            "category": cat, "gender": gender_of(name, cd),
            "sizes": gv.get("sizes", ""), "origin": gv.get("origin", ""),
            "material": gv.get("material", ""), "mfg_date": gv.get("mfg_date", ""),
            "url": BASE + "/goods/" + cd,
        })

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # stats
    enrich_ok = sum(1 for c in codes if cache.get(c, {}).get("status") == "ok")
    enrich_empty = sum(1 for c in codes if cache.get(c, {}).get("status") == "empty")
    enrich_miss = sum(1 for c in codes if cache.get(c, {}).get("status") not in ("ok", "empty"))
    cat_filled = sum(1 for r in rows if r["category"])
    cat_from_map = sum(1 for it in items if it["goodsCd"] in cmap)
    filled = {fld: sum(1 for r in rows if str(r.get(fld, "")).strip()) for fld in FIELDS}
    summary = {
        "ok": len(rows) > 0 and (est is None or len(rows) >= min(est, CAP) or len(rows) == est),
        "after": len(rows), "est_total": est, "last_page": last_page,
        "enrich_ok": enrich_ok, "enrich_empty": enrich_empty, "enrich_miss": enrich_miss,
        "cat_filled": cat_filled, "cat_from_map": cat_from_map,
        "filled": filled,
    }
    print("WROTE", len(rows), "rows ->", OUT, flush=True)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    with open(os.path.join(HERE, "outputs", "_eider_full_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return summary


if __name__ == "__main__":
    main()
