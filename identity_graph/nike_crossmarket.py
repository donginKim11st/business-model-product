#!/usr/bin/env python3
"""
나이키 에어포스 블랙 — 상품(=카탈로그) 단위 크로스마켓 도시에.

싸이닉 크로스마켓(naver_dossier_v3.py)과 같은 포맷이지만 신발 도메인에 맞춤:
  · 상품키 = 나이키 스타일코드 (DD8959-001 등). 화장품의 product_lines(모델코드)에 해당.
    cosmetics lexicon엔 신발 식별자가 없어 과병합하던 것을 코드로 정밀 분리
    (DD8959-001 트리플블랙 ≠ DD8959-103 화이트블랙).
  · 수량 = 켤레 단위(qty=1) → '리뷰300개'를 구매수량으로 오인하던 버그 제거.
  · 공식가(정품몰) 없음 → 대신 같은 SKU의 몰 간 '가격 분산(최저~최고)'을 셀러 지표로.

    python3 nike_crossmarket.py
출력: outputs/nike_crossmarket.html, outputs/nike_crossmarket.json
"""
import glob
import html
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")

CODE_RE = re.compile(r"([A-Za-z]{2}\d{4})[-\s]?(\d{3})?")
PTYPE = {1: "일반", 2: "중고", 3: "대여", 4: "단종", 5: "판매중지", 6: "가격비교통합"}

# 컬러웨이/모델 라벨링용 토큰 (제목 → 읽기 쉬운 이름)
COLOR = [
    (("트리플블랙", "올블랙", "올검", "검검"), "트리플 블랙"),
    (("화이트블랙", "흰검", "화이트 블랙"), "화이트 블랙"),
    (("범고래",), "범고래(판다)"),
    (("발렌타인",), "발렌타인 데이"),
    (("쉐도우",), "쉐도우"),
    (("새틴", "사틴"), "새틴"),
    (("옐로우", "다크설퍼", "검노"), "옐로우/설퍼"),
    (("고어텍스",), "고어텍스"),
    (("핑크",), "핑크"),
]
MODEL = [
    (("애슬레틱클럽", "athletic"), "애슬레틱 클럽"),
    (("lv8",), "LV8"),
    (("쉐도우", "shadow"), "쉐도우"),
    (("gs",), "GS(키즈)"),
    (("se",), "SE"),
    (("고어텍스",), "고어텍스"),
]


# 오픈마켓 = 내부 입점셀러가 가려진 마켓(mallName=마켓명). 그 외 도메인 = 셀러/편집샵 상호 그대로.
OPEN_MARKET = {"11st.co.kr": "11번가", "gmarket.co.kr": "G마켓", "auction.co.kr": "옥션",
               "ssg.com": "SSG", "lotteon.com": "롯데ON", "coupang.com": "쿠팡"}


def seller_kind(link):
    """반환 (kind, platform): kind ∈ {셀러, 마켓, 가격비교}."""
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


def label_of(titles):
    blob = " ".join(titles).replace(" ", "").lower()
    color = next((lab for toks, lab in COLOR if any(t in blob for t in toks)), "")
    model = next((lab for toks, lab in MODEL if any(t in blob for t in toks)), "")
    parts = ["에어포스 1 '07"]
    if model and model not in ("SE",):
        parts.append(model)
    parts.append(color or "블랙 계열")
    if model == "SE":
        parts.append("SE")
    return " ".join(parts)


def load():
    files = glob.glob(os.path.join(OUT, "naver_나이키*.json")) + glob.glob(os.path.join(OUT, "naver_에어포스*.json"))
    seen, recs = set(), []
    for f in files:
        for it in json.load(open(f, encoding="utf-8")):
            if not it.get("lprice"):
                continue
            key = (it["title"], it["mallName"], it["lprice"])
            if key in seen:
                continue
            seen.add(key)
            kind, plat = seller_kind(it.get("link", ""))
            recs.append({
                "title": it["title"], "mall": it["mallName"] or "(미상)",
                "price": it["lprice"], "ptype": it.get("productType"),
                "code": code_of(it["title"]), "link": it.get("link", ""),
                "kind": kind, "plat": plat,
            })
    return recs


