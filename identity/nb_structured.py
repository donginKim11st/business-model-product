#!/usr/bin/env python3
"""
NB 공식 정형 데이터(extract_nb.csv)를 nike_structured.html 과 같은 카드형 뷰로.
입력: outputs/extract_nb.csv (official_extract.py nb enrich 산출, 공통 스키마)
출력: outputs/nb_structured.html
구성: ① 공식 상품 카탈로그(표) ② 상품 스펙(표) ③ 사이즈-재고 단위(카드).
NB는 GTIN 미노출 → 나이키의 GTIN 자리에 '사이즈별 재고(판매중/품절)'.
"""
import csv
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")


def load():
    rows = list(csv.DictReader(open(os.path.join(OUT, "extract_nb.csv"), encoding="utf-8-sig")))
    for r in rows:
        r["attr"] = json.loads(r["attributes"]) if r["attributes"] else {}
        r["size_list"] = [s for s in (r.get("sizes") or "").split("|") if s]
        r["soldout"] = set(r["attr"].get("soldout_sizes") or [])
    return rows


def render(rows):
    from collections import Counter
    n = len(rows)
    with_origin = sum(1 for r in rows if r["origin"])
    with_color = sum(1 for r in rows if r["color"])
    prices = sorted(int(r["price"]) for r in rows if r["price"].isdigit())
    pmid = prices[len(prices) // 2] if prices else 0
    total_sizes = sum(len(r["size_list"]) for r in rows)
    gc = Counter(r.get("gender") or "기타" for r in rows)
    glabel = {"MEN": "남성", "WOMEN": "여성", "KIDS": "키즈", "기타": "기타"}
    gbreak = " · ".join(f"{glabel.get(g, g)} {c}" for g, c in gc.most_common())

    # ① 카탈로그 표
    cat_rows = ""
    for r in rows:
        a = r["attr"]
        nor = a.get("nor_price", "")
        price_cell = f"{int(r['price']):,}원" if r["price"].isdigit() else r["price"]
        if a.get("discounted") and nor:
            price_cell = f"<b>{price_cell}</b> <span class=msrp>{int(nor):,}</span>"
        g = {"MEN": "남성", "WOMEN": "여성", "KIDS": "키즈"}.get(r.get("gender", ""), r.get("gender", ""))
        cat_rows += (f"<tr><td><code>{html.escape(r['style_code'])}</code></td>"
                     f"<td>{html.escape(r['name'])}</td>"
                     f"<td>{html.escape(g) or '<span class=na>—</span>'}</td>"
                     f"<td>{html.escape(r['color']) or '<span class=na>—</span>'}</td>"
                     f"<td class=num>{price_cell}</td></tr>")
    # ② 스펙 표 (상품정보제공고시 의무표기 포함)
    spec_rows = ""
    for r in rows:
        krs = r["size_list"]
        rng = f"{krs[0]}~{krs[-1]}mm" if krs else ""
        a = r["attr"]
        na = "<span class=na>—</span>"
        spec_rows += (f"<tr><td><code>{html.escape(r['style_code'])}</code></td>"
                      f"<td>{html.escape(r['origin']) or na}</td>"
                      f"<td>{html.escape(a.get('mfg_date', '')) or na}</td>"
                      f"<td>{html.escape(r['material'][:40]) or na}</td>"
                      f"<td class=num>{len(krs)}</td><td>{rng}</td>"
                      f"<td>{html.escape(a.get('width', '')) or na}</td></tr>")
    # ③ 사이즈-재고 카드 (상위 6개)
    cards = ""
    for r in [x for x in rows if x["size_list"]][:6]:
        grid = "".join(
            f"<span class='sz {'so' if s in r['soldout'] else 'ok'}'>{html.escape(s)}</span>"
            for s in r["size_list"])
        avail = len(r["size_list"]) - len(r["soldout"] & set(r["size_list"]))
        cards += (f"<div class=gcard><h4><code>{html.escape(r['style_code'])}</code> "
                  f"{html.escape(r['name'])} <span class=mut>({avail}/{len(r['size_list'])} 판매중)</span></h4>"
                  f"<div class=szgrid>{grid}</div></div>")

    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>뉴발란스 공식 정형 데이터 · nbkorea.com</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#cc0000;--card:#fff;--green:#1a9d57;--red:#d23b3b;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(20,22,26,.96);color:#fff;padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:var(--brand);color:#fff;font-weight:900;padding:5px 11px;border-radius:8px;font-size:13px;letter-spacing:1px}}
h1{{font-size:16px;margin:0;font-weight:800}}
.live{{background:var(--green);color:#fff;font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.note{{background:#fdeeee;border:1px solid #f3c9c9;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:var(--brand)}}
.sect{{font-size:12.5px;color:var(--ink);font-weight:800;margin:22px 0 8px}}.sub{{font-size:11.5px;color:var(--mut);margin:-4px 0 8px}}
.scroll{{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:10px}}
table{{width:100%;border-collapse:collapse;font-size:12px;background:var(--card)}}
th,td{{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;background:#fafbfe;position:sticky;top:0;letter-spacing:.3px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}tr:last-child td{{border-bottom:none}}
code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11px}}.mut{{color:var(--mut);font-weight:500}}.na{{color:#c0c6d0}}
.msrp{{font-size:10px;color:var(--mut);text-decoration:line-through}}
.gwrap{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:760px){{.gwrap{{grid-template-columns:1fr}}}}
.gcard{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:11px 13px}}.gcard h4{{font-size:12px;margin:0 0 8px;font-weight:700}}
.szgrid{{display:flex;flex-wrap:wrap;gap:5px}}
.sz{{font-size:11px;font-weight:700;padding:3px 7px;border-radius:6px;font-variant-numeric:tabular-nums}}
.sz.ok{{background:#e7f7ee;color:var(--green)}}.sz.so{{background:#f1f3f7;color:#aab;text-decoration:line-through}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>NB</span><h1>뉴발란스 공식 정형 데이터 · nbkorea.com</h1>
<span class=live>● REAL · 서버측 전수</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{n}</div><div class=l>공식 신발 전수(남/여/키즈)</div></div>
    <div class=tile><div class=b>{total_sizes:,}</div><div class=l>사이즈 SKU(스타일×사이즈)</div></div>
    <div class=tile><div class=b>{round(with_color / n * 100) if n else 0}%</div><div class=l>컬러 확보(PDP)</div></div>
    <div class=tile><div class=b>{pmid:,}</div><div class=l>중앙 가격(원)</div></div>
  </div>
  <div class=note>🔑 nbkorea.com <code>productList.action</code> 3개 신발 카테고리(남성·여성·키즈) 서버측 전수 + 상세 병합 — <b>{gbreak}</b>.
    <b>컬러는 PDP의 <code>#optColName</code>에서 추출(100%), 발볼 폭(D·2E·4E)·성별은 별도</b>. 원산지·소재는 NB가 정형적으로 노출하지 않아 일부만({with_origin}/{n}). NB는 GTIN 미노출이라 ③은 사이즈별 재고로 대체.</div>

  <div class=sect>① 공식 상품 카탈로그 ({n}건)</div>
  <div class=sub>스타일코드 · 모델명 · 성별 · 컬러(PDP) · 가격(할인 시 정상가)</div>
  <div class=scroll><table><tr><th>스타일코드</th><th>모델명</th><th>성별</th><th>컬러</th><th class=num>가격</th></tr>{cat_rows}</table></div>

  <div class=sect>② 상품 스펙 ({n}건) — 상품정보제공고시</div>
  <div class=sub>제조국 · 제조년월 · 소재 · 사이즈 수 · 사이즈 범위(KR mm) · 폭</div>
  <div class=scroll><table><tr><th>스타일코드</th><th>제조국</th><th>제조년월</th><th>소재</th><th class=num>사이즈수</th><th>범위</th><th>폭</th></tr>{spec_rows}</table></div>

  <div class=sect>③ 사이즈-재고 단위 (스타일코드 × 사이즈 → 판매중/품절)</div>
  <div class=sub>전 {n}개 상품의 사이즈는 extract_nb.csv 참조 · 아래는 상위 6개 샘플 (<span style='color:var(--green)'>판매중</span> / <span style='color:#aab'>품절</span>)</div>
  <div class=gwrap>{cards}</div>

  <p class=foot><b>실데이터/정직성.</b> 출처 = nbkorea.com, 2026-06-26, 순수 Python urllib(서버측, 브라우저 없음).
    신발 전수 {n}개 = <code>productList.action?cateGrpCode=250110&cIdx=(1280·1320·1353)</code> 남/여/키즈 페이징. 컬러는 PDP <code>#optColName</code>(100%). 원산지·소재는 NB가 정형적으로 안 내줘 일부만(나이키 __NEXT_DATA__와 달리 NB는 DOM/정규식).</p>
</div></body></html>"""


def main():
    rows = load()
    out = os.path.join(OUT, "nb_structured.html")
    open(out, "w", encoding="utf-8").write(render(rows))
    print(f"NB {len(rows)}개 → {os.path.relpath(out)}")


if __name__ == "__main__":
    main()
