#!/usr/bin/env python3
"""통합 카탈로그 리포트 HTML — 가격 사다리 + 인사이트를 한 화면에 (product-identity-graph 편입).

Mongo insights 에서 다중몰 SKU(인사이트 적재분)를 읽어 SKU별로:
  · 가격 사다리(price_summary + offers: 몰별 가격/중고/셀러구분)
  · 인사이트(verdict 강점/약점 · aspect 차원별 · FAQ) — gpt-4o-mini 추출, 근거(블로그) 검증분
한 카드에 합쳐 렌더. PIG(가격/식별) × business-model(인사이트)의 결합을 시각화.

  MONGO_URI="mongodb://localhost:47017/?directConnection=true" python3 db/nike_report_html.py
출력: data/nike_report.html
"""
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import load_mongo
from pymongo import MongoClient

OUT = os.path.join(ROOT, "data", "nike_report.html")


def won(n):
    return f"{n:,}원" if n is not None else "—"


def insight_html(tax):
    """taxonomy → 강점/약점 + 차원별 point + (verdict 제외 aspect/context)."""
    verdict = tax.get("verdict") or {}
    def plist(items):
        return "".join(f"<li>{html.escape(it.get('point',''))}</li>" for it in (items or []))
    blocks = []
    if verdict.get("strengths"):
        blocks.append(f"<div class=ins><b class=pos>강점</b><ul>{plist(verdict['strengths'])}</ul></div>")
    if verdict.get("weaknesses"):
        blocks.append(f"<div class=ins><b class=neg>약점</b><ul>{plist(verdict['weaknesses'])}</ul></div>")
    # aspect/context 차원
    asp = []
    for dim, items in load_mongo.walk_points(tax):
        if dim.startswith("verdict"):
            continue
        lab = load_mongo.dim_label(dim)
        asp.append(f"<div class=ins><b>{html.escape(lab)}</b><ul>{plist(items)}</ul></div>")
    blocks += asp
    return "".join(blocks) or "<div class=mut>인사이트 없음</div>"


def faq_html(faqs):
    if not faqs:
        return ""
    rows = "".join(f"<details><summary>{html.escape(f.get('question',''))}</summary>"
                   f"<p>{html.escape(f.get('short_answer') or '')}</p></details>" for f in faqs)
    return f"<div class=faqs><div class=fh>FAQ</div>{rows}</div>"


def main():
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")).insights
    pkg = db.products.find_one({"_id": "NK_AF1_BLACK"})
    skus = list(db.products.find({"parent_uid": "NK_AF1_BLACK", "taxonomy": {"$exists": True}}))
    skus.sort(key=lambda d: -(d.get("price_summary", {}).get("n_malls", 0)))

    cards = []
    for d in skus:
        ps = d.get("price_summary", {})
        offers = list(db.offers.find({"product_uid": d["_id"]}).sort("price", 1).limit(6))
        orows = "".join(
            f"<tr class='{'u' if o.get('used') else ''}'><td>{html.escape(o['mall'])}"
            f"<span class='kk k-{ {'셀러':'s','마켓':'m','가격비교':'c'}.get(o.get('seller_kind'),'s') }'>{o.get('seller_kind','')}</span></td>"
            f"<td class=num><b>{o['price']:,}</b></td><td class=mut>{'중고' if o.get('used') else '신품'}</td></tr>"
            for o in offers)
        name = html.escape(d.get("keyword", d["_id"]))
        tax = d.get("taxonomy") or {}
        cards.append(f"""<div class=card>
          <div class=hd><h3>{name}</h3>
            <span class=code>{html.escape(d.get('style_code',''))}</span></div>
          <div class=cols>
            <div class=price>
              <div class=sum>최저 <b>{won(ps.get('min'))}</b>({html.escape(ps.get('low_mall',''))})
                · 중앙 {won(ps.get('median'))} · 최고 {won(ps.get('max'))}
                · <span class=red>폭 +{ps.get('spread_pct',0)}%</span></div>
              <div class=meta>{ps.get('n_malls',0)}몰 · {ps.get('n_listings',0)} 리스팅 · 중고 {ps.get('used',0)}/{ps.get('n_listings',0)}</div>
              <table><tr><th>몰</th><th>가격</th><th></th></tr>{orows}</table>
            </div>
            <div class=insight>
              <div class=ih>🧠 인사이트 <span class=mut>· 블로그 {d.get('analyzed_count','?')}건 · gpt-4o-mini</span></div>
              {insight_html(tax)}
              {faq_html(d.get('faqs'))}
            </div>
          </div></div>""")

    n_off = db.offers.count_documents({})
    body = f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>나이키 에어포스 블랙 · 가격×인사이트 통합 카탈로그</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--red:#d23b3b;--pos:#1a9d57;--neg:#c0392b;--card:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(20,22,26,.96);color:#fff;padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#fff;color:#111;font-weight:900;padding:5px 11px;border-radius:8px;font-size:12px;letter-spacing:1px}}