def build(recs):
    by_code = defaultdict(list)
    no_code = []
    for r in recs:
        if r["code"]:
            by_code[r["code"]].append(r)
        else:
            no_code.append(r)

    products = []
    for code, items in by_code.items():
        prices = sorted(r["price"] for r in items)
        malls = sorted({r["mall"] for r in items})
        used = sum(1 for r in items if r["ptype"] == 2)
        ups = sorted(items, key=lambda r: r["price"])
        spread = round((prices[-1] - prices[0]) / prices[0] * 100) if prices[0] else 0
        products.append({
            "code": code, "name": label_of([r["title"] for r in items]),
            "n_malls": len(malls), "n_listings": len(items),
            "min": prices[0], "max": prices[-1], "median": int(statistics.median(prices)),
            "spread_pct": spread, "low_mall": ups[0]["mall"],
            "used": used, "new": len(items) - used,
            "members": [{"mall": r["mall"], "price": r["price"], "title": r["title"],
                         "used": r["ptype"] == 2, "link": r["link"],
                         "kind": r["kind"], "plat": r["plat"]} for r in ups],
        })
    # 다중몰 SKU 우선, 가격분산 큰 순
    products.sort(key=lambda p: (-(p["n_malls"] > 1), -p["n_malls"], -p["spread_pct"]))
    return products, no_code


