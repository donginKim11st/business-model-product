#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extract a real product sample from the K2 (케이투 / K2코리아) official mall on K.VILLAGE
(https://www.k-village.co.kr/k2).

Server-side extraction via the site's own internal JSON APIs (sibling of eider_extract.py):
  - List/category : POST /search/result          (per medium-category cm code -> name/price/color/style)
  - Detail spec   : GET  /goods/api/gvnt/{goodsCd} (상품정보제공고시: material/origin/mfg_date/color/sizes)

Brand scope:  searchBrandLCode=1007 (whole-brand est_total = color-SKU count); per category use cm code only.
Output: outputs/extract_brand_k2.csv
Header: source,brand,style_code,name,color,price,currency,category,gender,sizes,origin,material,mfg_date,url
"""
import csv, json, os, re, time
import urllib.request, urllib.error, urllib.parse
from http.cookiejar import CookieJar

BASE = "https://www.k-village.co.kr"
OUT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "extract_brand_k2.csv")
UA   = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

BRAND_LCODE = "1007"   # K2 (verified live: brandNm=K2, totalSize 2298). EIDER=1008, WIDEANGLE=1009.

# K2 brand-nav medium-category (cm) code -> clean category label  (from /k2 page nav)
CATEGORIES = [
    ("1028", "아우터"),
    ("1029", "상의"),
    ("1031", "하의"),
    ("1033", "신발"),
    ("1034", "가방"),
    ("1035", "용품"),
]
PER_CATEGORY = 20          # take ~20 goods per category
CAP = 120                  # overall sample cap

cj = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def q(url):  # task convention: encode any korean-slug urls (ours are ascii, harmless)
    return urllib.parse.quote(url, safe=":/?=&%#+,")


def http_get(url):
    req = urllib.request.Request(q(url), headers={
        "User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE + "/k2", "Accept": "application/json, text/html, */*",
    })
    with opener.open(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def http_post_json(url, body, csrf):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(q(url), data=data, headers={
        "User-Agent": UA,
        "Content-Type": "application/json; charset=utf-8",
        "X-CSRF-TOKEN": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE + "/k2",
        "Accept": "application/json",
    }, method="POST")
    with opener.open(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def get_csrf():
    """GET the brand page to seed JSESSIONID/XSRF cookies and read the _csrf meta token."""
    html = http_get(BASE + "/k2")
    m = re.search(r'name="_csrf"\s+content="([^"]+)"', html)
    return m.group(1) if m else None


def clean_sizes(raw):
    """ '90,95,100,105' or 'M[04],L[05]' -> '90|95|100|105' / 'M|L' """
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
    # fallback: K2 style code 2nd char  (K/F + [W/M/U/K] ...): KWM=여성, KMM=남성, FUM=공용
    c = (goods_cd or "  ")[1:2].upper()
    return {"M": "남성", "W": "여성", "U": "공용", "K": "아동"}.get(c, "")


def color_from_dspy(pc_dspy, goods_nm):
    """ '시그니처 경량 바람막이 자켓 W (PISTACHIO GREEN)' -> 'PISTACHIO GREEN' (list fallback) """
    if pc_dspy:
        m = re.search(r"\(([^()]+)\)\s*$", pc_dspy.strip())
        if m:
            return m.group(1).strip()
    return ""


def enrich_gvnt(goods_cd):
    """Return dict(material, origin, mfg_date, color, sizes) from the 고시 API. Never raises."""
    out = {"material": "", "origin": "", "mfg_date": "", "color": "", "sizes": ""}
    try:
        raw = http_get(BASE + "/goods/api/gvnt/" + goods_cd)
        govs = (json.loads(raw).get("response") or {}).get("governments") or []
        for it in govs:
            nm = it.get("commCdNm") or ""
            val = (it.get("goodsGvntValue") or "").strip()
            if not val:
                continue
            if "소재" in nm and not out["material"]:          # '제품소재'
                out["material"] = re.sub(r"\s+", " ", val)
            elif "제조국" in nm and not out["origin"]:
                out["origin"] = val
            elif "제조연월" in nm and not out["mfg_date"]:     # '제조연월(수입연월)'
                out["mfg_date"] = val
            elif nm == "색상" and not out["color"]:
                out["color"] = val
            elif nm == "사이즈" and not out["sizes"]:
                out["sizes"] = clean_sizes(val)
    except Exception as e:
        out["_err"] = str(e)
    return out


def main():
    csrf = get_csrf()
    if not csrf:
        print("FATAL: no csrf token (blocked?)")
        return {"ok": False, "n": 0, "blocked": True}

    # est_total: whole-brand color-SKU count (L-code only; do NOT mix with category code)
    est_total = None
    try:
        whole = http_post_json(BASE + "/search/result",
                               {"searchType": "search", "searchTerm": "", "displaySize": 1,
                                "pageNumber": 1, "searchLine": "N", "searchSort": "date",
                                "searchBrandLCode": BRAND_LCODE}, csrf)
        est_total = whole["response"]["totalSize"]
    except Exception as e:
        print("est_total err:", e)

    rows = []
    seen = set()
    cat_counts = {}
    for cm, label in CATEGORIES:
        if len(rows) >= CAP:
            break
        try:
            # single category key only (sending searchBrandLCode too reverts to full brand)
            res = http_post_json(BASE + "/search/result",
                                 {"searchType": "search", "searchTerm": "", "displaySize": 40,
                                  "pageNumber": 1, "searchLine": "N", "searchSort": "date",
                                  "searchBrandMCode": cm}, csrf)
            r = res["response"]
            cat_counts[label] = r.get("totalSize")
            items = r.get("searchResult") or []
        except Exception as e:
            print("category", cm, "err:", e)
            cat_counts[label] = "ERR"
            continue

        taken = 0
        for g in items:
            if taken >= PER_CATEGORY or len(rows) >= CAP:
                break
            cd = g.get("goodsCd")
            if not cd or cd in seen:
                continue
            seen.add(cd)
            taken += 1
            name = (g.get("goodsNm") or "").strip()
            price = g.get("sellPrice") or g.get("tagPrice") or ""
            gv = enrich_gvnt(cd)
            color = gv["color"] or color_from_dspy(g.get("pcDspyNm"), name)
            rows.append({
                "source": "k2",
                "brand": "케이투",
                "style_code": cd,
                "name": name,
                "color": color,
                "price": price,
                "currency": "KRW",
                "category": label,
                "gender": gender_of(name, cd),
                "sizes": gv["sizes"],
                "origin": gv["origin"],
                "material": gv["material"],
                "mfg_date": gv["mfg_date"],
                "url": BASE + "/goods/" + cd,
            })
            time.sleep(0.15)

    fields = ["source", "brand", "style_code", "name", "color", "price", "currency",
              "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    # report fill rates
    filled = {}
    for fld in fields:
        filled[fld] = sum(1 for row in rows if str(row.get(fld, "")).strip())
    print("WROTE", len(rows), "rows ->", OUT)
    print("est_total(brand color-SKUs):", est_total)
    print("category counts:", cat_counts)
    print("filled:", filled)
    return {"ok": len(rows) > 0, "n": len(rows), "est_total": est_total,
            "cat_counts": cat_counts, "filled": filled}


if __name__ == "__main__":
    main()
