#!/usr/bin/env python3
"""
나이키 '브랜드' 라이브 크로스마켓 — 네이버 쇼핑검색 정식 API로 실시간 수집.

기존 nike_crossmarket.py(에어포스 전용, 캐시 파일 기반)를 브랜드 전반으로 일반화:
  1) 수집(브랜드): '나이키 <라인>' 여러 키워드를 라이브로 호출 (셀러 검색 아님)
  2) 카탈로그화 : 같은 스타일코드(DD8959-001 ...)끼리 군집 = 카탈로그 엔트리
  3) 속성 추출  : pig.normalize.extract_attributes 로 모델패밀리/컬러/컨디션/병행수입 추출

키는 환경변수에서만 읽음(소스 하드코딩 없음): NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
    python3 nike_live.py
출력: outputs/nike_live.html, outputs/nike_live.json
"""
import csv
import html
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "outputs")

from naver_pull import search_shop
from pig.normalize import extract_attributes

# 브랜드를 대표하는 나이키 스니커즈 라인 (라이브 검색어)
KEYWORDS = [
    "나이키 에어포스1", "나이키 덩크", "나이키 조던1", "나이키 에어맥스",
    "나이키 코르테즈", "나이키 블레이저", "나이키 페가수스",
]

CODE_RE = re.compile(r"([A-Za-z]{2}\d{4})[-\s]?(\d{3})?")
OPEN_MARKET = {"11st.co.kr": "11번가", "gmarket.co.kr": "G마켓", "auction.co.kr": "옥션",
               "ssg.com": "SSG", "lotteon.com": "롯데ON", "coupang.com": "쿠팡"}
# 상식 가격 범위(원): 일부 리셀 플랫폼이 네이버 가격비교로 1천만원대 비정상 가격을
# 넘기는데(실거래 아님) 이게 가격폭 지표를 망가뜨려 제외. 1만원 미만은 신발 아닌
# 액세서리(키링/양말/insole)일 확률.
PRICE_FLOOR, PRICE_CEIL = 10_000, 3_000_000

# 모델 패밀리 (제목 → 읽기 쉬운 라인명). 코드만으론 라인을 모르므로 제목 토큰으로 라벨.
MODEL_FAMILY = [
    (("에어포스", "airforce", "air force", "af1", "포스1", "포스 1"), "에어포스 1"),
    (("조던", "jordan", "aj1", "aj4", "aj11", "aj13"), "에어 조던"),
    (("덩크", "dunk"), "덩크"),
    (("에어맥스", "airmax", "air max", "에어 맥스"), "에어맥스"),
    (("코르테즈", "cortez"), "코르테즈"),
    (("블레이저", "blazer"), "블레이저"),
    (("페가수스", "pegasus", "페가서스"), "페가수스"),
    (("인빈서블", "invincible"), "인빈서블"),
    (("보메로", "vomero", "v2k"), "보메로/V2K"),
    (("줌플라이", "zoomfly", "줌 플라이"), "줌플라이"),
]
# 컬러웨이 (가장 구체적인 것 먼저)
COLORWAY = [
    (("트리플블랙", "올블랙", "올검", "검검"), "트리플 블랙"),
    (("범고래", "판다", "panda"), "범고래(판다)"),
    (("화이트블랙", "흰검", "화이트 블랙"), "화이트/블랙"),
    (("블랙화이트", "검흰"), "블랙/화이트"),
    (("그레이", "회색", "gray", "grey", "울프그레이"), "그레이"),
    (("네이비", "navy", "감색"), "네이비"),
    (("로얄블루", "블루", "파랑", "blue"), "블루"),
    (("레드", "빨강", "red", "시카고"), "레드"),
    (("그린", "초록", "green"), "그린"),
    (("핑크", "pink"), "핑크"),
    (("브라운", "갈색", "brown"), "브라운"),
    (("베이지", "beige", "크림", "샌드", "cream"), "베이지/크림"),
    (("퍼플", "보라", "purple"), "퍼플"),
    (("옐로우", "노랑", "yellow", "설퍼"), "옐로우"),
    (("화이트", "흰색", "white", "올백", "올화이트"), "화이트"),
    (("블랙", "검정", "검은", "black"), "블랙"),
]


