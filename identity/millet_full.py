#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
밀레(MILLET) 공식몰 *전수(全數)* 추출 (stdlib only, resumable).

플랫폼: 자체몰 (/front/product/{id})
리스트: POST /front/product/product_list.ajax (세션쿠키 + X-CSRF-TOKEN)
  params: cateIdx, brandIdx=0, page, psize=25(서버상한), psort=new, pword=
  -> 상품카드 HTML 조각 + `var pls2="<카테고리 총개수>"`
상세: GET /front/product/{id} -> 상품정보제공고시 table(소재/색상/치수/제조국/제조연월)
       style_code 는 og:title "이름_모델코드" 에서 취득(리스트 카드와 동일).

전략(advisor 반영):
  - 전 카테고리(155개) cateIdx 를 nav 에서 동적 수집 -> 각 카테고리 page=1.. 끝까지 크롤.
  - 크롤 dedup 키 = product id. 카테고리별 distinct id 수 vs pls2 로 '전수' 검증(reconcile).
  - 일시 오류는 break 가 아니라 재시도(3회). 진짜 실패만 incomplete 로 기록.
  - category 값 = 그 상품을 포함하는 카테고리 중 pls2 가 가장 작은(=가장 구체적) 카테고리명.
  - 최종 CSV dedup = style_code 기준(빈 코드는 id 로 폴백 -> 무손실).
  - 체크포인트(JSONL/JSON)로 재개 가능. 상세 페이지는 상품당 1회만.
  - 고유 상품 > 5000 이면 5000 에서 멈추고 notes 기록.
