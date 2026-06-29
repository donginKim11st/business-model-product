#!/usr/bin/env python3
"""
스타스포츠 공식몰(starsportsmall.co.kr, classic ASP 자체몰) 서버측 상품 표본 추출.
stdlib only (urllib). 인코딩 euc-kr.

플랫폼: 자체 ASP. JSON-LD 없음 → 후크: DOM(상세 specTable).
  · 카테고리(리스트): /goods/submain.asp?cate={ID}&listsize=100[&page=N]
        - 상품링크: /goods/content.asp?guid={GUID}
        - 총수량 텍스트: "총 N개의 제품이 있습니다"
  · 상세: /goods/content.asp?guid={GUID}
        - 고시/스펙: <table class="specTable"><tr><th>라벨</th><td>값</td></tr>...
          (구판) 품명/모델명·크기·색상·재질·동일모델의 출시년월·제조국
          (신판) 제품소재·색상·"사이즈, 중량"·제조국  (품명/모델명 행 없음)
        - 신판 상품명: <div class="tit-area"><h3 class="tit">상품명 ...
        - 모델코드 폴백: 간편주문표 "MODEL  JS5970"
        - 가격: tbl-type3 "판매가격 N 원"

필드 매핑(사이트 실측 우선; 힌트와 상이한 점은 notes 기록):
  · style_code = 모델명(또는 품명및모델명 우측, 또는 MODEL)
      ※ 힌트의 '상품코드 FOX301M'은 렌더링 행이 아니라 tbl-type3 summary 속성
        보일러플레이트 — 실제 모델코드는 모델명/MODEL 값.
  · sizes      = 크기/사이즈(중량 결합셀 포함) 행 원문(사이즈 select 없음, 임의 분해 X)
  · color      = 색상 행(옵션 혼재 아님 — 전용 행 존재)
  · material   = 재질/제품소재,  origin = 제조국,  mfg_date = 동일모델의 출시년월
고시(소재/제조국/제조년월) 대다수 텍스트 → gosi_status=text
출력: outputs/extract_brand_starsports.csv (utf-8-sig)
"""
import csv
import html as ihtml
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
OUT_CSV = os.path.join(OUT, "extract_brand_starsports.csv")
BASE = "https://starsportsmall.co.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

HEADER = ["source", "brand", "style_code", "name", "color", "price",
          "currency", "category", "gender", "sizes", "origin",
          "material", "mfg_date", "url"]

TARGET = 120
PER_LEAF_CAP = 10        # 카테고리당 상한(한 카테고리 독식 방지)
MAX_PAGES = 5
LISTSIZE = 100

