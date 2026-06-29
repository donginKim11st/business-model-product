#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FULL (전수) extractor for the K2 (케이투 / K2코리아) official mall on K.VILLAGE
(https://www.k-village.co.kr/k2). Upgrades extract_k2.py (120-row sample) to the
whole catalogue by paging the brand search API to the last page (no sample cap).

Method (server-side urllib, Chrome UA, JSON internal APIs):
  Phase A  POST /search/result   searchBrandLCode=1007 , displaySize=100, pageNumber++
           -> loop until a page returns < displaySize items (totalSize ~2287/2298).
           Dumps all goodsCd + list metadata to outputs/_k2_ids.json
  Phase B  GET  /goods/api/gvnt/{goodsCd}  (상품정보제공고시: 소재/제조국/제조연월/사이즈/종류)
           -> append each enriched row to outputs/_k2_progress.jsonl (resume-safe).
           A gvnt failure NEVER drops the row: list-level fields are kept, only
           material/origin/mfg_date are left blank.
  Finally  write outputs/extract_brand_k2.csv (dedup by goodsCd / style_code).

Output header: source,brand,style_code,name,color,price,currency,category,gender,
               sizes,origin,material,mfg_date,url    (utf-8-sig)
Hard cap: 5000 rows (task guard); K2 is ~2287 so the cap is not expected to bind.
"""
import csv, json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse
from http.cookiejar import CookieJar

BASE = "https://www.k-village.co.kr"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "outputs", "extract_brand_k2.csv")
IDS  = os.path.join(HERE, "outputs", "_k2_ids.json")
PROG = os.path.join(HERE, "outputs", "_k2_progress.jsonl")
LOG  = os.path.join(HERE, "outputs", "_k2_full.log")
UA   = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

BRAND_LCODE = "1007"   # K2 (verified live: brandNm=K2, totalSize ~2287-2298)
PAGE_SIZE   = 100
CAP         = 5000     # task guard: stop at 5000 if catalogue were ever this huge

FIELDS = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]

cj = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def q(url):  # task convention: encode korean-slug urls (ours are ascii, harmless)
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
    html = http_get(BASE + "/k2")
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


def color_from_dspy(pc_dspy, goods_nm):
    if pc_dspy:
        m = re.search(r"\(([^()]+)\)\s*$", pc_dspy.strip())
        if m:
            return m.group(1).strip()
    return ""


# coarse category classifier on gvnt '종류' (authoritative) then product name.
# priority order matters: distinctive cats (신발/가방) first, 용품 is the catch-all.
_CAT_RULES = [
    ("신발", r"신발|슈즈|운동화|샌들|부츠|워킹화|트레킹화|등산화|러닝화|슬리퍼|아쿠아|스니커|단화|로퍼"),
    ("가방", r"가방|백팩|배낭|크로스백|슬링백|메신저|더플|토트백|숄더백|파우치|힙색|웨이스트백|보스턴|러기지|캐리어|짐색"),
    ("아우터", r"자켓|재킷|점퍼|코트|패딩|다운|바람막이|조끼|플리스|후리스|아노락|윈드|파카|야상|집업\s*점퍼|블루종"),
    ("하의", r"팬츠|바지|쇼츠|반바지|레깅스|스커트|치마|슬랙스|조거|9부|7부|5부|7부|숏팬츠|하의"),
    ("상의", r"티셔츠|티셔트|반팔|긴팔|셔츠|맨투맨|후드|니트|스웨터|폴로|카라|래쉬가드|래시가드|탑|브라탑|크롭|상의|집업\s*티"),
    ("용품", r"모자|캡|비니|버킷|햇|양말|장갑|벨트|머플러|스카프|마스크|넥워머|게이터|타월|타올|텐트|매트|스틱|우산|헤어|밴드|토시|안경|선글|고글|워치|시계|키링|아이웨어|용품|액세서|악세서|토트백|백"),
]


def classify_category(jong, goods_nm):
    blob = ((jong or "") + " " + (goods_nm or "")).strip()
    if not blob:
        return ""
    for label, pat in _CAT_RULES:
        if re.search(pat, blob):
            return label
    return ""


def enrich_gvnt(goods_cd):
    """Return dict(material, origin, mfg_date, color, sizes, jong, _err). Never raises."""
    out = {"material": "", "origin": "", "mfg_date": "", "color": "", "sizes": "",
           "jong": "", "_err": ""}
    try:
        raw = http_get(BASE + "/goods/api/gvnt/" + goods_cd)
        govs = (json.loads(raw).get("response") or {}).get("governments") or []
        for it in govs:
            nm = it.get("commCdNm") or ""
            val = (it.get("goodsGvntValue") or "").strip()
            if not val:
                continue
            if "소재" in nm and not out["material"]:
                out["material"] = re.sub(r"\s+", " ", val)
            elif "제조국" in nm and not out["origin"]:
                out["origin"] = val
            elif "제조연월" in nm and not out["mfg_date"]:
                out["mfg_date"] = val
            elif nm == "색상" and not out["color"]:
                out["color"] = val
            elif nm == "사이즈" and not out["sizes"]:
                out["sizes"] = clean_sizes(val)
            elif nm == "종류" and not out["jong"]:
                out["jong"] = val
    except Exception as e:
        out["_err"] = str(e)
    return out


# ---------------------------------------------------------------- Phase A
def collect_ids(csrf):
    """Page whole brand to the last page; persist goodsCd + list metadata."""
    items = {}
    page = 1
    est_total = None
    while True:
        body = {"searchType": "search", "searchTerm": "", "displaySize": PAGE_SIZE,
                "pageNumber": page, "searchLine": "N", "searchSort": "date",
                "searchBrandLCode": BRAND_LCODE}
        res = http_post_json(BASE + "/search/result", body, csrf)
        r = res["response"]
        if est_total is None:
            est_total = r.get("totalSize")
        batch = r.get("searchResult") or []
        new = 0
        for g in batch:
            cd = g.get("goodsCd")
            if not cd or cd in items:
                continue
            items[cd] = {
                "goodsCd": cd,
                "goodsNm": (g.get("goodsNm") or "").strip(),
                "sellPrice": g.get("sellPrice"),
                "tagPrice": g.get("tagPrice"),
                "pcDspyNm": g.get("pcDspyNm"),
            }
            new += 1
        log(f"[A] page {page}: got {len(batch)} (new {new}) total uniq {len(items)} / est {est_total}")
        if len(batch) < PAGE_SIZE or len(items) >= CAP:
            break
        page += 1
        time.sleep(0.2)
    data = {"est_total": est_total, "pages": page, "items": list(items.values())}
    with open(IDS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    log(f"[A] DONE collected {len(items)} ids across {page} pages (est {est_total})")
    return data


# ---------------------------------------------------------------- Phase B
def load_done():
    done = {}
    if os.path.exists(PROG):
        with open(PROG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    done[row["style_code"]] = row
                except Exception:
                    continue
    return done


def enrich_all(ids_data):
    items = ids_data["items"]
    done = load_done()
    log(f"[B] resume: {len(done)} already enriched of {len(items)}")
    pf = open(PROG, "a", encoding="utf-8")
    n = 0
    for it in items:
        cd = it["goodsCd"]
        if cd in done:
            continue
        name = it["goodsNm"]
        gv = enrich_gvnt(cd)
        color = gv["color"] or color_from_dspy(it.get("pcDspyNm"), name)
        category = classify_category(gv["jong"], name)
        row = {
            "source": "k2",
            "brand": "케이투",
            "style_code": cd,
            "name": name,
            "color": color,
            "price": it.get("sellPrice") or it.get("tagPrice") or "",
            "currency": "KRW",
            "category": category,
            "gender": gender_of(name, cd),
            "sizes": gv["sizes"],
            "origin": gv["origin"],
            "material": gv["material"],
            "mfg_date": gv["mfg_date"],
            "url": BASE + "/goods/" + cd,
        }
        pf.write(json.dumps(row, ensure_ascii=False) + "\n")
        pf.flush()
        done[cd] = row
        n += 1
        if n % 100 == 0:
            log(f"[B] enriched {n} (this run), {len(done)} total")
        time.sleep(0.12)
    pf.close()
    log(f"[B] DONE enriched {n} this run; {len(done)} total rows in progress")
    return done


def write_csv(done):
    rows = list(done.values())[:CAP]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in FIELDS})
    filled = {fld: sum(1 for r in rows if str(r.get(fld, "")).strip()) for fld in FIELDS}
    log("WROTE", len(rows), "rows ->", OUT)
    log("filled:", json.dumps(filled, ensure_ascii=False))
    return len(rows), filled


def main():
    csrf = get_csrf()
    if not csrf:
        log("FATAL: no csrf token (blocked?)")
        return
    if os.path.exists(IDS):
        with open(IDS, encoding="utf-8") as f:
            ids_data = json.load(f)
        log(f"[A] reuse cached ids: {len(ids_data['items'])} (pages {ids_data.get('pages')}, est {ids_data.get('est_total')})")
    else:
        ids_data = collect_ids(csrf)
    done = enrich_all(ids_data)
    n, filled = write_csv(done)
    log(f"FINAL after={n} est_total={ids_data.get('est_total')} pages={ids_data.get('pages')}")


if __name__ == "__main__":
    main()
