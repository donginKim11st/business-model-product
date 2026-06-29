#!/usr/bin/env python3
"""
크로스마켓 도시에 v3 — ER 엔진 + OpenAI LLM 분할 반영.
v2(룰 클러스터) 위에 outputs/llm_clusters.json(LLM이 리페어/톤업 등 sub-variant를
분리한 최종 클러스터)을 입혀 렌더. LLM 재호출 없이 캐시에서 생성.

    python3 llm_split.py 32        # 먼저 LLM 분할 → outputs/llm_clusters.json
    python3 naver_dossier_v3.py
"""
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
sys.path.insert(0, HERE)

from pig.normalize import extract_attributes
from naver_resolve import load_records
from naver_dossier_v2 import disp_name


def build_products(clusters, by_id):
    products = []
    for cl in clusters:
        items = [by_id[i] for i in cl if i in by_id]
        if len(items) < 2:
            continue
        a = extract_attributes(items[0])
        off = [r for r in items if "공식" in r["mall"]]
        official_unit = min((r["unit_price"] for r in off), default=None)
        ups = sorted(items, key=lambda r: r["unit_price"])
        lowest = ups[0]
        malls = sorted({r["mall"] for r in items})
        undercut = (round((official_unit - lowest["unit_price"]) / official_unit * 100)
                    if official_unit and lowest["unit_price"] < official_unit else 0)
        products.append({
            "name": disp_name([r["raw_title"] for r in items]),
            "category": a["category"], "size": a["size_token"],
            "n_malls": len(malls), "n_listings": len(items),
            "official_unit": official_unit, "lowest_unit": lowest["unit_price"],
            "lowest_mall": lowest["mall"], "max_unit": ups[-1]["unit_price"],
            "undercut_pct": undercut, "has_coupang": any("쿠팡" in m for m in malls),
            "members": [{"mall": r["mall"], "unit": r["unit_price"], "price": r["price"],
                         "qty": r["qty"], "title": r["raw_title"], "official": "공식" in r["mall"]}
                        for r in ups],
        })
    products.sort(key=lambda p: (-p["undercut_pct"], -p["n_malls"]))
    return products