PARENT_NAMES = {
    "축구", "풋살", "족구", "배구", "농구", "야구", "핸드볼/럭비", "핸드볼",
    "럭비", "라켓스포츠", "뉴스포츠", "육상/런닝", "수영", "피트니스",
    "학교체육", "테니스", "배드민턴", "탁구",
}

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def fetch(url, timeout=30, retries=2):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return r.status, r.read().decode("euc-kr", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def clean(s):
    s = s.replace("\xa0", " ")
    return ihtml.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()


def cate_map():
    st, h = fetch(f"{BASE}/main/")
    pairs = re.findall(r'<a[^>]*submain\.asp\?cate=(\d+)[^>]*>(.*?)</a>', h, re.S)
    m = OrderedDict()
    for cid, txt in pairs:
        t = clean(txt)
        if not t:
            ma = re.search(r'alt="([^"]+)"', txt)
            t = ma.group(1).strip() if ma else ""
        if cid not in m or (not m[cid] and t):
            m[cid] = t
    return m


def list_page(cid, page=1):
    st, h = fetch(f"{BASE}/goods/submain.asp?cate={cid}"
                  f"&listsize={LISTSIZE}&page={page}")
    guids = []
    for g in re.findall(r'content\.asp\?guid=(\d+)', h):
        if g not in guids:
            guids.append(g)
    mc = re.search(r'총\s*([\d,]+)\s*개의 제품', h)
    total = int(mc.group(1).replace(",", "")) if mc else None
    return guids, total


def parse_spec_table(h):
    out = OrderedDict()
    m = re.search(r'<table[^>]*class="[^"]*specTable[^"]*"[^>]*>(.*?)</table>',
                  h, re.S)
    if not m:
        return out
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.S):
        th = re.search(r'<th[^>]*>(.*?)</th>', tr, re.S)
        td = re.search(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if th and td:
            k = clean(th.group(1))
            v = clean(td.group(1))
            if k:
                out[k] = v
    return out


def _norm(s):
    return re.sub(r"[\s,/]", "", s)


def pick(spec, keys):
    """exact(정규화) → contains 순으로 라벨 매칭."""
    norm = {_norm(k): v for k, v in spec.items()}
    for want in keys:
        w = _norm(want)
        if norm.get(w):
            return norm[w]
    for want in keys:
        w = _norm(want)
        for k, v in norm.items():
            if w and w in k and v:
                return v
    return ""


def display_name(h):
    """신판 상품명: <h3 class="tit"> 텍스트(자식 span 이전)."""
    m = re.search(r'<h3[^>]*class="[^"]*\btit\b[^"]*"[^>]*>(.*?)</h3>', h, re.S)
    if m:
        inner = re.split(r'<span|<a\b|<br', m.group(1))[0]
        t = clean(inner)
        if t and len(t) > 1:
            return t
    for pat in (r'class="[^"]*goodsNm[^"]*"[^>]*>(.*?)<',
                r'class="[^"]*titName[^"]*"[^>]*>(.*?)<'):
        mm = re.search(pat, h, re.S)
        if mm:
            t = clean(mm.group(1))
            if t and len(t) > 1:
                return t
    return ""


def norm_style(s):
    if not s:
        return ""
    # 다색상 모델은 "BASE-XX(색), ..." 식 나열 → 첫 코드 토큰만
    s = re.split(r"[,(]", s)[0]
    s = re.sub(r"\s+", "", s).upper().strip("-/")
    return s


def parse_price(h):
    idx = h.find("판매가격")
    if idx >= 0:
        seg = clean(h[idx:idx + 160])
        m = re.search(r"판매가격\s*([\d,]{2,})", seg)  # '원'은 긴 span 뒤라 미요구
        if m:
            return m.group(1).replace(",", "")
    return ""


def gender_of(name):
    if re.search(r"키즈|주니어|유소년|유아|아동|kids|junior", name, re.I):
        return "아동"
    if re.search(r"여성|우먼|women", name, re.I):
        return "여성"
    if re.search(r"남성|맨즈|men\b", name, re.I):
        return "남성"
    return ""


def parse_detail(guid, label):
    url = f"{BASE}/goods/content.asp?guid={guid}"
    st, h = fetch(url)
    rec = {k: "" for k in HEADER}
    rec["source"] = "starsports"
    rec["brand"] = "스타스포츠"
    rec["category"] = label
    rec["url"] = url

    spec = parse_spec_table(h)
    norm = {_norm(k): v for k, v in spec.items()}

    # --- name / style_code ---
    name, style = "", ""
    combo = norm.get("품명및모델명")
    if combo:
        # 결합행: 우측 마지막 '/' 이후가 모델코드(없으면 코드 없음)
        if "/" in combo:
            name, _, style = (x.strip() for x in combo.rpartition("/"))
        else:
            name = combo
    else:
        name = pick(spec, ["품명", "상품명"])
        # 모델코드는 exact-only (contains는 '동일모델의출시년월' 등 오매칭)
        style = norm.get("모델명") or norm.get("모델") or norm.get("품번") or ""
    if not style:
        mm = re.search(r"MODEL\s*</[^>]+>\s*(?:<[^>]+>\s*)*([A-Za-z0-9\-]+)", h)
        if mm:
            style = mm.group(1).strip()
    if not name:
        name = display_name(h)

    rec["name"] = name
    rec["style_code"] = norm_style(style)
    rec["color"] = pick(spec, ["색상", "컬러", "색깔"])
    rec["material"] = pick(spec, ["재질", "제품소재", "소재", "원단"])
    rec["origin"] = pick(spec, ["제조국", "원산지"])
    rec["mfg_date"] = pick(spec, ["동일모델의 출시년월", "출시년월",
                                  "제조년월", "제조연월"])
    rec["sizes"] = pick(spec, ["크기", "사이즈", "치수"])  # 결합셀 원문 유지
    rec["price"] = parse_price(h)
    rec["currency"] = "KRW" if rec["price"] else ""
    rec["gender"] = gender_of(name)
    rec["_gosi"] = bool(rec["material"] and rec["origin"])
    return rec


def build_leaves(cmap):
    """nav 순서로 스포츠별 leaf 그룹핑 → 라운드로빈 인터리브."""
    sport_leaves = OrderedDict()
    cur = "기타"
    for cid, name in cmap.items():
        if not name or name == "스타":
            continue
        if name in PARENT_NAMES:
            cur = name
            sport_leaves.setdefault(cur, [])
            continue
        sport_leaves.setdefault(cur, []).append((cid, name))
    flat = []
    if sport_leaves:
        mx = max(len(v) for v in sport_leaves.values())
        for i in range(mx):
            for leaves in sport_leaves.values():
                if i < len(leaves):
                    flat.append(leaves[i])
    return flat


def main():
    os.makedirs(OUT, exist_ok=True)
    cmap = cate_map()

    # est_total: parent 스포츠 그룹 totalCount 합
    parents = OrderedDict()
    for cid, name in cmap.items():
        if name in PARENT_NAMES:
            parents.setdefault(name, cid)
    est_total, est_parts = 0, {}
    for name, cid in parents.items():
        try:
            _, tot = list_page(cid, 1)
            if tot:
                est_total += tot
                est_parts[name] = tot
        except Exception as e:  # noqa: BLE001
            print(f"[est] {name}({cid}) 실패: {e}", file=sys.stderr)
        time.sleep(0.15)
    print(f"[est_total] {est_total} parts={est_parts}", file=sys.stderr)

    leaves = build_leaves(cmap)
    collected, seen, leaf_used = [], set(), OrderedDict()
    for cid, name in leaves:
        if len(collected) >= TARGET:
            break
        got = 0
        for page in range(1, MAX_PAGES + 1):
            try:
                guids, _ = list_page(cid, page)
            except Exception as e:  # noqa: BLE001
                print(f"[list] {name}({cid}) p{page} 실패: {e}", file=sys.stderr)
                break
            new = [g for g in guids if g not in seen]
            for g in new:
                if got >= PER_LEAF_CAP or len(collected) >= TARGET:
                    break
                seen.add(g)
                collected.append((g, name))
                got += 1
            if got >= PER_LEAF_CAP or len(collected) >= TARGET:
                break
            if len(guids) < LISTSIZE:
                break
            time.sleep(0.2)
        if got:
            leaf_used[name] = leaf_used.get(name, 0) + got
        time.sleep(0.2)

    print(f"[collect] {len(collected)}개 | 카테고리 {len(leaf_used)}종",
          file=sys.stderr)

    rows, fail = [], 0
    for n, (guid, label) in enumerate(collected, 1):
        try:
            rows.append(parse_detail(guid, label))
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[detail] {guid} 실패: {e}", file=sys.stderr)
        if n % 20 == 0:
            print(f"[detail] {n}/{len(collected)} ...", file=sys.stderr)
        time.sleep(0.18)

    gosi_n = sum(1 for r in rows if r.get("_gosi"))
    for r in rows:
        r.pop("_gosi", None)

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    filled = {k: sum(1 for r in rows if str(r.get(k, "")).strip())
              for k in HEADER}
    print(f"\n=== 완료 === 행수 {len(rows)} (실패 {fail}) "
          f"| est_total={est_total} | gosi텍스트(소재&제조국) {gosi_n}/{len(rows)}")
    print("채워진 컬럼:", {k: v for k, v in filled.items() if v})
    print("카테고리 분포:", dict(leaf_used))
    print("경로:", OUT_CSV)
    for r in rows[:6]:
        print("SAMPLE:", {k: r[k] for k in
              ("style_code", "name", "color", "price", "category",
               "sizes", "origin", "material", "mfg_date")})


if __name__ == "__main__":
    main()
