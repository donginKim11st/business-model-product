#!/usr/bin/env python3
"""
블랙야크 공식몰(byn.kr) 서버측 상품 표본 추출 (stdlib only).

플랫폼: 자체 PHP (BYN MALL). 후크: DOM.
  · 리스트/카테고리: /blackyak/shop/big_section.php?cno1={N}&page={p}  (20개/page)
  · 상세:           /blackyak/shop/detail.php?pno={HASH}
추출 전략(JSON-LD 없음 → DOM):
  · 상품정보제공고시 표(상품코드/소재/색상/사이즈/제조국/제조년월) = 신뢰 백본
  · recopick 로그 객체(items:[{...}]) = 이름/정가/판매가/통화/카테고리 보너스
출력: outputs/extract_brand_blackyak.csv
  헤더: source,brand,style_code,name,color,price,currency,category,gender,sizes,origin,material,mfg_date,url
"""
import csv
import html as _html
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BASE = "https://www.byn.kr"
LIST = "/blackyak/shop/big_section.php?cno1={cno}&page={page}"
DETAIL = "/blackyak/shop/detail.php?pno={pno}"

HEADER = ["source", "brand", "style_code", "name", "color", "price",
          "currency", "category", "gender", "sizes", "origin",
          "material", "mfg_date", "url"]


def http_get(url, retries=2, timeout=20):
    # 한글 슬러그 등 비-ASCII 경로 퍼센트 인코딩
    url = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


_PNO_RE = re.compile(r'detail\.php\?pno=([0-9A-F]+)')
_TOTAL_RE = re.compile(r'TotalCnt"?>\s*([0-9,]+)')
# 상품정보제공고시: <th>...<span ...>라벨</span></th> <td ...>값</td>
_ROW_RE = re.compile(
    r'<th[^>]*>.*?<span[^>]*>([^<]+)</span>\s*</th>\s*<td[^>]*>(.*?)</td>', re.S)
_RECO_RE = re.compile(r'items:\s*\[\{(.*?)\}\s*\]', re.S)


def _clean(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    s = _html.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def _reco_field(block, key):
    m = re.search(r'\b' + re.escape(key) + r'\s*:\s*"([^"]*)"', block)
    return m.group(1).strip() if m else ""


def _dedupe_csv(value):
    seen, keep = set(), []
    for part in value.split(','):
        p = part.strip()
        if p and p not in seen:
            seen.add(p)
            keep.append(p)
    return ', '.join(keep)


_GENDER_MAP = [
    ("남녀공용", "남녀공용"), ("공용", "남녀공용"),
    ("남성", "남성"), ("여성", "여성"),
    ("키즈", "아동"), ("아동", "아동"), ("주니어", "아동"), ("유아", "아동"),
]


def _gender_from_name(name):
    head = name[:6]
    for kw, label in _GENDER_MAP:
        if head.startswith(kw) or kw in head:
            return label
    return ""


def list_pnos(cno, page):
    html = http_get(BASE + LIST.format(cno=cno, page=page))
    pnos, seen = [], set()
    for m in _PNO_RE.finditer(html):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            pnos.append(m.group(1))
    total = ""
    mt = _TOTAL_RE.search(html)
    if mt:
        total = mt.group(1)
    return pnos, total


def parse_detail(pno):
    url = BASE + DETAIL.format(pno=pno)
    html = http_get(url)

    # --- 상품정보제공고시 표 ---
    table = {}
    i = html.find('제조국')
    if i != -1:
        seg = html[i - 4000:i + 2000]
        for label, val in _ROW_RE.findall(seg):
            table[label.strip()] = _clean(val)

    # --- recopick 로그 객체 ---
    reco = ""
    mr = _RECO_RE.search(html)
    if mr:
        reco = mr.group(1)

    # 이름: recopick title → og:description, 후행 #N 제거
    name = _reco_field(reco, "title")
    if not name:
        m = re.search(r'og:description"\s+content="([^"]*)"', html)
        name = _html.unescape(m.group(1).strip()) if m else ""
    name = re.sub(r'#\d+\s*$', '', name).strip()

    # 가격: 판매가(sale_price) 우선, 없으면 정가(price)
    price = _reco_field(reco, "sale_price") or _reco_field(reco, "price")
    currency = _reco_field(reco, "sale_currency") or _reco_field(reco, "currency") or "KRW"
    category = _reco_field(reco, "c1")

    style_code = table.get("상품코드", "")
    color = table.get("색상", "")
    sizes_raw = table.get("사이즈", "")
    sizes = "|".join(s.strip() for s in sizes_raw.split(',') if s.strip())
    origin = table.get("제조국", "")
    material = _dedupe_csv(table.get("소재", ""))
    mfg_date = table.get("제조년월", "")
    gender = _gender_from_name(name)

    return {
        "source": "blackyak",
        "brand": "블랙야크",
        "style_code": style_code,
        "name": name,
        "color": color,
        "price": price,
        "currency": currency,
        "category": category,
        "gender": gender,
        "sizes": sizes,
        "origin": origin,
        "material": material,
        "mfg_date": mfg_date,
        "url": url,
    }


def main():
    cno = int(sys.argv[1]) if len(sys.argv) > 1 else 1012
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    cap = int(sys.argv[3]) if len(sys.argv) > 3 else 120

    os.makedirs(OUT, exist_ok=True)
    all_pnos, seen = [], set()
    est_total = ""
    for p in range(1, max_pages + 1):
        try:
            pnos, total = list_pnos(cno, p)
        except Exception as e:  # noqa: BLE001
            print(f"[list] page {p} 실패: {e}", file=sys.stderr)
            continue
        if total and not est_total:
            est_total = total
        new = [x for x in pnos if x not in seen]
        for x in new:
            seen.add(x)
        all_pnos.extend(new)
        print(f"[list] cno={cno} page={p}: +{len(new)} (누적 {len(all_pnos)})",
              file=sys.stderr)
        if not new or len(all_pnos) >= cap:
            break
        time.sleep(0.4)

    all_pnos = all_pnos[:cap]
    rows, fail = [], 0
    for n, pno in enumerate(all_pnos, 1):
        try:
            rows.append(parse_detail(pno))
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[detail] {pno} 실패: {e}", file=sys.stderr)
        if n % 20 == 0:
            print(f"[detail] {n}/{len(all_pnos)} ...", file=sys.stderr)
        time.sleep(0.35)

    path = os.path.join(OUT, "extract_brand_blackyak.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    filled = {k: sum(1 for r in rows if str(r.get(k, "")).strip()) for k in HEADER}
    print(f"\n=== 완료 ===\n행수: {len(rows)} (실패 {fail}) | est_total(cno={cno}): {est_total}")
    print("채워진 컬럼:", {k: v for k, v in filled.items() if v})
    print("경로:", path)


if __name__ == "__main__":
    main()
