#!/usr/bin/env python3
"""
크로스마켓 도시에 v2 — ER 엔진 클러스터 기반 (가드+라인+사이즈+재현율 적용).
naver_dossier.py(임시 토큰 규칙)를 대체. 같은 제품이 몰 간 하나로 통합된 결과로
공식가 vs 리셀러 최저가(개당)를 비교한다.

    python3 naver_pull.py 싸이닉 "싸이닉 토너" "싸이닉 선에센스"   # 먼저 수집(키 필요)
    python3 naver_dossier_v2.py
"""
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
sys.path.insert(0, HERE)

from pig.blocking import HybridBlocker
from pig.resolve import resolve
from pig.normalize import extract_attributes
from naver_resolve import load_records


def disp_name(titles):
    t = min(titles, key=len)
    t = re.sub(r"\[[^\]]*\]", " ", t)        # [단독구성] [1+1]
    t = t.split("/")[0]                        # drop "/ benefit..." tail
    t = re.sub(r"(싸이닉)\s+싸이닉", r"\1", t)   # de-dup brand
    return re.sub(r"\s+", " ", t).strip()


def build():
    recs = load_records()
    by_id = {r["id"]: r for r in recs}
    run = resolve(recs, HybridBlocker(), cluster_guard=True)

    products = []
    for cl in run["clusters"]:
        items = [by_id[i] for i in cl]
        if len(items) < 2:
            continue
        a = extract_attributes(items[0])
        off = [r for r in items if "공식" in r["mall"]]
        official_unit = min((r["unit_price"] for r in off), default=None)
        ups = sorted(items, key=lambda r: r["unit_price"])
        lowest = ups[0]
        malls = sorted({r["mall"] for r in items})
        undercut_pct = (round((official_unit - lowest["unit_price"]) / official_unit * 100)
                        if official_unit and lowest["unit_price"] < official_unit else 0)
        products.append({
            "name": disp_name([r["raw_title"] for r in items]),
            "category": a["category"], "size": a["size_token"],
            "lines": a["product_lines"], "n_malls": len(malls), "malls": malls,
            "n_listings": len(items),
            "official_unit": official_unit,
            "lowest_unit": lowest["unit_price"], "lowest_mall": lowest["mall"],
            "max_unit": ups[-1]["unit_price"], "undercut_pct": undercut_pct,
            "has_coupang": any("쿠팡" in m for m in malls),
            "members": [{"mall": r["mall"], "unit": r["unit_price"], "price": r["price"],
                         "qty": r["qty"], "title": r["raw_title"],
                         "official": "공식" in r["mall"]} for r in ups],
        })
    products.sort(key=lambda p: (-p["undercut_pct"], -p["n_malls"]))
    return recs, run, products


def render(recs, run, products):
    n_mall = len({r["mall"] for r in recs})
    undercut = [p for p in products if p["undercut_pct"] > 0]
    coup = [p for p in products if p["has_coupang"]]
    deepest = max((p["undercut_pct"] for p in products), default=0)

    cards = []
    for p in products[:18]:
        rows = "".join(
            f"<tr class='{ 'off' if m['official'] else '' }'><td>{html.escape(m['mall'])}"
            f"{' <span class=pg>공식</span>' if m['official'] else ''}</td>"
            f"<td class=num>{m['price']:,}원</td><td class=num>×{m['qty']}</td>"
            f"<td class=num><b>{m['unit']:,}</b></td></tr>" for m in p["members"][:8])
        ut = (f"<span class=red>공식가보다 −{p['undercut_pct']}%</span>"
              if p["undercut_pct"] else "<span class=mut>공식가 미확인/최저</span>")
        cards.append(f"""<div class=card><h3>{html.escape(p['name'])}
          <span class=mut>· {p['category'] or ''} {p['size'] or ''} · {p['n_malls']}몰 · {p['n_listings']} 리스팅 → 1 제품</span></h3>
          <div class=sum>개당 최저 <b>{p['lowest_unit']:,}원</b>({html.escape(p['lowest_mall'])})
            {('· 공식 '+format(p['official_unit'],',')+'원' if p['official_unit'] else '')} · {ut}</div>
          <table><tr><th>몰</th><th>표시가</th><th>수량</th><th>개당</th></tr>{rows}</table></div>""")

    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SCINIC 크로스마켓 도시에 v2 (ER 엔진)</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--brand-d:#1e4fa8;--red:#d23b3b;--green:#1a9d57;--green-bg:#e6f7ee;--card:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:960px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(244,246,250,.93);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#03c75a;color:#fff;font-weight:900;padding:5px 10px;border-radius:8px}}
