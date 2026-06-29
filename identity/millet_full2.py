#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
밀레(MILLET) 공식몰 *전수(全數)* 추출 v2 — 컬러웨이 단위 1행 (stdlib only, resumable).

배경 / 정정:
  v1(millet_full.py)은 상세 og:title 코드(MLVUT497 등)로 dedup 했으나, millet 의
  og:title 코드는 *스타일 단위* 코드라 한 코드가 여러 컬러웨이 product id 를 묶는다.
  (예: MLVUT497 -> id 29163=L/SAGE, 29164=WINE, 29165=ASPHALT — 각 id 가 별도
   og:image·별도 컬러웨이.) v1 dedup 은 컬러웨이를 붕괴시켜 '절단'에 해당.
  => v2 는 **product id(=컬러웨이) 1개당 1행**. style_code 는 og:title 의 *원본 풀코드*
     (절단 금지). 컬러웨이 색상은 구매옵션 블록(item-i <span>색상</span>)에서 취득.

데이터 소스(상세 /front/product/{id}):
  - og:title "이름_모델코드" -> name, style_code(스타일코드, 컬러웨이들이 공유)
  - 구매옵션 JS: html+='<div class="item-i">'; html+='<span>{컬러}</span>'; -> 컬러웨이 색상
  - 고시 table: 색상(전체목록·폴백), 치수, 제조국/원산지, 소재, 제조연월
리스트(이미 완료): _millet_list.json (cateIdx 전수 × page 끝까지 -> 2207 uniq id)

출력: outputs/extract_brand_millet.csv (utf-8-sig, 덮어쓰기)
  헤더: source,brand,style_code,name,color,price,currency,category,gender,
        sizes,origin,material,mfg_date,url
  - source=millet, brand=MILLET, currency=KRW
  - 1행 = 1 컬러웨이(product id). style_code 는 풀 스타일코드(컬러웨이 간 반복 가능;
    컬러웨이 고유성은 style_code+color+url 로 보장). 사이트가 컬러웨이 SKU 코드를
    노출하지 않으므로 합성코드는 만들지 않음(조인키=네이티브 스타일코드 유지).
  - >5000 이면 5000 에서 멈춤 + notes.
