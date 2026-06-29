#!/usr/bin/env python3
"""
네파 공식몰(nplus.co.kr) 전수(全數) 추출 — stdlib(+ curl_cffi fallback).

전 카테고리(nav의 모든 cno) × 전 페이지(빈 페이지/마지막까지) 크롤.
리스트: /product/list.asp?cno={cno}&page={p}
상세  : /product/view.asp?pno={pno}
출력  : outputs/extract_brand_nepa.csv (utf-8-sig)
헤더  : source,brand,style_code,name,color,price,currency,category,gender,sizes,origin,material,mfg_date,url

특징:
- 전역 배너 pno(추천)는 overflow 페이지(page=999)에서 동적 감지 후 제외.
- 페이지네이션 종료는 "HTTP 200 + 신규 pno 0"일 때만. HTTP 에러는 종료로 보지 않음(재시도/로그).
- data-tot(카테고리 총 상품수)을 완전성 게이트로 사용: 수집 pno수 vs data-tot 비교.
- 브랜드 필터: jsonld brand에 NEPA 미포함(PYRENEX/ZUCCHERO/CUISSE 등 유통 브랜드)이면 skip.
- 재개 가능: 페이즈1=outputs/_nepa_pnos.json, 페이즈2=outputs/_nepa_rows.jsonl(pno마다 append).
- 상한 5000 kept에서 중단.
"""
import csv, gzip, html as H, json, os, re, sys, time
import urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
os.makedirs(OUT, exist_ok=True)
CSV_PATH = os.path.join(OUT, "extract_brand_nepa.csv")
PNOS_STATE = os.path.join(OUT, "_nepa_pnos.json")
ROWS_JSONL = os.path.join(OUT, "_nepa_rows.jsonl")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
CAP = 5000          # phase2 kept-row cap (task: stop at 5000 if >5000)
COLLECT_CAP = 6800  # phase1 pno-enumeration cap (buffer over CAP for brand-skips + style_code dedup)
HDRS = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
        "Accept": "text/html,application/xhtml+xml"}


def is_outlet(cno):
    """OUTLET 세분류(410~441). 410 단독 data-tot=6144(과거시즌). 현 카탈로그보다 후순위."""
    try:
        return 410 <= int(cno) <= 441
    except Exception:
        return False


def order_cnos(cnos):
    """현 카탈로그(비-OUTLET) 먼저, OUTLET 블록(410~441) 맨 뒤. 캡이 현 카탈로그를 보장."""
    cur = [c for c in cnos if not is_outlet(c)]
    out = sorted([c for c in cnos if is_outlet(c)], key=int)
    return cur + out

try:
    from curl_cffi import requests as _cffi
except Exception:
    _cffi = None


def http_get(url, retries=4, timeout=30):
    """성공 시 (200, html). 모든 재시도 실패 시 예외 raise (절대 빈 페이지로 위장 안 함)."""
    q = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(q, headers=HDRS)
            r = urllib.request.urlopen(req, timeout=timeout)
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return r.status, data.decode("utf-8", "replace")
        except Exception as e:
            last = e
            if _cffi is not None:
                try:
                    resp = _cffi.get(q, headers=HDRS, impersonate="chrome", timeout=timeout)
                    if resp.status_code == 200:
                        return 200, resp.text
                    last = Exception(f"cffi status {resp.status_code}")
                except Exception as e2:
                    last = e2
            time.sleep(0.7 * (attempt + 1))
    raise last


# ----------------------------- detail parsing -----------------------------
def _jsonld(html):
    for b in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                        html, re.S | re.I):
        try:
            d = json.loads(b)
        except Exception:
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and "Product" in str(it.get("@type", "")):
                return it
    return None


def _price(offers):
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return ""
    for k in ("price", "lowPrice", "highPrice"):
        v = offers.get(k)
        if v not in (None, ""):
            return str(v).replace(",", "")
    return ""


def _notice(html):
    d = {}
    for dt, dd in re.findall(r'<dt>(.*?)</dt>\s*<dd>\s*(.*?)\s*</dd>', html, re.S):
        key = re.sub(r'\s+', ' ', H.unescape(re.sub(r'<[^>]+>', '', dt))).strip()
        val = re.sub(r'\s+', ' ', H.unescape(re.sub(r'<[^>]+>', '', dd))).strip()
        if key and val and key not in d:
            d[key] = val
    return d


def _pick(d, *keys):
    for k in keys:
        for dk, dv in d.items():
            if k in dk:
                return dv
    return ""


def _color(html):
    cs = html.find("_color")
    block = html[cs:html.find("_size", cs)] if cs >= 0 else html
    for li in re.findall(r'<li class="([^"]*)">.*?data-color-name="([^"]*)"', block, re.S):
        cls, name = li
        if "on" in cls.split():
            return H.unescape(name).strip()
    m = re.search(r'data-color-name="([^"]*)"', block)
    return H.unescape(m.group(1)).strip() if m else ""