"""
import csv, html as H, http.cookiejar, json, os, re, sys, time
import urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
os.makedirs(OUT, exist_ok=True)
CSV_PATH = os.path.join(OUT, "extract_brand_millet.csv")
LIST_CKPT = os.path.join(OUT, "_millet_list.json")     # 리스트 단계 결과
ROWS_CKPT = os.path.join(OUT, "_millet_rows.jsonl")    # 상세 단계 append
META_PATH = os.path.join(OUT, "_millet_full.meta.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE = "https://www.millet.co.kr"
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
MAX_UNIQUE = 5000

opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
_csrf = {"tok": None}


def enc(url):
    return urllib.parse.quote(url, safe=":/?=&%#+,")


def http_get(url, timeout=30):
    req = urllib.request.Request(enc(url), headers={"User-Agent": UA})
    return opener.open(req, timeout=timeout).read().decode("utf-8", "replace")


def warm_csrf(cat="156"):
    html = http_get(f"{BASE}/front/product/category/{cat}")
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    _csrf["tok"] = m.group(1) if m else None
    return html


def list_page(cat, page, psize=25):
    data = urllib.parse.urlencode({
        "cateIdx": cat, "brandIdx": "0", "page": str(page),
        "psize": str(psize), "psort": "new", "pword": ""}).encode()
    req = urllib.request.Request(
        f"{BASE}/front/product/product_list.ajax", data=data, headers={
            "User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": _csrf["tok"],
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": f"{BASE}/front/product/category/{cat}",
            "Accept": "application/json, text/javascript, */*; q=0.01"})
    return opener.open(req, timeout=30).read().decode("utf-8", "replace")


def list_page_retry(cat, page, tries=3):
    """일시 오류는 재시도. 403 은 csrf 재워밍."""
    last = None
    for attempt in range(tries):
        try:
            return list_page(cat, page)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 403:
                try:
                    warm_csrf(cat)
                except Exception:
                    pass
            time.sleep(0.6 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(0.6 * (attempt + 1))
    raise last


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


CODE_RE = re.compile(r"^[A-Z]{1,6}[A-Z0-9]*\d[A-Z0-9]*$")


def split_code(name_raw):
    if "_" in name_raw:
        head, tail = name_raw.rsplit("_", 1)
        tail = tail.strip()
        if CODE_RE.match(tail):
            return head.strip(), tail
    return name_raw, ""


def parse_cards(frag):
    out = []
    blocks = re.split(r'<div class="prd-cont">', frag)[1:]
    for b in blocks:
        m = re.search(r'/front/product/(\d+)', b)
        if not m:
            continue
        pid = m.group(1)
        nm = re.search(r'<p class="name">(.*?)</p>', b, re.S)
        name_raw = H.unescape(strip_tags(nm.group(1))) if nm else ""
        pr = re.search(r'<span class="price">([\d,]+)\s*원', b)
        price = pr.group(1).replace(",", "") if pr else ""
        name, code = split_code(name_raw)
        out.append({"id": pid, "name": name, "style_code": code, "price": price})
    return out


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


def parse_detail(pid):
    html = http_get(f"{BASE}/front/product/{pid}")
    fields = {"color": "", "sizes": "", "origin": "", "material": "",
              "mfg_date": "", "style_code": "", "name": ""}
    og = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    if og:
        nm, code = split_code(H.unescape(og.group(1).strip()))
        fields["name"], fields["style_code"] = nm, code
    for th, td in th_td_pairs(html):
        if not td:
            continue
        if not fields["material"] and "소재" in th:
            fields["material"] = td
        elif not fields["color"] and "색상" in th:
            fields["color"] = td
        elif not fields["sizes"] and ("치수" in th or "사이즈" in th):
            parts = [p.strip() for p in re.split(r"[,/]", td) if p.strip()]
            fields["sizes"] = "|".join(parts)
        elif not fields["origin"] and ("제조국" in th or "원산지" in th):
            fields["origin"] = td
        elif not fields["mfg_date"] and ("제조연월" in th or "제조년월" in th):
            fields["mfg_date"] = td
    return fields


def discover_categories():
    html = http_get(f"{BASE}/front/product/category/156")
    cats = {}
    for m in re.finditer(
            r'/front/product/category/(\d+)"[^>]*>(.*?)</a>', html, re.S):
        cid = m.group(1)
        txt = strip_tags(m.group(2))
        if txt and cid not in cats:
            cats[cid] = txt
    return cats


# ---------------- Phase 1: list crawl (per category, full pagination) -------
def phase1():
    if os.path.exists(LIST_CKPT):
        with open(LIST_CKPT, encoding="utf-8") as f:
            data = json.load(f)
        print(f"[phase1] resume from ckpt: {len(data['meta'])} products",
              file=sys.stderr)
        return data
    warm_csrf("156")
    cats = discover_categories()
    print(f"[phase1] discovered {len(cats)} categories", file=sys.stderr)
    meta = {}                 # id -> {name, style_code, price}
    cat_report = {}           # cat -> {name, pls2, collected}
    for ci, (cat, cname) in enumerate(cats.items(), 1):
        seen_ids = set()
        pls2 = None
        page = 1
        incomplete = False
        while True:
            try:
                frag = list_page_retry(cat, page)
            except Exception as e:
                print(f"[warn] list {cat} p{page} FAILED: {e}", file=sys.stderr)
                incomplete = True
                break
            if pls2 is None:
                mt = re.search(r'var\s+pls2\s*=\s*"?(\d+)"?', frag)
                pls2 = int(mt.group(1)) if mt else None
            cards = parse_cards(frag)
            if not cards:
                break
            for c in cards:
                seen_ids.add(c["id"])
                if c["id"] not in meta:
                    meta[c["id"]] = {"name": c["name"],
                                     "style_code": c["style_code"],
                                     "price": c["price"]}
            # 마지막 페이지 판정: 카드 < 25 또는 pls2 도달
            if len(cards) < 25:
                break
            if pls2 is not None and len(seen_ids) >= pls2:
                break
            page += 1
            if page > 400:       # 안전 상한
                incomplete = True
                break
            time.sleep(0.12)
        cat_report[cat] = {"name": cname, "pls2": pls2,
                           "collected": len(seen_ids),
                           "incomplete": incomplete}
        short = ""
        if pls2 and len(seen_ids) < pls2:
            short = f"  *SHORT {len(seen_ids)}/{pls2}*"
        print(f"[phase1] {ci}/{len(cats)} cat {cat}({cname}) "
              f"pages={page} ids={len(seen_ids)} pls2={pls2}{short} "
              f"| total uniq={len(meta)}", file=sys.stderr)
        # assign best (most specific) category to each id
        for pid in seen_ids:
            cur = meta[pid].get("_cat_pls2")
            if cur is None or (pls2 is not None and pls2 < cur):
                meta[pid]["category"] = cname
                meta[pid]["_cat_pls2"] = pls2 if pls2 is not None else 10**9
        time.sleep(0.1)
    data = {"meta": meta, "cat_report": cat_report,
            "n_categories": len(cats)}
    with open(LIST_CKPT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


# ---------------- Phase 2: detail crawl (per unique id) ---------------------
def phase2(meta):
    done = {}
    if os.path.exists(ROWS_CKPT):
        with open(ROWS_CKPT, encoding="utf-8") as f:
            for line in f:
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
               "price": m.get("price", ""),
               "category": m.get("category", ""),
               "color": "", "sizes": "", "origin": "",
               "material": "", "mfg_date": ""}
        try:
            d = parse_detail(pid)
            # 상세 og:title 의 코드/이름을 우선(리스트와 동일하지만 안전)
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
        time.sleep(0.22)
    fout.close()
    return done, capped


# ---------------- Phase 3: assemble CSV (dedup by style_code|id) ------------
def phase3(rows):
    by_key = {}
    order = []
    for pid, r in rows.items():
        key = r.get("style_code") or ("id:" + pid)
        if key not in by_key:
            by_key[key] = r
            order.append(key)
    bak = CSV_PATH + ".prev.bak"
    if os.path.exists(CSV_PATH):
        try:
            os.replace(CSV_PATH, bak)
        except Exception:
            pass
    n = 0
    filled = {k: 0 for k in HEADER}
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for key in order:
            r = by_key[key]
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
                "url": f"{BASE}/front/product/{r['id']}"})
            w.writerow(row)
            n += 1
            for k, v in row.items():
                if str(v).strip():
                    filled[k] += 1
    return n, filled


def main():
    t0 = time.time()
    data = phase1()
    meta = data["meta"]
    cat_report = data["cat_report"]
    rows, capped = phase2(meta)
    n, filled = phase3(rows)

    # reconciliation
    shorts = {c: r for c, r in cat_report.items()
              if r.get("pls2") and r["collected"] < r["pls2"]}
    incompletes = {c: r for c, r in cat_report.items() if r.get("incomplete")}
    empty_code = sum(1 for r in rows.values() if not r.get("style_code"))
    total_pages = None  # not tracked aggregate; per-cat in report
    meta_out = {
        "rows_after": n,
        "unique_ids": len(meta),
        "n_categories": data["n_categories"],
        "empty_style_code_rows": empty_code,
        "capped_at_5000": capped,
        "short_categories": shorts,
        "incomplete_categories": list(incompletes.keys()),
        "elapsed_sec": round(time.time() - t0, 1),
        "filled": filled,
    }
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta_out, f, ensure_ascii=False, indent=2)

    print("\n=== SUMMARY ===")
    print("rows (after dedup):", n)
    print("unique ids:", len(meta))
    print("categories crawled:", data["n_categories"])
    print("empty style_code rows:", empty_code)
    print("capped@5000:", capped)
    print("short categories (collected<pls2):", len(shorts))
    for c, r in list(shorts.items())[:20]:
        print(f"   cat {c}({r['name']}): {r['collected']}/{r['pls2']}")
    print("incomplete categories:", list(incompletes.keys()))
    print("csv:", CSV_PATH)
    print("filled:")
    for k in HEADER:
        print(f"  {k}: {filled[k]}/{n}")


if __name__ == "__main__":
    main()
