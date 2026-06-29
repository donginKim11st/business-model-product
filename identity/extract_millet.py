#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
밀레(MILLET) 공식몰 서버측 상품 표본 추출 (stdlib only).

플랫폼: 자체몰 (/front/product/{id}), hook=dom
리스트: POST /front/product/product_list.ajax  (세션쿠키 + X-CSRF-TOKEN 필요)
  params: cateIdx, brandIdx=0, page, psize=25, psort=new, pword=
  -> 상품카드 HTML 조각 + `var pls2="<전체개수>"` (카테고리 카운트)
상세: GET /front/product/{id} -> 상품정보제공고시 table(제품소재/색상/치수/제조국/제조연월)

전략(advisor 반영):
  - category 는 추론하지 않고 '타입 카테고리'를 크롤하여 카테고리명을 그대로 사용(출처 있는 값)
  - gender 는 상품명에 남성/여성/키즈/주니어/MEN/WOMEN 토큰이 실제로 있을 때만 채움(없으면 공란)
  - price 는 리스트 카드의 판매가(.price)에서 취득(상세의 0원 파싱트랩 회피)
  - 행마다 즉시 CSV flush, 상품별 try/except, ~0.3s sleep, 403 시 CSRF 재워밍
"""
import csv, html as H, http.cookiejar, os, re, sys, time
import urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
os.makedirs(OUT, exist_ok=True)
CSV_PATH = os.path.join(OUT, "extract_brand_millet.csv")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE = "https://www.millet.co.kr"

HEADER = ["source","brand","style_code","name","color","price","currency",
          "category","gender","sizes","origin","material","mfg_date","url"]

# 타입 카테고리(이름=category 값으로 사용). 신발 카테고리 포함(과제 권고).
CATEGORIES = [
    ("541", "트레킹화"),
    ("162", "자켓"),
    ("423", "배낭"),
    ("206", "쿨티셔츠"),
]
TARGET = 120          # 표본 상한(고유 품번 기준)
PER_CAT_PAGES = 8     # 카테고리당 최대 페이지(25*8=200)

opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def enc(url):
    return urllib.parse.quote(url, safe=":/?=&%#+,")


def http_get(url, timeout=30):
    req = urllib.request.Request(enc(url), headers={"User-Agent": UA})
    return opener.open(req, timeout=timeout).read().decode("utf-8", "replace")


def warm_csrf(cat):
    """카테고리 페이지를 받아 쿠키 세팅 + csrf-token 반환."""
    html = http_get(f"{BASE}/front/product/category/{cat}")
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    return m.group(1) if m else None


def list_page(cat, page, csrf, psize=25):
    data = urllib.parse.urlencode({
        "cateIdx": cat, "brandIdx": "0", "page": str(page),
        "psize": str(psize), "psort": "new", "pword": ""}).encode()
    req = urllib.request.Request(
        f"{BASE}/front/product/product_list.ajax", data=data, headers={
            "User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": csrf,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": f"{BASE}/front/product/category/{cat}",
            "Accept": "application/json, text/javascript, */*; q=0.01"})
    return opener.open(req, timeout=30).read().decode("utf-8", "replace")


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def parse_cards(frag):
    """리스트 조각 -> [{id,url,name,style_code,price,colors}]"""
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
        colors = len(re.findall(r'class="btn icon color-chip"', b))
        name, code = split_code(name_raw)
        out.append({"id": pid, "url": f"{BASE}/front/product/{pid}",
                    "name": name, "style_code": code, "price": price,
                    "color_chips": colors})
    return out


CODE_RE = re.compile(r"^[A-Z]{1,6}[A-Z0-9]*\d[A-Z0-9]*$")


def split_code(name_raw):
    """상품명 끝 '_모델코드' 분리. 코드형식이 아니면 분리하지 않음."""
    if "_" in name_raw:
        head, tail = name_raw.rsplit("_", 1)
        tail = tail.strip()
        if CODE_RE.match(tail):
            return head.strip(), tail
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
    out = []
    for th, td in pairs:
        out.append((strip_tags(th), H.unescape(strip_tags(td))))
    return out


def parse_detail(pid):
    """상세 상품정보제공고시 -> color/sizes/origin/material/mfg_date"""
    html = http_get(f"{BASE}/front/product/{pid}")
    fields = {"color": "", "sizes": "", "origin": "", "material": "",
              "mfg_date": ""}
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


def main():
    cards = {}        # key -> card dict (+ category); key = style_code or id
    est_per_cat = {}  # cat -> pls2 (상품ID 기준, 컬러변형 포함)
    order = []
    for cat, catname in CATEGORIES:
        if len(cards) >= TARGET:
            break
        try:
            csrf = warm_csrf(cat)
        except Exception as e:
            print(f"[warn] warm {cat}: {e}", file=sys.stderr)
            continue
        for page in range(1, PER_CAT_PAGES + 1):
            if len(cards) >= TARGET:
                break
            try:
                frag = list_page(cat, page, csrf)
            except urllib.error.HTTPError as e:
                if e.code == 403:  # CSRF/세션 만료 -> 재워밍 후 1회 재시도
                    csrf = warm_csrf(cat)
                    try:
                        frag = list_page(cat, page, csrf)
                    except Exception as e2:
                        print(f"[warn] list {cat} p{page}: {e2}", file=sys.stderr)
                        break
                else:
                    print(f"[warn] list {cat} p{page}: {e}", file=sys.stderr)
                    break
            except Exception as e:
                print(f"[warn] list {cat} p{page}: {e}", file=sys.stderr)
                break
            if cat not in est_per_cat:
                mt = re.search(r'var\s+pls2\s*=\s*"?(\d+)"?', frag)
                est_per_cat[cat] = int(mt.group(1)) if mt else None
            page_cards = parse_cards(frag)
            if not page_cards:
                break
            new_on_page = 0
            for c in page_cards:
                # 컬러변형은 같은 품번 -> 품번 기준 1행으로 dedup(컬러는 색상 컬럼에 전부 표기)
                key = c["style_code"] or ("id:" + c["id"])
                if key in cards:
                    continue
                c["category"] = catname
                cards[key] = c
                order.append(key)
                new_on_page += 1
                if len(cards) >= TARGET:
                    break
            print(f"[list] cat {cat}({catname}) p{page}: "
                  f"+{new_on_page} (total {len(cards)})", file=sys.stderr)
            time.sleep(0.25)

    # 상세 채우고 행단위 즉시 기록
    n = 0
    filled = {k: 0 for k in HEADER}
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for i, key in enumerate(order, 1):
            c = cards[key]
            row = {k: "" for k in HEADER}
            row.update({
                "source": "millet", "brand": "MILLET",
                "style_code": c["style_code"], "name": c["name"],
                "price": c["price"], "currency": "KRW",
                "category": c["category"],
                "gender": gender_from_name(c["name"]),
                "url": c["url"]})
            try:
                d = parse_detail(c["id"])
                row.update(d)
            except Exception as e:
                print(f"[warn] detail {c['id']}: {e}", file=sys.stderr)
            w.writerow(row)
            f.flush()
            n += 1
            for k, v in row.items():
                if str(v).strip():
                    filled[k] += 1
            if i % 10 == 0:
                print(f"[detail] {i}/{len(order)}", file=sys.stderr)
            time.sleep(0.3)

    print("\n=== SUMMARY ===")
    print("rows:", n)
    print("csv:", CSV_PATH)
    print("est_per_cat (pls2):", est_per_cat,
          "sum:", sum(v for v in est_per_cat.values() if v))
    print("filled counts:")
    for k in HEADER:
        print(f"  {k}: {filled[k]}/{n}")


if __name__ == "__main__":
    main()