def _sizes(html):
    ss = html.find("_size")
    block = html[ss:ss + 4000] if ss >= 0 else ""
    return [s for s in dict.fromkeys(re.findall(r'data-size="([^"]*)"', block))]


def _cat_gender(html):
    m = re.search(r'"category"\s*:\s*"([^"]*)"', html)
    if not m:
        return "", ""
    raw = m.group(1)
    seg = raw.split("|")
    gender = seg[0].strip() if seg else ""
    cat = " > ".join(s.strip() for s in seg[1:]) if len(seg) > 1 else raw
    return cat, gender


GENDER_PREFIX = [("여성", "WOMEN"), ("남성", "MEN"), ("공용", "UNISEX"),
                 ("아동", "KIDS"), ("키즈", "KIDS"), ("주니어", "KIDS"), ("유아", "KIDS")]


def parse_detail(pno):
    url = f"https://www.nplus.co.kr/product/view.asp?pno={pno}"
    st, html = http_get(url)
    ld = _jsonld(html)
    name = (ld.get("name") if ld else "") or ""
    name = re.sub(r'\s+', ' ', H.unescape(name)).strip()
    sku = (ld.get("sku") if ld else "") or ""
    brand_ld = ""
    if ld:
        b = ld.get("brand")
        brand_ld = b.get("name") if isinstance(b, dict) else (b or "")
    price = _price(ld.get("offers")) if ld else ""
    currency = ""
    if ld and isinstance(ld.get("offers"), dict):
        currency = ld["offers"].get("priceCurrency", "")
    elif ld and isinstance(ld.get("offers"), list) and ld["offers"]:
        currency = ld["offers"][0].get("priceCurrency", "")

    cat, gender = _cat_gender(html)
    if not gender:
        for pre, g in GENDER_PREFIX:
            if name.startswith(pre):
                gender = g
                break
    notice = _notice(html)
    origin = _pick(notice, "원산지", "제조국")
    mfg = _pick(notice, "제조년월", "제조일자", "제조연월")
    material = _pick(notice, "소재 및 관리", "소재", "혼용률")
    color = _color(html)
    sizes = _sizes(html)

    return {
        "source": "nepa",
        "brand": "네파",
        "style_code": sku,
        "name": name,
        "color": color,
        "price": price,
        "currency": currency or ("KRW" if price else ""),
        "category": cat,
        "gender": gender,
        "sizes": "|".join(sizes),
        "origin": origin,
        "material": material,
        "mfg_date": mfg,
        "url": url,
    }, (brand_ld or "").upper()


# ----------------------------- phase 1: collect pnos -----------------------------
def discover_cnos():
    st, html = http_get("https://www.nplus.co.kr/product/list.asp?cno=100&page=1")
    cnos = sorted(set(re.findall(r'cno=(\d+)', html)), key=int)
    return cnos


def detect_banners():
    st, html = http_get("https://www.nplus.co.kr/product/list.asp?cno=100&page=999")
    return [x for x in dict.fromkeys(re.findall(r'view\.asp\?pno=(\d+)', html))]


def page_pnos(cno, p):
    st, html = http_get(f"https://www.nplus.co.kr/product/list.asp?cno={cno}&page={p}")
    tot = None
    m = re.search(r'data-tot="(\d+)"', html)
    if m:
        tot = int(m.group(1))
    pnos = [x for x in dict.fromkeys(re.findall(r'view\.asp\?pno=(\d+)', html))]
    return pnos, tot


