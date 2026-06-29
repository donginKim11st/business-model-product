#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FULL census extractor for National Geographic (내셔널지오그래픽) on N.STATION
(nstationmall.com, 더네이쳐홀딩스 직영) — Vue SPA + self-built backend.

전 카테고리 × 전 페이지 전수 추출 (full census, not a 120 sample).

LIST  : /goods/category/{AAA000000}?page=N   (server-rendered grid, 100/page)
        - top-level categories discovered from /natgeo nav (AAA000000 codes).
        - paginate ?page=1.. until a page yields no detail links OR repeats the
          previous page's id set (site clamps page>last to the last page).
        - harvest /goods/detail/{goodsId} links; dedup across categories keeping
          first-seen order (core product cats first, outlet 011 last).
DETAIL: /goods/detail/{goodsId}              (server-rendered embedded JSON + DOM)
        - var goodsInfo = {...}            : id, name, brandName, price, dcPrice,
                                             firstCategory/category
        - data-product-code="..."         : COLORWAY-UNIQUE code (style + color
                                             suffix, e.g. N265UPA910099). This is
                                             the style_code we keep, UNTRUNCATED.
                                             (the <dt>스타일</dt> field is style-
                                             level only — N265UPA910 — and drops
                                             the color suffix, so it is NOT used.)
        - 상품고시 table <th><b>k</b></th><td><b>v</b></td> : 원산지/소재/제조연월
OPTIONS: /goods/detail/{goodsId}/options     (internal JSON XHR)
        - {"options":[{"_name": color,"sub":[{"_name": size,"_price":..}]}]}

Notes:
  - one goodsId == one colorway. rows are per goodsId (컬러웨이 단위).
  - price = dcPrice (판매가); coupon price (쿠폰 할인가) ignored.
  - 고시 소재/제조연월 = 대개 "상세페이지 참조"(이미지) → 공란. origin은 '원산지' 텍스트행에서.
  - >5000 distinct → stop at 5000 (note records true total).