h1{{font-size:16px;margin:0;font-weight:800}}h3{{font-size:14px;margin:0;font-weight:800}}
.live{{background:#1a9d57;color:#fff;font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.note{{background:#eef4ff;border:1px solid #cfe0ff;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:var(--brand)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:14px 16px;margin:13px 0}}
.hd{{display:flex;align-items:center;gap:9px;margin-bottom:10px}}.code{{background:#eef1f6;padding:2px 7px;border-radius:5px;font-size:11px;color:#465569}}
.cols{{display:grid;grid-template-columns:1fr 1.15fr;gap:18px}}@media(max-width:760px){{.cols{{grid-template-columns:1fr}}}}
.sum{{font-size:12.5px;color:var(--mut);margin-bottom:3px}}.sum b{{color:var(--ink)}}.meta{{font-size:11px;color:var(--mut);margin-bottom:7px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:4px 7px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;font-weight:700;background:#fafbfe}}tr:last-child td{{border-bottom:none}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}
tr.u{{background:#fbfbfd}}.kk{{display:inline-block;font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;margin-left:4px}}
.k-s{{background:#e6f7ee;color:#1a9d57}}.k-m{{background:#fff6e6;color:#b9770b}}.k-c{{background:#eef1f7;color:#465569}}
.insight{{border-left:1px solid var(--line);padding-left:16px}}@media(max-width:760px){{.insight{{border-left:none;padding-left:0;border-top:1px solid var(--line);padding-top:10px}}}}
.ih{{font-size:12px;font-weight:800;margin-bottom:7px}}.ins{{margin-bottom:7px}}.ins b{{font-size:11.5px}}.ins b.pos{{color:var(--pos)}}.ins b.neg{{color:var(--neg)}}
.ins ul{{margin:2px 0 0;padding-left:17px}}.ins li{{font-size:12px;margin:1px 0}}
.faqs{{margin-top:9px;border-top:1px dashed var(--line);padding-top:7px}}.fh{{font-size:11px;font-weight:800;color:var(--mut);margin-bottom:3px}}
details{{font-size:12px;margin:2px 0}}summary{{cursor:pointer;font-weight:600}}details p{{margin:3px 0 6px;color:#444}}
.red{{color:var(--red);font-weight:700}}.mut{{color:var(--mut);font-weight:500}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>AF1</span><h1>나이키 에어포스 블랙 · 가격 × 인사이트 통합 카탈로그</h1>
<span class=live>MongoDB · REAL</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{len(skus)}</div><div class=l>인사이트 적재 SKU</div></div>
    <div class=tile><div class=b>{n_off}</div><div class=l>가격 offer(전체)</div></div>
    <div class=tile><div class=b>{sum(len(list(load_mongo.walk_points(d.get('taxonomy') or {}))) for d in skus)}</div><div class=l>인사이트 차원</div></div>
    <div class=tile><div class=b>≈₩55</div><div class=l>추출 비용(10 SKU)</div></div>
  </div>
  <div class=note>🔗 <b>두 엔진의 결합</b>: 왼쪽 = product-identity-graph(스타일코드로 cross-mall 해소한 <b>가격 사다리</b>),
    오른쪽 = business-model(네이버 블로그 → gpt-4o-mini <b>인사이트</b>). 같은 카탈로그 노드(MongoDB 한 문서)에 둘이 함께 들어갑니다.</div>
  {''.join(cards)}
  <p class=foot><b>실데이터/정직성.</b> 가격=네이버 쇼핑검색 정식 API(대부분 중고/리셀), 스타일코드로 SKU 해소.
    인사이트=네이버 블로그 50건/SKU → 광고필터 → gpt-4o-mini, 근거검증 통과분만. 배송비는 검색 API 미제공.
    인사이트 키워드는 모델/컬러 단위라 같은 컬러웨이는 리뷰가 겹칠 수 있음(SKU 노드는 분리 유지).</p>
</div></body></html>"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"생성: {OUT}  (SKU {len(skus)} · offer {n_off})")


if __name__ == "__main__":
    main()