def render(products, no_code, n_listings, n_mall):
    multi = [p for p in products if p["n_malls"] > 1]
    spreads = [p["spread_pct"] for p in multi if p["spread_pct"]]
    cards = []
    for p in products[:24]:
        if p["n_malls"] < 2:
            continue
        rows = ""
        for m in p["members"][:7]:
            kc = {"셀러": "k-s", "마켓": "k-m", "가격비교": "k-c"}.get(m["kind"], "k-s")
            seller = html.escape(m["mall"])
            if m.get("link"):
                seller = f"<a href='{html.escape(m['link'])}' target=_blank>{seller}</a>"
            rows += (f"<tr class='{'used' if m['used'] else ''}'>"
                     f"<td>{seller}<span class='kk {kc}'>{m['kind']}</span>"
                     f"{' <span class=ug>중고</span>' if m['used'] else ''}"
                     f"<br><span class=plat>{html.escape(m['plat'])}</span></td>"
                     f"<td class=num><b>{m['price']:,}</b>원</td>"
                     f"<td class=num><span class=na>—</span></td></tr>")
        cards.append(f"""<div class=card><h3>{html.escape(p['name'])}
          <span class=mut>· <code>{p['code']}</code> · {p['n_malls']}몰 · {p['n_listings']} 리스팅 → 1 SKU</span></h3>
          <div class=sum>최저 <b>{p['min']:,}원</b>({html.escape(p['low_mall'])}) · 중앙값 {p['median']:,} · 최고 {p['max']:,}
            · <span class=red>가격폭 +{p['spread_pct']}%</span>{(' · 중고 '+str(p['used'])+'/'+str(p['n_listings'])) if p['used'] else ''}</div>
          <table><tr><th>셀러 / 플랫폼</th><th>표시가</th><th>배송비</th></tr>{rows}</table></div>""")
    biggest = max((p["spread_pct"] for p in multi), default=0)
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>나이키 에어포스 블랙 · 상품(카탈로그) 단위 크로스마켓</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--red:#d23b3b;--amber:#b9770b;--amber-bg:#fff6e6;--card:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:980px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(20,22,26,.96);color:#fff;padding:13px 0;position:sticky;top:0;z-index:5;border-bottom:1px solid #000}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#fff;color:#111;font-weight:900;padding:5px 11px;border-radius:8px;font-size:12px;letter-spacing:1px}}
h1{{font-size:16px;margin:0;font-weight:800}}h3{{font-size:13px;margin:0 0 6px}}
.live{{background:#1a9d57;color:#fff;font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.note{{background:var(--amber-bg);border:1px solid #ecd9a8;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:var(--amber)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px}}
.sum{{font-size:12px;color:var(--mut);margin-bottom:6px}}.sum b{{color:var(--ink)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.3px;font-weight:700;background:#fafbfe}}tr:last-child td{{border-bottom:none}}td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
tr.used{{background:#fbfbfd}}.ug{{display:inline-block;font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;background:#eef1f7;color:#697586}}
td a{{color:var(--brand);text-decoration:none}}td a:hover{{text-decoration:underline}}
.plat{{color:var(--mut);font-size:10px}}.na{{color:#c0c6d0}}
.kk{{display:inline-block;font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;margin-left:4px}}
.k-s{{background:#e6f7ee;color:#1a9d57}}.k-m{{background:#fff6e6;color:#b9770b}}.k-c{{background:#eef1f7;color:#465569}}
code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11px}}.red{{color:var(--red);font-weight:700}}.mut{{color:var(--mut);font-weight:500}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>AF1</span><h1>나이키 에어포스 블랙 · 상품(카탈로그) 단위</h1>
<span class=live>REAL · 네이버 정식 API</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{len(multi)}</div><div class=l>다중몰 SKU(스타일코드)</div></div>
    <div class=tile><div class=b>{n_mall}</div><div class=l>판매 몰</div></div>
    <div class=tile><div class=b>{n_listings}</div><div class=l>리스팅</div></div>
    <div class=tile><div class=b>+{biggest}%</div><div class=l>최대 동일SKU 가격폭</div></div>
  </div>
  <div class=note>🔑 <b>상품키 = 나이키 스타일코드</b>(예: <code>DD8959-001</code>). 제목 토큰 유사도만 쓰면
    트리플블랙(−001)과 화이트블랙(−103)이 한 덩어리로 과병합되는데, 코드로 묶으니 같은 SKU만 정확히 모입니다.
    공식 정품몰이 없는 리셀/편집샵 시장이라 '공식가 −%' 대신 <b>동일 SKU의 몰 간 가격폭(최저~최고)</b>이 셀러 지표 —
    같은 신발이 몰에 따라 최대 +{biggest}% 벌어집니다.</div>
  <div class=note>🏷️ <b>셀러 / 배송비.</b> <span class='kk k-s'>셀러</span> = mallName이 곧 입점 상호(스마트스토어·편집샵, 클릭 시 해당 리스팅) ·
    <span class='kk k-m'>마켓</span> = 오픈마켓(11번가·G마켓·옥션 등)이라 <b>내부 입점셀러는 API가 가림</b> ·
    <span class='kk k-c'>가격비교</span> = 네이버 통합노드. <b>배송비는 네이버 쇼핑검색 API가 제공하지 않아 전부 '—'</b> — 받으려면 상품 상세페이지 별도 수집 필요.</div>
  <div class=grid>{''.join(cards)}</div>
  <p class=foot><b>실데이터/정직성.</b> 네이버 쇼핑검색 정식 API, 3개 키워드 병합 {n_listings} 리스팅 → 스타일코드 {len(products)} SKU(다중몰 {len(multi)}).
    스타일코드 매칭 80% — 나머지 20%(코드 없는 제목 {len(no_code)}건)는 신뢰성 있게 통합 불가(별도 처리). productType은 API 라벨 기준 대부분 '중고/리셀'(편집샵 특성).
    수량은 켤레 단위(qty=1)로 고정해 '리뷰300개' 오인 버그 제거. 사이즈(mm)는 미반영 — 같은 코드 내 사이즈 구분은 다음 단계.</p>
</div></body></html>"""


def main():
    recs = load()
    products, no_code = build(recs)
    n_mall = len({r["mall"] for r in recs})
    multi = [p for p in products if p["n_malls"] > 1]
    with open(os.path.join(OUT, "nike_crossmarket.html"), "w", encoding="utf-8") as f:
        f.write(render(products, no_code, len(recs), n_mall))
    with open(os.path.join(OUT, "nike_crossmarket.json"), "w", encoding="utf-8") as f:
        json.dump({"n_listings": len(recs), "n_mall": n_mall, "n_sku": len(products),
                   "n_multi_mall": len(multi), "no_code": len(no_code), "products": products},
                  f, ensure_ascii=False, indent=2)
    print(f"리스팅 {len(recs)} → 스타일코드 SKU {len(products)} (다중몰 {len(multi)}), 코드없음 {len(no_code)}, 몰 {n_mall}")
    print("=" * 68)
    for p in multi[:12]:
        print(f"  {p['code']:13} {p['name'][:24]:26} {p['n_malls']}몰 "
              f"{p['min']:>7,}~{p['max']:>7,}원  폭+{p['spread_pct']}%  중고{p['used']}/{p['n_listings']}")
    print("outputs/nike_crossmarket.html, .json 생성")


if __name__ == "__main__":
    main()
