#!/usr/bin/env python3
"""
네파 공식몰(nplus.co.kr, Styleship/ASP) 서버측 상품 표본 추출 — stdlib만.

후크: jsonld(상세 name/sku/price/brand) + dataLayer ecommerce(category/gender)
      + DOM(color/sizes/origin/material/mfg_date).
리스트: /product/list.asp?cno={cno}&page={p}  (20개/page, 추천배너 4개 제외)
상세  : /product/view.asp?pno={pno}
출력  : outputs/extract_brand_nepa.csv
헤더  : source,brand,style_code,name,color,price,currency,category,gender,sizes,origin,material,mfg_date,url
"""
import csv, gzip, html as H, json, os, re, sys, time
import urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
os.makedirs(OUT, exist_ok=True)
CSV_PATH = os.path.join(OUT, "extract_brand_nepa.csv")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADER = ["source","brand","style_code","name","color","price","currency",
          "category","gender","sizes","origin","material","mfg_date","url"]
BANNER_PNOS = {"43065","43263","42975","41724"}  # 모든 리스트 페이지에 반복되는 추천 배너


def http_get(url, retries=2, timeout=30):
    url = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            })
            r = urllib.request.urlopen(req, timeout=timeout)
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return r.status, data.decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(0.6)
    raise last


def collect_pnos(cno, max_pages):
    pnos = []
    est = None
    for p in range(1, max_pages + 1):
        try:
            st, html = http_get(f"https://www.nplus.co.kr/product/list.asp?cno={cno}&page={p}")
        except Exception as e:
            print(f"  list cno={cno} p={p} ERR {e}", file=sys.stderr)
            break
        if est is None:
            m = re.search(r'id="paging"[^>]*data-tot="(\d+)"', html)
            if m:
                est = int(m.group(1))
        found = [x for x in dict.fromkeys(re.findall(r'view\.asp\?pno=(\d+)', html))
                 if x not in BANNER_PNOS]
        real = [x for x in found if x not in pnos]
        pnos.extend(real)
        print(f"  list cno={cno} p={p}: +{len(real)} (total {len(pnos)})")
        if not real:
            break
        time.sleep(0.3)
    return pnos, est


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
    """상품정보고시 + 소재 dt/dd 전부 dict로."""
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
    # 단일 색상이거나 활성 표시 없음 -> 첫 색상
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
    parts = [p for p in re.split(r'[|>/]', m.group(1)) if p.strip()]
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


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "probe":
        for pno in sys.argv[2:]:
            row, bld = parse_detail(pno)
            print(f"\n--- pno={pno} (jsonld brand={bld}) ---")
            for k in HEADER:
                print(f"   {k:11}= {row[k]}")
            time.sleep(0.3)
        return

    cats = [("100", 3), ("200", 3)]  # MEN, WOMEN 각 3페이지
    pnos, ests = [], {}
    for cno, mp in cats:
        ps, est = collect_pnos(cno, mp)
        ests[cno] = est
        for p in ps:
            if p not in pnos:
                pnos.append(p)
    pnos = pnos[:120]
    print(f"\ncollected {len(pnos)} unique pnos; est MEN={ests.get('100')} WOMEN={ests.get('200')}")

    rows, skipped = [], []
    for i, pno in enumerate(pnos, 1):
        try:
            row, bld = parse_detail(pno)
            if bld and "NEPA" not in bld:
                skipped.append((pno, f"brand={bld}"))
                print(f"  [{i}/{len(pnos)}] pno={pno} SKIP non-NEPA brand={bld}")
                continue
            rows.append(row)
            if i % 10 == 0 or i <= 3:
                print(f"  [{i}/{len(pnos)}] pno={pno} {row['style_code']} {row['name'][:24]} "
                      f"{row['color']}/{row['price']}/{row['origin']}/{row['mfg_date']}")
        except Exception as e:
            skipped.append((pno, str(e)))
            print(f"  [{i}/{len(pnos)}] pno={pno} ERR {e}", file=sys.stderr)
        time.sleep(0.3)

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    filled = {k: sum(1 for r in rows if str(r.get(k, "")).strip()) for k in HEADER}
    print(f"\nWROTE {len(rows)} rows -> {CSV_PATH}; skipped={len(skipped)}")
    print("filled per column:", json.dumps(filled, ensure_ascii=False))


if __name__ == "__main__":
    main()