"""
import csv, html as H, http.cookiejar, json, os, re, sys, time
import urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
os.makedirs(OUT, exist_ok=True)
CSV_PATH = os.path.join(OUT, "extract_brand_millet.csv")
LIST_CKPT = os.path.join(OUT, "_millet_list.json")      # phase1 결과(완료)
ROWS_CKPT = os.path.join(OUT, "_millet_rows2.jsonl")    # v2 상세 append
META_PATH = os.path.join(OUT, "_millet_full2.meta.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE = "https://www.millet.co.kr"
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
MAX_UNIQUE = 5000

opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def enc(url):
    return urllib.parse.quote(url, safe=":/?=&%#+,")


def http_get(url, timeout=30):
    req = urllib.request.Request(enc(url), headers={"User-Agent": UA})
    return opener.open(req, timeout=timeout).read().decode("utf-8", "replace")


def http_get_retry(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            return http_get(url)
        except Exception as e:
            last = e
            time.sleep(0.6 * (attempt + 1))
    raise last


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


CODE_RE = re.compile(r"^[A-Z]{1,6}[A-Z0-9]*\d[A-Z0-9]*$")
# RUDY PROJECT 등 아이웨어: og:title 끝 '(SP977842-0010)' 형식의 컬러웨이 코드
PAREN_CODE_RE = re.compile(r"\(([A-Z]{1,4}\d[A-Z0-9]*(?:-[A-Z0-9]+)?)\)\s*$")


def split_code(name_raw):
    """og:title -> (이름, 풀 스타일코드). 절단/변형 없음.
    두 형식 지원: '이름_모델코드'(밀레 본품), '이름 색상 (CODE)'(아이웨어)."""
    if "_" in name_raw:
        head, tail = name_raw.rsplit("_", 1)
        tail = tail.strip()
        if CODE_RE.match(tail):
            return head.strip(), tail
    m = PAREN_CODE_RE.search(name_raw)
    if m:
        return name_raw[:m.start()].strip(), m.group(1)
    return name_raw, ""


GENDER_TOKENS = [("남성", "남성"), ("여성", "여성"), ("키즈", "키즈"),
                 ("주니어", "주니어"), ("아동", "키즈"),
                 ("WOMEN", "여성"), ("MEN", "남성")]


def gender_from_name(name):
    up = name.upper()
    for tok, val in GENDER_TOKENS:
        if tok in name or tok in up:
            return val
    return ""


def th_td_pairs(html):
    pairs = re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", html, re.S)
    return [(strip_tags(th), H.unescape(strip_tags(td))) for th, td in pairs]


# 구매옵션 블록: html+='<div class="item-i">'; html+='<span>{컬러}</span>';
COLORWAY_RE = re.compile(r'item-i">\';\s*html\+=\'<span>([^<]+)</span>')


def parse_colorway_color(html):
    m = COLORWAY_RE.search(html)
    if m:
        return H.unescape(m.group(1).strip())
    # 느슨한 폴백: 'item-i' 이후 첫 <span>...</span>
    i = html.find("item-i")
    if i != -1:
        m2 = re.search(r"<span>([^<]+)</span>", html[i:i + 200])
        if m2:
            return H.unescape(m2.group(1).strip())
    return ""


def parse_detail(pid):
    html = http_get_retry(f"{BASE}/front/product/{pid}")
    f = {"name": "", "style_code": "", "color": "", "color_list": "",
         "sizes": "", "origin": "", "material": "", "mfg_date": ""}
    og = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    if og:
        nm, code = split_code(H.unescape(og.group(1).strip()))
        f["name"], f["style_code"] = nm, code
    f["color"] = parse_colorway_color(html)        # 컬러웨이 단위 색상
    for th, td in th_td_pairs(html):
        if not td:
            continue
        if not f["material"] and "소재" in th:
            f["material"] = td
        elif not f["color_list"] and "색상" in th:
            f["color_list"] = td
        elif not f["sizes"] and ("치수" in th or "사이즈" in th):
            parts = [p.strip() for p in re.split(r"[,/]", td) if p.strip()]
            f["sizes"] = "|".join(parts)
        elif not f["origin"] and ("제조국" in th or "원산지" in th):
            f["origin"] = td
        elif not f["mfg_date"] and ("제조연월" in th or "제조년월" in th):
            f["mfg_date"] = td
    if not f["color"]:                              # 옵션블록 없으면 고시목록 폴백
        f["color"] = f["color_list"]
    return f


# ---------------- Phase 2: detail crawl (per colorway id) ------------------
def phase2(meta):
    done = {}
    if os.path.exists(ROWS_CKPT):
        with open(ROWS_CKPT, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    done[r["id"]] = r
                except Exception:
                    pass
        print(f"[phase2] resume: {len(done)} rows already fetched",
              file=sys.stderr)
    ids = list(meta.keys())
    capped = False
    if len(ids) > MAX_UNIQUE:
        ids = ids[:MAX_UNIQUE]
        capped = True
    fout = open(ROWS_CKPT, "a", encoding="utf-8")
    for i, pid in enumerate(ids, 1):
        if pid in done:
            continue
        m = meta[pid]
        rec = {"id": pid, "name": m.get("name", ""),
               "style_code": m.get("style_code", ""),
               "price": m.get("price", ""), "category": m.get("category", ""),
               "color": "", "sizes": "", "origin": "",
               "material": "", "mfg_date": ""}
        try:
            d = parse_detail(pid)
            if d.get("style_code"):
                rec["style_code"] = d["style_code"]
            if d.get("name"):
                rec["name"] = d["name"]
            for k in ("color", "sizes", "origin", "material", "mfg_date"):
                rec[k] = d[k]
        except Exception as e:
            print(f"[warn] detail {pid}: {e}", file=sys.stderr)
        fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fout.flush()
        done[pid] = rec
        if i % 50 == 0:
            print(f"[phase2] {i}/{len(ids)} (fetched {len(done)})",
                  file=sys.stderr)
            phase3(done)        # 페이지(배치)마다 중간저장
        time.sleep(0.2)
    fout.close()
    return done, capped


# ---------------- Phase 3: CSV — 1행 = 1 컬러웨이(id), dedup 없음 ----------
def phase3(rows):
    tmp = CSV_PATH + ".tmp"
    n = 0
    filled = {k: 0 for k in HEADER}
    # id 오름차순 안정 정렬(재현성)
    items = sorted(rows.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit()
                   else 0)
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for pid, r in items:
            row = {k: "" for k in HEADER}
            row.update({
                "source": "millet", "brand": "MILLET",
                "style_code": r.get("style_code", ""),
                "name": r.get("name", ""),
                "color": r.get("color", ""),
                "price": r.get("price", ""), "currency": "KRW",
                "category": r.get("category", ""),
                "gender": gender_from_name(r.get("name", "")),
                "sizes": r.get("sizes", ""),
                "origin": r.get("origin", ""),
                "material": r.get("material", ""),
                "mfg_date": r.get("mfg_date", ""),
                "url": f"{BASE}/front/product/{pid}"})
            w.writerow(row)
            n += 1
            for k, v in row.items():
                if str(v).strip():
                    filled[k] += 1
    os.replace(tmp, CSV_PATH)
    return n, filled


def main():
    t0 = time.time()
    with open(LIST_CKPT, encoding="utf-8") as f:
        data = json.load(f)
    meta = data["meta"]
    print(f"[main] list ckpt: {len(meta)} unique colorway ids, "
          f"{data.get('n_categories')} categories", file=sys.stderr)
    rows, capped = phase2(meta)
    n, filled = phase3(rows)

    # reconciliation
    distinct_codes = len({r.get("style_code") for r in rows.values()
                          if r.get("style_code")})
    empty_code = sum(1 for r in rows.values() if not r.get("style_code"))
    empty_color = sum(1 for r in rows.values() if not r.get("color"))
    meta_out = {
        "rows_after": n,
        "unique_colorway_ids": len(meta),
        "distinct_style_codes": distinct_codes,
        "n_categories": data.get("n_categories"),
        "empty_style_code_rows": empty_code,
        "empty_color_rows": empty_color,
        "capped_at_5000": capped,
        "elapsed_sec": round(time.time() - t0, 1),
        "filled": filled,
    }
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta_out, f, ensure_ascii=False, indent=2)

    print("\n=== SUMMARY ===")
    print("rows (1 per colorway id):", n)
    print("unique colorway ids:", len(meta))
    print("distinct style codes:", distinct_codes)
    print("empty style_code rows:", empty_code)
    print("empty color rows:", empty_color)
    print("capped@5000:", capped)
    print("csv:", CSV_PATH)
    for k in HEADER:
        print(f"  {k}: {filled[k]}/{n}")


if __name__ == "__main__":
    main()
