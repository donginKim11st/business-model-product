#!/usr/bin/env python3
"""attrs_<slug>.json 들을 하나의 독립형 HTML 뷰어로.
브랜드별로 공식몰 PDP에서 뽑을 수 있는 '모든 속성'(JSON-LD/고시/메타/옵션)을 카테고리별 표로.
출력: outputs/attrs_viewer.html (외부 의존성 0, 단일 파일)"""
import glob
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
CATS = [("jsonld", "JSON-LD", "#2d6cdf"), ("gosi", "상품정보제공고시/스펙", "#1a9d57"),
        ("meta", "메타태그(og/product)", "#b9770b"), ("options", "옵션", "#8b5cf6")]


def esc(s):
    return html.escape(str(s))


def render(brands):
    total_attrs = sum(sum(len(b.get(c, {})) for c, _, _ in CATS) for b in brands)
    # 브랜드 네비 칩
    nav = " ".join(f"<a href='#{b['_slug']}' class=navchip>{esc(b['_brand'])} "
                   f"<b>{sum(len(b.get(c, {})) for c, _, _ in CATS)}</b></a>" for b in brands)
    cards = ""
    for b in brands:
        n_total = sum(len(b.get(c, {})) for c, _, _ in CATS)
        sample = b.get("_sample", {})
        sections = ""
        for key, label, color in CATS:
            d = b.get(key, {})
            if not d:
                continue
            rows = ""
            for k, v in d.items():
                if isinstance(v, list):
                    v = ", ".join(str(x) for x in v)
                rows += f"<tr><td class=k>{esc(k)}</td><td class=v>{esc(v)[:200]}</td></tr>"
            sections += (f"<div class=sect><div class=secthead style='color:{color}'>"
                         f"<span class=dot style='background:{color}'></span>{label} "
                         f"<span class=cnt>{len(d)}</span></div>"
                         f"<table class=attr>{rows}</table></div>")
        err = f"<div class=err>⚠ 수집 실패: {esc(b.get('error', ''))}</div>" if b.get("error") else ""
        cards += (f"<div class=card id='{b['_slug']}'><div class=top>"
                  f"<h2>{esc(b['_brand'])}</h2><span class=tot>{n_total} 속성</span></div>"
                  f"<div class=samp>샘플: <code>{esc(sample.get('style_code', ''))}</code> "
                  f"{esc((sample.get('name') or '')[:40])} "
                  f"· <a href='{esc(sample.get('url', ''))}' target=_blank>PDP↗</a></div>"
                  f"{err}{sections}</div>")
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>공식몰 PDP 추출가능 속성 인벤토리</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--card:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:1100px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(20,22,26,.96);color:#fff;padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#fff;color:#111;font-weight:900;padding:5px 11px;border-radius:8px;font-size:12px;letter-spacing:1px}}
h1{{font-size:16px;margin:0;font-weight:800}}h2{{font-size:15px;margin:0;font-weight:800}}
.kpis{{display:flex;gap:10px;margin:16px 0;flex-wrap:wrap}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 16px}}.tile .b{{font-size:20px;font-weight:800;color:#2d6cdf}}.tile .l{{font-size:11px;color:var(--mut)}}
.note{{background:#eef3fc;border:1px solid #cfe0fb;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:#2452a8}}
.nav{{margin:12px 0;line-height:2}}.navchip{{display:inline-block;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:11.5px;margin:0 4px 4px 0;text-decoration:none;color:var(--ink)}}.navchip b{{color:#2d6cdf}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 18px;margin:14px 0;scroll-margin-top:60px}}
.top{{display:flex;align-items:center;gap:10px}}.tot{{margin-left:auto;font-size:12px;font-weight:800;color:#2d6cdf;background:#eef3fc;padding:3px 10px;border-radius:999px}}
.samp{{font-size:12px;color:var(--mut);margin:5px 0 10px}}.samp a{{color:#2d6cdf;text-decoration:none}}code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11px}}
.sect{{margin:10px 0}}.secthead{{font-size:12px;font-weight:800;margin-bottom:5px;text-transform:uppercase;letter-spacing:.3px}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}}.cnt{{color:var(--mut);font-weight:600}}
table.attr{{width:100%;border-collapse:collapse;font-size:12px}}
table.attr td{{border-bottom:1px solid var(--line);padding:4px 8px;vertical-align:top}}table.attr tr:last-child td{{border-bottom:none}}
td.k{{color:var(--mut);width:38%;font-family:ui-monospace,monospace;font-size:11px;word-break:break-all}}td.v{{font-weight:500;word-break:break-word}}
.err{{background:#fdecec;color:#d23b3b;border-radius:8px;padding:8px 12px;font-size:12px;margin:6px 0}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}
</style></head><body>
<header><div class=wrap><span class=logo>ATTRS</span><h1>공식몰 PDP 추출가능 속성 인벤토리</h1></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{len(brands)}</div><div class=l>브랜드</div></div>
    <div class=tile><div class=b>{total_attrs}</div><div class=l>총 속성(샘플 1개씩)</div></div>
    <div class=tile><div class=b>{max((sum(len(b.get(c,{})) for c,_,_ in CATS)) for b in brands)}</div><div class=l>최다 단일 브랜드</div></div>
  </div>
  <div class=note>🔑 각 공식몰 <b>상품 상세(PDP)에서 실제로 뽑을 수 있는 모든 속성</b>을 브랜드당 샘플 1개로 수집.
    <b>JSON-LD</b>(스키마 표준: sku·gtin·brand·offers·평점·리뷰…) · <b>상품정보제공고시/스펙</b>(소재·제조국·제조년월·제조사·치수·중량·KC고시·배송…) ·
    <b>메타태그</b>(og/product) · <b>옵션</b>(컬러/사이즈). 공통 14컬럼 외에 이만큼 더 있다는 인벤토리. 단일 HTML, 외부 의존성 없음.</div>
  <div class=nav>{nav}</div>
  {cards}
  <p class=foot><b>실데이터.</b> 각 브랜드 공식몰 PDP, 2026-06. 브랜드마다 후크가 달라 속성 종류·개수가 다름(JSON-LD몰은 표준필드 풍부, 자체몰은 고시표 풍부).
    아디다스는 Akamai 봇차단으로 서버측 수집 실패(언블로커/브라우저 필요). 샘플 1개 기준이라 전 상품엔 동일 키가 값만 다르게 반복됨.</p>
</div></body></html>"""


def main():
    brands = []
    for f in sorted(glob.glob(os.path.join(OUT, "attrs_*.json"))):
        b = json.load(open(f, encoding="utf-8"))
        brands.append(b)
    # 속성 많은 순
    brands.sort(key=lambda b: -sum(len(b.get(c, {})) for c, _, _ in CATS))
    out = os.path.join(OUT, "attrs_viewer.html")
    open(out, "w", encoding="utf-8").write(render(brands))
    print(f"{len(brands)}개 브랜드 → {out}")


if __name__ == "__main__":
    main()