"""
import re, csv, json, time, sys, os, threading
import urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from curl_cffi import requests as creq
    _SESS = creq.Session(impersonate="chrome")
    HAVE_CFFI = True
except Exception:  # noqa: BLE001
    _SESS = None
    HAVE_CFFI = False

BASE = "https://www.nstationmall.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
ROOT = "/Users/a1101417/Work/business-model/identity"
OUT = os.path.join(ROOT, "outputs/extract_brand_natgeo.csv")
IDS_CACHE = os.path.join(ROOT, "outputs/_natgeo_ids.json")
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
CAP = 5000
WORKERS = 10

# fallback top-level categories (used if nav discovery fails)
TOPS_FALLBACK = ["001000000", "002000000", "003000000", "004000000", "006000000",
                 "007000000", "008000000", "010000000", "011000000", "054000000",
                 "066000000", "074000000", "083000000"]

GENDER_MAP = [("남녀공용", "공용"), ("남성", "남성"), ("여성", "여성"), ("남자", "남성"),
              ("여자", "여성"), ("유니", "공용"), ("공용", "공용"), ("키즈", "키즈"),
              ("아동", "키즈"), ("주니어", "키즈"), ("유아", "키즈"), ("KIDS", "키즈"),
              ("JUNIOR", "키즈")]
CAT_GENDER = {"001": "남성", "002": "여성", "007": "키즈"}
PLACEHOLDER = {"", "-", "상세페이지 참조", "상세페이지참조", "상세설명참조", "상세페이지 참고",
               "상세설명에 표시", "상세설명에표시", "상세 설명에 표시", "상세페이지 표시",
               "상세정보 참조", "상세정보참조", "상품상세참조", "상품상세 참조"}

_local = threading.local()


def _urllib_get(url):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,")
    hdr = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
           "X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/natgeo"}
    with urllib.request.urlopen(urllib.request.Request(enc, headers=hdr), timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def _session():
    if not HAVE_CFFI:
        return None
    s = getattr(_local, "sess", None)
    if s is None:
        s = creq.Session(impersonate="chrome")
        _local.sess = s
    return s


def fetch(url, as_json=False, retries=3):
    last = None
    for attempt in range(retries + 1):
        try:
            if HAVE_CFFI:
                r = _session().get(url, timeout=30, headers={
                    "Accept-Language": "ko-KR,ko;q=0.9",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": BASE + "/natgeo"})
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}")
                return r.json() if as_json else r.text
            data = _urllib_get(url)
            return json.loads(data) if as_json else data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5 * (attempt + 1))
    raise last


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


# ---------------- LIST / harvest ----------------

def discover_tops():
    try:
        h = fetch(BASE + "/natgeo")
        tops = sorted(set(c for c in re.findall(r"/goods/category/(\d+)", h)
                          if c.endswith("000000")))
        if tops:
            return tops
    except Exception as e:  # noqa: BLE001
        print(f"[warn] nav discovery failed: {e}")
    return TOPS_FALLBACK


def harvest_ids():
    tops = discover_tops()
    print(f"== top categories ({len(tops)}): {tops}")
    first_cat, order, percat = {}, [], {}
    for code in tops:
        prev, pg, catset = None, 1, set()
        while True:
            try:
                h = fetch(f"{BASE}/goods/category/{code}?page={pg}")
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] cat {code} p{pg}: {e}")
                break
            ids = list(dict.fromkeys(re.findall(r"/goods/detail/(\d+)", h)))
            if not ids or ids == prev:
                break
            for g in ids:
                if g not in first_cat:
                    first_cat[g] = code
                    order.append(g)
                catset.add(g)
            prev = ids
            pg += 1
            if pg > 200:
                break
            time.sleep(0.1)
        percat[code] = len(catset)
        print(f"  {code}: pages={pg-1} distinct={len(catset)} total={len(order)}")
    json.dump({"order": order, "first_cat": first_cat, "percat": percat},
              open(IDS_CACHE, "w"))
    return order, first_cat, percat


# ---------------- DETAIL parsing ----------------

def parse_goods_info(html):
    m = re.search(r"var\s+goodsInfo\s*=\s*\{(.*?)\}", html, re.S)
    out = {}
    if not m:
        return out
    body = m.group(1)
    for key in ("id", "name", "brandName", "price", "dcPrice",
                "category", "firstCategory", "secondCategory", "thirdCategory"):
        km = re.search(r"\b" + key + r"\s*:\s*('([^']*)'|\"([^\"]*)\"|([\d.]+))", body)
        if km:
            out[key] = (km.group(2) or km.group(3) or km.group(4) or "").strip()
    return out


def parse_style_code(html):
    """COLORWAY-unique code from data-product-code (untruncated).
    Fallback to <dt>스타일</dt><dd> (style-level) only if missing."""
    m = re.search(r'data-product-code\s*=\s*"([^"]+)"', html)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.search(r"<dt>\s*스타일\s*</dt>\s*<dd>(.*?)</dd>", html, re.S)
    return clean(m.group(1)) if m else ""


def parse_gosi(html):
    out = {}
    for m in re.finditer(r"<th[^>]*><b>(.*?)</b></th>\s*<td><b>(.*?)</b></td>", html, re.S):
        out[clean(m.group(1))] = clean(m.group(2))
    return out


def parse_options(opt):
    colors, sizes, prices = [], [], []
    for o in (opt.get("options") or []):
        cname = (o.get("_name") or "").strip()
        if cname and cname not in colors:
            colors.append(cname)
        for s in (o.get("sub") or []):
            sname = (s.get("_name") or "").strip()
            if sname and sname != "-" and sname not in sizes:
                sizes.append(sname)
            try:
                prices.append(int(float(s.get("_price"))))
            except (TypeError, ValueError):
                pass
    top_price = opt.get("_price")
    try:
        top_price = int(float(top_price))
    except (TypeError, ValueError):
        top_price = min(prices) if prices else None
    return "|".join(colors), "|".join(sizes), top_price


def gender_of(name, cat_code, gi):
    hay = (name or "") + " " + (gi.get("category") or "")
    for token, g in GENDER_MAP:
        if token in hay:
            return g
    return CAT_GENDER.get((cat_code or "")[:3], "")


def extract_one(gid, cat_code):
    url = f"{BASE}/goods/detail/{gid}"
    html = fetch(url)
    gi = parse_goods_info(html)
    try:
        opt = fetch(f"{url}/options", as_json=True)
    except Exception:  # noqa: BLE001
        opt = {}
    color, sizes, opt_price = parse_options(opt)

    name = (gi.get("name") or "").strip()
    if not name:
        m = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        name = clean(m.group(1)) if m else ""

    gosi = parse_gosi(html)
    material = gosi.get("제품 소재") or gosi.get("소재") or ""
    mfg = gosi.get("제조연월") or gosi.get("제조년월") or ""
    origin = gosi.get("원산지") or gosi.get("제조국") or ""
    material = "" if material in PLACEHOLDER else material
    mfg = "" if mfg in PLACEHOLDER else mfg
    origin = "" if origin in PLACEHOLDER else origin

    price = gi.get("dcPrice") or gi.get("price") or (str(opt_price) if opt_price else "")
    return {
        "source": "natgeo",
        "brand": (gi.get("brandName") or "내셔널지오그래픽").strip(),
        "style_code": parse_style_code(html),
        "name": name,
        "color": color,
        "price": str(price).strip(),
        "currency": "KRW",
        "category": (gi.get("firstCategory") or gi.get("category") or "").strip(),
        "gender": gender_of(name, cat_code, gi),
        "sizes": sizes,
        "origin": origin,
        "material": material,
        "mfg_date": mfg,
        "url": url,
    }


def write_csv(rows_by_gid, order):
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for gid in order:
            r = rows_by_gid.get(gid)
            if r:
                w.writerow(r)


def main():
    use_cache = "--cache" in sys.argv
    if use_cache and os.path.exists(IDS_CACHE):
        d = json.load(open(IDS_CACHE))
        order, first_cat, percat = d["order"], d["first_cat"], d["percat"]
        print(f"== loaded cached ids: {len(order)} ==")
    else:
        print("== harvesting ids (전 카테고리 × 전 페이지) ==")
        order, first_cat, percat = harvest_ids()

    total_distinct = len(order)
    capped = total_distinct > CAP
    targets = order[:CAP] if capped else order
    print(f"\nTOTAL distinct goodsIds={total_distinct}; "
          f"{'CAPPED at '+str(CAP) if capped else 'extracting all'} → {len(targets)} targets")

    rows_by_gid, fails = {}, []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(extract_one, gid, first_cat.get(gid, "")): gid
                for gid in targets}
        for fut in as_completed(futs):
            gid = futs[fut]
            try:
                rows_by_gid[gid] = fut.result()
            except Exception as e:  # noqa: BLE001
                fails.append((gid, str(e)))
            done += 1
            if done % 200 == 0:
                write_csv(rows_by_gid, targets)
                print(f"  {done}/{len(targets)} done (ok={len(rows_by_gid)} fail={len(fails)}) [checkpoint]")

    # retry failures once, serially
    if fails:
        print(f"== retrying {len(fails)} failures ==")
        still = []
        for gid, _ in fails:
            try:
                rows_by_gid[gid] = extract_one(gid, first_cat.get(gid, ""))
            except Exception as e:  # noqa: BLE001
                still.append((gid, str(e)))
        fails = still

    write_csv(rows_by_gid, targets)
    n = len(rows_by_gid)

    # ---- stats ----
    filled = {c: 0 for c in HEADER}
    for gid in targets:
        r = rows_by_gid.get(gid)
        if not r:
            continue
        for c in HEADER:
            if r[c]:
                filled[c] += 1
    distinct_style = len(set(r["style_code"] for r in rows_by_gid.values() if r["style_code"]))
    core = sum(1 for gid in targets if not first_cat.get(gid, "").startswith("011"))
    outlet = len(targets) - core
    print(f"\nWROTE {n} rows -> {OUT}")
    print(f"total_distinct={total_distinct} capped={capped} fails={len(fails)}")
    print(f"distinct style_code(colorway)={distinct_style}  core={core} outlet(011)={outlet}")
    print("filled per column:")
    for c in HEADER:
        print(f"  {c:11s}: {filled[c]}/{n}")
    if fails:
        print("remaining fails:", fails[:10])
    # emit a machine-readable summary line
    summary = {"total_distinct": total_distinct, "rows": n, "capped": capped,
               "distinct_style": distinct_style, "core": core, "outlet": outlet,
               "fails": len(fails), "percat": percat}
    json.dump(summary, open(os.path.join(ROOT, "outputs/_natgeo_full_summary.json"), "w"))
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