h1{{font-size:16px;margin:0;font-weight:800}}h3{{font-size:13.5px;margin:0 0 6px}}
.live{{background:var(--green-bg);color:var(--green);font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.note{{background:var(--green-bg);border:1px solid #9ee3bd;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:var(--green)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px}}
.sum{{font-size:12px;color:var(--mut);margin-bottom:6px}}.sum b{{color:var(--ink)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.3px;font-weight:700;background:#fafbfe}}tr:last-child td{{border-bottom:none}}td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
tr.off{{background:#f0fff6}}.pg{{display:inline-block;font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;background:var(--green-bg);color:var(--green)}}
.red{{color:var(--red);font-weight:700}}.mut{{color:var(--mut);font-weight:500}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>N</span><h1>SCINIC 크로스마켓 도시에 v2 — ER 엔진 클러스터</h1>
<span class=live>REAL · 엔진 통합</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{len(products)}</div><div class=l>통합 제품(다중몰)</div></div>
    <div class=tile><div class=b>{n_mall}</div><div class=l>판매 몰</div></div>
    <div class=tile><div class=b>{len(undercut)}</div><div class=l>공식가 아래 판매</div></div>
    <div class=tile><div class=b>{len(coup)}</div><div class=l>쿠팡 포함 제품</div></div>
  </div>
  <div class=note>✅ <b>임시 토큰 규칙이 아니라 ER 엔진 산출</b> — 가드(전이블롭 차단)+제품라인+ml/g+재현율 수정 적용.
    같은 제품이 몰 간 하나로 통합돼 <b>개당가</b>로 공식가 vs 리셀러 최저가를 비교합니다.
    최대 공식가 침해 <b>−{deepest}%</b>. 쿠팡 가격은 네이버 가격비교 경유로 일부 포함.</div>
  <div class=grid>{''.join(cards)}</div>
  <p class=foot><b>실데이터/정직성.</b> 네이버 쇼핑검색 정식 API({len(recs)} 리스팅) → ER 엔진 클러스터({len([p for p in products])} 다중몰 제품).
    수량('2개'·'3개세트'·'1+1')은 구매단위로 보고 <b>개당가 정규화</b>. 공식가=mallName에 '공식' 포함 스토어 기준.
    잔여: UV엑스퍼트 리페어 vs 톤업 같은 <b>sub-variant 병합은 정책/LLM 영역</b>. 쿠팡 전수·옥션 직접 수집은 별도 게이트.</p>
</div></body></html>"""


def main():
    recs, run, products = build()
    html_out = render(recs, run, products)
    with open(os.path.join(OUT, "naver_crossmarket_v2.html"), "w", encoding="utf-8") as f:
        f.write(html_out)
    with open(os.path.join(OUT, "naver_crossmarket_v2.json"), "w", encoding="utf-8") as f:
        json.dump({"n_listings": len(recs), "n_products": len(products), "products": products},
                  f, ensure_ascii=False, indent=2)
    print(f"통합 제품(다중몰) {len(products)} · 공식가 아래 {len([p for p in products if p['undercut_pct']>0])}")
    for p in products[:10]:
        print(f"  {p['name'][:30]:32} {p['n_malls']}몰 개당 {p['lowest_unit']:,}~{p['max_unit']:,}"
              f"{'  공식 '+format(p['official_unit'],',') if p['official_unit'] else ''}"
              f"{'  −'+str(p['undercut_pct'])+'%' if p['undercut_pct'] else ''}")
    print("outputs/naver_crossmarket_v2.html, .json 생성")


if __name__ == "__main__":
    main()