def seller_kind(link):
    host = urlparse(link or "").netloc.lower()
    if "smartstore.naver.com" in host:
        return "셀러", "스마트스토어"
    if "search.shopping.naver.com" in host:
        return "가격비교", "네이버 통합"
    for dom, name in OPEN_MARKET.items():
        if dom in host:
            return "마켓", name
    return "셀러", host.replace("www.", "").replace("m.", "") or "자체몰"


def code_of(title):
    m = CODE_RE.search(title.replace(" ", ""))
    if not m:
        return None
    return m.group(1).upper() + ("-" + m.group(2) if m.group(2) else "")


def _pick(table, blob, default=""):
    return next((lab for toks, lab in table if any(t in blob for t in toks)), default)


def label_of(titles):
    blob = " ".join(titles).replace(" ", "").lower()
    family = _pick(MODEL_FAMILY, blob, "나이키")
    color = _pick(COLORWAY, blob, "컬러 미상")
    return f"{family} · {color}", family, color


def pull_live(cid, csec):
    seen, recs = set(), []
    per_kw = {}
    dropped = 0
    for kw in KEYWORDS:
        try:
            items = search_shop(kw, cid, csec, display=100)
        except Exception as e:
            print(f"  ✗ '{kw}' 수집 실패: {e}", file=sys.stderr)
            per_kw[kw] = 0
            continue
        n0 = len(recs)
        for it in items:
            if not it.get("lprice"):
                continue
            if not (PRICE_FLOOR <= it["lprice"] <= PRICE_CEIL):
                dropped += 1
                continue
            key = (it["title"], it["mallName"], it["lprice"])
            if key in seen:
                continue
            seen.add(key)
            kind, plat = seller_kind(it.get("link", ""))
            attr = extract_attributes({"title": it["title"]})
            _, family, color = label_of([it["title"]])
            recs.append({
                "title": it["title"], "mall": it["mallName"] or "(미상)",
                "price": it["lprice"], "ptype": it.get("productType"),
                "code": code_of(it["title"]), "link": it.get("link", ""),
                "kind": kind, "plat": plat,
                # 네이버 shop.json productType: 1~3=일반(새상품), 4~6=중고, 7~9=단종, 10~12=판매예정.
                # (이전 코드의 '2==중고'는 오류 — 2는 '가격비교 비매칭 새 단독상품'이다.)
                "used": it.get("productType") in (4, 5, 6),
                "graymarket": attr["is_graymarket"],
                "family": family, "colorway": color, "attr": attr,
                # 네이버 API가 직접 준 정형 필드도 보존
                "api_brand": it.get("brand", ""), "api_maker": it.get("maker", ""),
                "api_category": it.get("category", ""),
            })
        per_kw[kw] = len(recs) - n0
        print(f"  · {kw:18} +{per_kw[kw]} (누적 {len(recs)})")
    if dropped:
        print(f"  (가격 이상치 {dropped}건 제외: {PRICE_FLOOR:,}원 미만 또는 {PRICE_CEIL:,}원 초과)")
    return recs, per_kw, dropped


def build(recs):
    by_code = defaultdict(list)
    no_code = []
    for r in recs:
        (by_code[r["code"]].append(r) if r["code"] else no_code.append(r))

    products = []
    for code, items in by_code.items():
        prices = sorted(r["price"] for r in items)
        malls = sorted({r["mall"] for r in items})
        used = sum(1 for r in items if r["used"])
        gray = sum(1 for r in items if r["graymarket"])
        ups = sorted(items, key=lambda r: r["price"])
        name, family, color = label_of([r["title"] for r in items])
        spread = round((prices[-1] - prices[0]) / prices[0] * 100) if prices[0] else 0
        products.append({
            "code": code, "name": name, "family": family, "color": color,
            "n_malls": len(malls), "n_listings": len(items),
            "min": prices[0], "max": prices[-1], "median": int(statistics.median(prices)),
            "spread_pct": spread, "low_mall": ups[0]["mall"],
            "used": used, "new": len(items) - used, "graymarket": gray,
            "members": [{"mall": r["mall"], "price": r["price"], "title": r["title"],
                         "used": r["used"], "gray": r["graymarket"], "link": r["link"],
                         "kind": r["kind"], "plat": r["plat"]} for r in ups],
        })
    products.sort(key=lambda p: (-(p["n_malls"] > 1), -p["n_malls"], -p["spread_pct"]))
    return products, no_code