def render(products, n_mall, n_listings, model):
    undercut = [p for p in products if p["undercut_pct"] > 0]
    coup = [p for p in products if p["has_coupang"]]
    deepest = max((p["undercut_pct"] for p in products), default=0)
    cards = []
    for p in products[:20]:
        rows = "".join(
            f"<tr class='{'off' if m['official'] else ''}'><td>{html.escape(m['mall'])}"
            f"{' <span class=pg>공식</span>' if m['official'] else ''}</td>"
            f"<td class=num>{m['price']:,}원</td><td class=num>×{m['qty']}</td>"
            f"<td class=num><b>{m['unit']:,}</b></td></tr>" for m in p["members"][:6])
        ut = (f"<span class=red>공식가 −{p['undercut_pct']}%</span>"
              if p["undercut_pct"] else "<span class=mut>공식가 미확인/최저</span>")
        cards.append(f"""<div class=card><h3>{html.escape(p['name'])}
          <span class=mut>· {p['category'] or ''} {p['size'] or ''} · {p['n_malls']}몰 · {p['n_listings']} 리스팅 → 1 SKU</span></h3>
          <div class=sum>개당 최저 <b>{p['lowest_unit']:,}원</b>({html.escape(p['lowest_mall'])})
            {('· 공식 '+format(p['official_unit'],',')+'원' if p['official_unit'] else '')} · {ut}</div>
          <table><tr><th>몰</th><th>표시가</th><th>수량</th><th>개당</th></tr>{rows}</table></div>""")
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SCINIC 크로스마켓 도시에 v3 (ER+LLM)</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--red:#d23b3b;--green:#1a9d57;--green-bg:#e6f7ee;--violet:#6b4fc0;--violet-bg:#f0ecfb;--card:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:960px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(244,246,250,.93);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#10a37f;color:#fff;font-weight:900;padding:5px 10px;border-radius:8px;font-size:12px}}
h1{{font-size:16px;margin:0;font-weight:800}}h3{{font-size:13px;margin:0 0 6px}}
.live{{background:var(--violet-bg);color:var(--violet);font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.note{{background:var(--violet-bg);border:1px solid #d6c9f2;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:var(--violet)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px}}
.sum{{font-size:12px;color:var(--mut);margin-bottom:6px}}.sum b{{color:var(--ink)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.3px;font-weight:700;background:#fafbfe}}tr:last-child td{{border-bottom:none}}td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
tr.off{{background:#f0fff6}}.pg{{display:inline-block;font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;background:var(--green-bg);color:var(--green)}}
.red{{color:var(--red);font-weight:700}}.mut{{color:var(--mut);font-weight:500}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>ER+LLM</span><h1>SCINIC 크로스마켓 도시에 v3</h1>
<span class=live>REAL · 엔진+LLM 분할</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{len(products)}</div><div class=l>통합 SKU(다중몰)</div></div>
    <div class=tile><div class=b>{n_mall}</div><div class=l>판매 몰</div></div>
    <div class=tile><div class=b>{len(undercut)}</div><div class=l>공식가 아래 판매</div></div>
    <div class=tile><div class=b>{len(coup)}</div><div class=l>쿠팡 포함 SKU</div></div>
  </div>
  <div class=note>🧠 <b>3단계 cascade 전체 적용</b>: 값싼 블로킹 → 룰 충돌(가드+라인+사이즈) → <b>OpenAI {html.escape(model)}</b>가 잔여 sub-variant 분리.
    v2 대비 <b>UV엑스퍼트 리페어 vs 톤업, 에멀전 vs 스킨, 마일드 vs 슈퍼마일드</b>가 별도 SKU로 갈라졌습니다. 개당가 기준 공식가 vs 최저, 최대 −{deepest}%.</div>
  <div class=grid>{''.join(cards)}</div>
  <p class=foot><b>실데이터/정직성.</b> 네이버 정식 API({n_listings} 리스팅) → 룰 클러스터 → LLM 분할({len(products)} 다중몰 SKU).
    LLM은 정밀도 쪽으로 기울어(과분할 경향) 정확한 SKU 입도는 <b>제품 정체성 정책</b> 결정에 달림(프롬프트가 노브). 수량은 개당가 정규화.
    공식가=mallName '공식' 스토어. 쿠팡은 네이버 가격비교 경유 일부. 옥션·쿠팡 직접 전수는 별도 게이트.</p>
</div></body></html>"""


def main():
    path = os.path.join(OUT, "llm_clusters.json")
    if not os.path.exists(path):
        print("✗ outputs/llm_clusters.json 없음. 먼저 실행: python3 llm_split.py 32")
        sys.exit(1)
    data = json.load(open(path, encoding="utf-8"))
    clusters, model = data["clusters"], data.get("model", "openai")
    recs = load_records()
    by_id = {r["id"]: r for r in recs}
    n_mall = len({r["mall"] for r in recs})
    products = build_products(clusters, by_id)

    with open(os.path.join(OUT, "naver_crossmarket_v3.html"), "w", encoding="utf-8") as f:
        f.write(render(products, n_mall, len(recs), model))
    with open(os.path.join(OUT, "naver_crossmarket_v3.json"), "w", encoding="utf-8") as f:
        json.dump({"model": model, "n_listings": len(recs), "n_products": len(products),
                   "products": products}, f, ensure_ascii=False, indent=2)
    print(f"통합 SKU(다중몰) {len(products)} · 공식가 아래 {len([p for p in products if p['undercut_pct']>0])} (모델 {model})")
    for p in products[:8]:
        print(f"  {p['name'][:34]:36} {p['n_malls']}몰 개당{p['lowest_unit']:,}~{p['max_unit']:,}"
              f"{'  −'+str(p['undercut_pct'])+'%' if p['undercut_pct'] else ''}")
    print("outputs/naver_crossmarket_v3.html, .json 생성")


if __name__ == "__main__":
    main()