def collect():
    if os.path.exists(PNOS_STATE):
        state = json.load(open(PNOS_STATE, encoding="utf-8"))
    else:
        state = {"banners": [], "cnos": [], "cats_done": {}, "errors": {}, "pnos": []}

    if not state["banners"]:
        state["banners"] = detect_banners()
        print(f"banners: {state['banners']}")
    banners = set(state["banners"])

    if not state["cnos"]:
        state["cnos"] = discover_cnos()
    cnos = order_cnos(state["cnos"])  # current catalog first, OUTLET last
    print(f"discovered {len(cnos)} cnos (outlet block last)")

    pno_set = set(state["pnos"])
    pno_list = list(state["pnos"])
    state.setdefault("collect_capped", False)

    for cno in cnos:
        if cno in state["cats_done"]:
            continue
        if len(pno_list) >= COLLECT_CAP:
            state["collect_capped"] = True
            print(f"  COLLECT_CAP {COLLECT_CAP} reached ({len(pno_list)} pnos); "
                  f"stopping enumeration before cno={cno}")
            break
        seen_local = set()
        cat_collected = []
        data_tot = None
        pages = 0
        err = None
        p = 1
        while True:
            try:
                pnos, tot = page_pnos(cno, p)
            except Exception as e:
                err = f"p{p}: {e}"
                print(f"  ! cno={cno} p={p} ERROR {e}", file=sys.stderr)
                break
            if data_tot is None and tot is not None:
                data_tot = tot
            real = [x for x in pnos if x not in banners]
            new = [x for x in real if x not in seen_local]
            if not new:
                break  # HTTP 200 + zero new pno = true end
            seen_local.update(new)
            cat_collected.extend(new)
            pages = p
            if len(pno_set) + len(cat_collected) >= COLLECT_CAP:
                state["collect_capped"] = True
                print(f"  COLLECT_CAP reached mid cno={cno} p={p}")
                break
            p += 1
            time.sleep(0.18)
        # merge into global
        for x in cat_collected:
            if x not in pno_set:
                pno_set.add(x)
                pno_list.append(x)
        rec = {"got": len(seen_local), "data_tot": data_tot, "pages": pages}
        if err:
            state["errors"][cno] = err
            print(f"  cno={cno}: got {len(seen_local)} pages {pages} tot={data_tot} ERR")
        else:
            state["cats_done"][cno] = rec
            flag = ""
            if data_tot is not None and abs(len(seen_local) - data_tot) > 4:
                flag = f"  <-- MISMATCH (got {len(seen_local)} vs tot {data_tot})"
            print(f"  cno={cno}: got {len(seen_local)} pages {pages} tot={data_tot}{flag}")
        state["pnos"] = pno_list
        json.dump(state, open(PNOS_STATE, "w", encoding="utf-8"), ensure_ascii=False)

    print(f"\nPHASE1 done: {len(pno_list)} unique pnos; errors={list(state['errors'])}")
    return state


# ----------------------------- phase 2: fetch details -----------------------------
def load_done():
    done = set()
    kept = 0
    if os.path.exists(ROWS_JSONL):
        for line in open(ROWS_JSONL, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            done.add(rec["pno"])
            if rec.get("keep"):
                kept += 1
    return done, kept


def details(state):
    pnos = state["pnos"]
    done, kept = load_done()
    print(f"PHASE2: {len(pnos)} pnos total, {len(done)} already processed, {kept} kept")
    capped = False
    f = open(ROWS_JSONL, "a", encoding="utf-8")
    try:
        for i, pno in enumerate(pnos, 1):
            if pno in done:
                continue
            if kept >= CAP:
                capped = True
                print(f"  CAP {CAP} reached; stopping detail fetch")
                break
            try:
                row, bld = parse_detail(pno)
            except Exception as e:
                f.write(json.dumps({"pno": pno, "keep": False, "err": str(e)},
                                   ensure_ascii=False) + "\n")
                f.flush()
                print(f"  [{i}] pno={pno} ERR {e}", file=sys.stderr)
                time.sleep(0.2)
                continue
            keep = not (bld and "NEPA" not in bld)
            rec = {"pno": pno, "keep": keep, "brand_ld": bld}
            if keep:
                rec["row"] = row
                kept += 1
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            if i % 50 == 0 or kept <= 3:
                print(f"  [{i}/{len(pnos)}] pno={pno} keep={keep} kept={kept} "
                      f"{row['style_code']} {row['name'][:24]}")
            time.sleep(0.2)
    finally:
        f.close()
    return capped


# ----------------------------- finalize CSV -----------------------------
def finalize():
    rows = []
    seen_key = set()
    kept = 0
    skipped_brands = {}
    errors = 0
    for line in open(ROWS_JSONL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("err"):
            errors += 1
            continue
        if not rec.get("keep"):
            b = rec.get("brand_ld") or "?"
            skipped_brands[b] = skipped_brands.get(b, 0) + 1
            continue
        kept += 1
        r = rec["row"]
        key = r["style_code"] or r["url"]  # never crush blank skus together
        if key in seen_key:
            continue
        seen_key.add(key)
        rows.append(r)
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return rows, kept, skipped_brands, errors


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    state = None
    if mode in ("all", "collect"):
        state = collect()
    if mode in ("all", "details"):
        if state is None:
            state = json.load(open(PNOS_STATE, encoding="utf-8"))
        capped = details(state)
    if mode in ("all", "details", "finalize"):
        rows, kept, skipped_brands, errors = finalize()
        print(f"\nFINAL: {len(rows)} unique rows -> {CSV_PATH}")
        print(f"  kept(pre-dedup)={kept} errors={errors}")
        print(f"  skipped non-NEPA brands: {json.dumps(skipped_brands, ensure_ascii=False)}")
        filled = {k: sum(1 for r in rows if str(r.get(k, '')).strip()) for k in HEADER}
        print(f"  filled per col: {json.dumps(filled, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