def render(products, no_code, n_listings, n_mall, per_kw, dropped=0):
    multi = [p for p in products if p["n_malls"] > 1]
    biggest = max((p["spread_pct"] for p in multi), default=0)
    fam_counter = Counter(p["family"] for p in products)
    fam_chips = " ".join(
        f"<span class=fchip>{html.escape(f)} <b>{c}</b></span>"
        for f, c in fam_counter.most_common())

    cards = []
    for p in multi[:30]:
        rows = ""
        for m in p["members"][:8]:
            kc = {"셀러": "k-s", "마켓": "k-m", "가격비교": "k-c"}.get(m["kind"], "k-s")
            seller = html.escape(m["mall"])
            if m.get("link"):
                seller = f"<a href='{html.escape(m['link'])}' target=_blank>{seller}</a>"
            flags = (" <span class=ug>중고</span>" if m["used"] else "") + \
                    (" <span class=gm>병행</span>" if m["gray"] else "")
            rows += (f"<tr class='{'used' if m['used'] else ''}'>"
                     f"<td>{seller}<span class='kk {kc}'>{m['kind']}</span>{flags}"
                     f"<br><span class=plat>{html.escape(m['plat'])}</span></td>"
                     f"<td class=num><b>{m['price']:,}</b>원</td></tr>")
        # 추출 속성 칩
        chips = (f"<span class=ac>패밀리: {html.escape(p['family'])}</span>"
                 f"<span class=ac>컬러: {html.escape(p['color'])}</span>"
                 f"<span class=ac>스타일코드: <code>{p['code']}</code></span>"
                 f"<span class=ac>{p['n_malls']}몰 · {p['n_listings']}리스팅</span>"
                 + (f"<span class='ac warn'>중고 {p['used']}/{p['n_listings']}</span>" if p['used'] else "")
                 + (f"<span class='ac warn'>병행수입 {p['graymarket']}</span>" if p['graymarket'] else ""))
        cards.append(f"""<div class=card><h3>{html.escape(p['name'])}</h3>
          <div class=attrs>{chips}</div>
          <div class=sum>최저 <b>{p['min']:,}원</b>({html.escape(p['low_mall'])}) · 중앙값 {p['median']:,} · 최고 {p['max']:,}
            · <span class=red>가격폭 +{p['spread_pct']}%</span></div>
          <table><tr><th>셀러 / 플랫폼</th><th>표시가</th></tr>{rows}</table></div>""")

    kw_rows = " ".join(f"<span class=fchip>{html.escape(k)} <b>{v}</b></span>" for k, v in per_kw.items())
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>나이키 브랜드 · 라이브 크로스마켓 카탈로그</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--red:#d23b3b;--amber:#b9770b;--amber-bg:#fff6e6;--card:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:1040px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(20,22,26,.96);color:#fff;padding:13px 0;position:sticky;top:0;z-index:5;border-bottom:1px solid #000}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#fff;color:#111;font-weight:900;padding:5px 11px;border-radius:8px;font-size:13px;letter-spacing:1px}}
h1{{font-size:16px;margin:0;font-weight:800}}h3{{font-size:13.5px;margin:0 0 7px}}
.live{{background:#1a9d57;color:#fff;font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.note{{background:var(--amber-bg);border:1px solid #ecd9a8;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:var(--amber)}}
.sect{{font-size:12px;color:var(--mut);font-weight:700;margin:18px 0 8px;text-transform:uppercase;letter-spacing:.4px}}
.fchip{{display:inline-block;background:#fff;border:1px solid var(--line);border-radius:999px;padding:4px 11px;font-size:12px;margin:0 6px 6px 0}}.fchip b{{color:var(--brand)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px}}
.attrs{{margin:0 0 7px}}.ac{{display:inline-block;background:#eef3fc;color:#2452a8;border-radius:6px;padding:2px 8px;font-size:11px;margin:0 5px 5px 0}}.ac.warn{{background:#fdeee9;color:#b54;}}
.sum{{font-size:12px;color:var(--mut);margin-bottom:6px}}.sum b{{color:var(--ink)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.3px;font-weight:700;background:#fafbfe}}tr:last-child td{{border-bottom:none}}td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
tr.used{{background:#fbfbfd}}.ug{{display:inline-block;font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;background:#eef1f7;color:#697586}}
.gm{{display:inline-block;font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;background:#fdeee9;color:#b54}}
td a{{color:var(--brand);text-decoration:none}}td a:hover{{text-decoration:underline}}
.plat{{color:var(--mut);font-size:10px}}
.kk{{display:inline-block;font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;margin-left:4px}}
.k-s{{background:#e6f7ee;color:#1a9d57}}.k-m{{background:#fff6e6;color:#b9770b}}.k-c{{background:#eef1f7;color:#465569}}
code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11px}}.red{{color:var(--red);font-weight:700}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>NIKE</span><h1>나이키 브랜드 · 라이브 크로스마켓 카탈로그</h1>
<span class=live>● LIVE · 네이버 정식 API</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{n_listings}</div><div class=l>수집 리스팅(브랜드 검색)</div></div>
    <div class=tile><div class=b>{len(products)}</div><div class=l>카탈로그 SKU(스타일코드)</div></div>
    <div class=tile><div class=b>{len(multi)}</div><div class=l>다중몰 SKU</div></div>
    <div class=tile><div class=b>+{biggest}%</div><div class=l>최대 동일SKU 가격폭</div></div>
  </div>
  <div class=note>🔑 <b>1) 수집 = 브랜드명 검색</b>(셀러 검색 아님): {kw_rows}<br>
    <b>2) 카탈로그화 = 스타일코드 군집</b>(예 <code>DD8959-001</code>) — 제목 유사도만 쓰면 컬러웨이가 과병합되는데 코드로 같은 SKU만 정확히 묶음.
    <b>3) 속성 추출</b> = 각 카드의 칩(패밀리·컬러·컨디션·병행수입)은 <code>pig.normalize.extract_attributes</code>가 제목에서 뽑은 것.</div>
  <div class=sect>모델 패밀리 분포 (카탈로그 SKU 수)</div>
  <div>{fam_chips}</div>
  <div class=sect>다중몰 카탈로그 (가격폭 큰 순)</div>
  <div class=grid>{''.join(cards)}</div>
  <p class=foot><b>실데이터/정직성.</b> 네이버 쇼핑검색 정식 API 라이브, {len(KEYWORDS)}개 키워드 병합 {n_listings} 리스팅(키워드당 최대 100) → 스타일코드 {len(products)} SKU(다중몰 {len(multi)}).
    스타일코드 없는 제목 {len(no_code)}건은 신뢰성 있게 통합 불가(별도 처리). 중고 판정은 네이버 productType(4~6=중고) 기준 —
    이번 수집은 대부분 가격비교 비매칭 '새 단독 리스팅'(productType 1~3)이라 중고는 소수.
    배송비·오픈마켓 입점셀러명은 API 미제공. 사이즈(mm)는 같은 코드 내 다음 단계.
    {f'리셀 플랫폼이 가격비교로 넘긴 비정상가(1만원 미만/300만원 초과) <b>{dropped}건</b>은 가격폭 왜곡 방지를 위해 제외.' if dropped else ''}</p>
</div></body></html>"""


def write_csvs(recs, products):
    """정형데이터 출력: ① 리스팅 단위(속성 전개) ② 카탈로그 단위."""
    # ① 리스팅 단위 — 비정형 제목 → 정형 속성 한 행
    lp = os.path.join(OUT, "nike_live_listings.csv")
    lcols = ["sku_code", "model_family", "colorway", "category", "brand",
             "model_code", "volume_ml", "size_token", "condition", "is_bundle",
             "is_graymarket", "pack_count", "mall", "seller_kind", "platform",
             "price", "used", "api_brand", "api_category", "raw_title", "link"]
    with open(lp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=lcols)
        w.writeheader()
        for r in recs:
            a = r["attr"]
            w.writerow({
                "sku_code": r["code"] or "", "model_family": r["family"],
                "colorway": r["colorway"], "category": a["category"] or "",
                "brand": a["brand"] or "", "model_code": a["model"] or "",
                "volume_ml": a["volume_ml"] or "", "size_token": a["size_token"] or "",
                "condition": a["condition"] or "", "is_bundle": a["is_bundle"],
                "is_graymarket": a["is_graymarket"], "pack_count": a["pack_count"] or "",
                "mall": r["mall"], "seller_kind": r["kind"], "platform": r["plat"],
                "price": r["price"], "used": r["used"],
                "api_brand": r["api_brand"], "api_category": r["api_category"],
                "raw_title": r["title"], "link": r["link"],
            })
    # ② 카탈로그 단위 — 같은 스타일코드로 묶인 제품 한 행
    cp = os.path.join(OUT, "nike_live_catalog.csv")
    ccols = ["sku_code", "name", "model_family", "colorway", "n_malls", "n_listings",
             "price_min", "price_median", "price_max", "spread_pct",
             "used_count", "new_count", "graymarket_count", "lowest_mall"]
    with open(cp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ccols)
        w.writeheader()
        for p in products:
            w.writerow({
                "sku_code": p["code"], "name": p["name"], "model_family": p["family"],
                "colorway": p["color"], "n_malls": p["n_malls"], "n_listings": p["n_listings"],
                "price_min": p["min"], "price_median": p["median"], "price_max": p["max"],
                "spread_pct": p["spread_pct"], "used_count": p["used"], "new_count": p["new"],
                "graymarket_count": p["graymarket"], "lowest_mall": p["low_mall"],
            })
    return lp, cp


def main():
    cid, csec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        print("✗ NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수 필요", file=sys.stderr)
        sys.exit(1)
    os.makedirs(OUT, exist_ok=True)
    print("나이키 브랜드 라이브 수집…")
    recs, per_kw, dropped = pull_live(cid, csec)
    products, no_code = build(recs)
    n_mall = len({r["mall"] for r in recs})
    multi = [p for p in products if p["n_malls"] > 1]
    with open(os.path.join(OUT, "nike_live.html"), "w", encoding="utf-8") as f:
        f.write(render(products, no_code, len(recs), n_mall, per_kw, dropped))
    with open(os.path.join(OUT, "nike_live.json"), "w", encoding="utf-8") as f:
        json.dump({"n_listings": len(recs), "n_mall": n_mall, "n_sku": len(products),
                   "n_multi_mall": len(multi), "no_code": len(no_code),
                   "keywords": KEYWORDS, "products": products}, f, ensure_ascii=False, indent=2)
    write_csvs(recs, products)
    print("=" * 70)
    print(f"리스팅 {len(recs)} → 카탈로그 SKU {len(products)} (다중몰 {len(multi)}), 코드없음 {len(no_code)}, 몰 {n_mall}")
    for p in multi[:12]:
        print(f"  {p['code']:13} {p['name'][:26]:28} {p['n_malls']}몰 "
              f"{p['min']:>7,}~{p['max']:>7,}원 폭+{p['spread_pct']}% 중고{p['used']}/{p['n_listings']}")
    print("→ outputs/nike_live.html, nike_live.json, nike_live_listings.csv, nike_live_catalog.csv")


if __name__ == "__main__":
    main()
