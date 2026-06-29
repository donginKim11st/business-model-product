#!/usr/bin/env python3
"""
나이키 공식(nike.com/kr) '진짜 정형 데이터' 빌더.

입력: outputs/nike_official_structured.json (브라우저 세션의 __NEXT_DATA__에서 수집)
      - wide: 검색 Wall 레코드(스타일코드/제목/성별·카테고리/공식컬러/정규화 base color/뱃지/라인)
      - deep: 상품 페이지 레코드(성별/원산지/sport/taxonomy/설명 + 사이즈 전체 US·KR mm·GTIN·재고)

출력(정형 CSV 3종 + HTML):
  outputs/nike_official_catalog.csv  : 공식 상품 카탈로그(wide, 189행)
  outputs/nike_official_specs.csv    : 상품 스펙(deep, 17행, 원산지/사이즈수 등)
  outputs/nike_official_skus.csv     : GTIN-사이즈 단위(가장 깊은 정형: 스타일코드×사이즈→GTIN)
  outputs/nike_structured.html       : 위 정형 데이터 뷰

핵심: 마켓 제목 파싱이 아니라 '공식 소스의 권위 속성'이라
      base_color/성별/카테고리/원산지/GTIN가 전부 채워진다(제목 파싱본은 비었던 칸).
"""
import csv
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")


def load():
    d = json.load(open(os.path.join(OUT, "nike_official_structured.json"), encoding="utf-8"))
    return d["wide"], [x for x in d["deep"] if x.get("ok")]


def write_catalog_csv(wide):
    cols = ["style_code", "type", "subtype", "title", "gender_cat", "colorway",
            "base_color", "base_hex", "badge", "featured", "is_new", "line_q", "pdp"]
    path = os.path.join(OUT, "nike_official_catalog.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in wide:
            w.writerow(r)
    return path


def write_specs_csv(deep):
    cols = ["style_code", "gender", "colorway", "origin", "sport", "taxonomy",
            "net_quantity", "n_sizes", "size_range_kr", "availability", "desc"]
    path = os.path.join(OUT, "nike_official_specs.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in deep:
            krs = [s["kr"] for s in r["sizes"] if s.get("kr")]
            w.writerow({
                "style_code": r["code"], "gender": r["gender"], "colorway": r["colorway"],
                "origin": r["origin"], "sport": r["sport"], "taxonomy": r["taxonomy"],
                "net_quantity": r["net_quantity"], "n_sizes": len(r["sizes"]),
                "size_range_kr": (f"{krs[0]}~{krs[-1]}" if krs else ""),
                "availability": r["availability"], "desc": r["desc"],
            })
    return path


def write_skus_csv(deep):
    """가장 깊은 정형 데이터: 스타일코드 × 사이즈 → GTIN."""
    cols = ["style_code", "colorway", "size_us", "size_kr_mm", "gtin", "status"]
    path = os.path.join(OUT, "nike_official_skus.csv")
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in deep:
            for s in r["sizes"]:
                w.writerow({"style_code": r["code"], "colorway": r["colorway"],
                            "size_us": s.get("us"), "size_kr_mm": s.get("kr"),
                            "gtin": s.get("gtin"), "status": s.get("status")})
                n += 1
    return path, n


def render(wide, deep, n_skus):
    lines = sorted({w["line_q"] for w in wide})
    # wide 테이블
    wrows = ""
    for r in wide:
        sw = (f"<span class=swatch style='background:#{html.escape(r['base_hex'])}'></span>"
              if r.get("base_hex") else "")
        nb = "<span class=nb>NEW</span>" if r.get("is_new") else ""
        wrows += (f"<tr><td><code>{html.escape(r['style_code'])}</code></td>"
                  f"<td>{html.escape(r['title'])}{nb}</td>"
                  f"<td>{html.escape(r['gender_cat'])}</td>"
                  f"<td>{html.escape(r['colorway'])}</td>"
                  f"<td>{sw}{html.escape(r['base_color'])}</td>"
                  f"<td>{html.escape(r.get('badge',''))}</td></tr>")
    # deep 스펙 테이블
    drows = ""
    for r in deep:
        krs = [s["kr"] for s in r["sizes"] if s.get("kr")]
        rng = f"{krs[0]}~{krs[-1]}mm" if krs else ""
        drows += (f"<tr><td><code>{html.escape(r['code'])}</code></td>"
                  f"<td>{html.escape(r['gender'])}</td>"
                  f"<td>{html.escape(r['colorway'])}</td>"
                  f"<td>{html.escape(r['origin'])}</td>"
                  f"<td>{html.escape(r['sport'])}</td>"
                  f"<td class=num>{len(r['sizes'])}</td>"
                  f"<td>{rng}</td></tr>")
    # GTIN 단위 샘플(상위 3개 SKU의 전체 사이즈)
    gtin_cards = ""
    for r in deep[:3]:
        grows = "".join(
            f"<tr><td>{html.escape(s.get('us',''))}</td><td>{html.escape(s.get('kr',''))}</td>"
            f"<td><code>{html.escape(s.get('gtin',''))}</code></td>"
            f"<td><span class='st {'ok' if s.get('status')=='ACTIVE' else 'no'}'>{html.escape(s.get('status',''))}</span></td></tr>"
            for s in r["sizes"])
        gtin_cards += (f"<div class=gcard><h4><code>{html.escape(r['code'])}</code> · "
                       f"{html.escape(r['colorway'])} <span class=mut>({len(r['sizes'])} 사이즈)</span></h4>"
                       f"<table class=g><tr><th>US</th><th>KR(mm)</th><th>GTIN(바코드)</th><th>재고</th></tr>{grows}</table></div>")

    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>나이키 공식 정형 데이터 · nike.com</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--green:#1a9d57;--card:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(20,22,26,.96);color:#fff;padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#fff;color:#111;font-weight:900;padding:5px 11px;border-radius:8px;font-size:13px;letter-spacing:1px}}
h1{{font-size:16px;margin:0;font-weight:800}}
.live{{background:var(--green);color:#fff;font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.note{{background:#eef3fc;border:1px solid #cfe0fb;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:#2452a8}}
.sect{{font-size:12.5px;color:var(--ink);font-weight:800;margin:22px 0 8px}}
.sub{{font-size:11.5px;color:var(--mut);margin:-4px 0 8px}}
.scroll{{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:10px}}
table{{width:100%;border-collapse:collapse;font-size:12px;background:var(--card)}}
th,td{{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;background:#fafbfe;position:sticky;top:0;letter-spacing:.3px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}tr:last-child td{{border-bottom:none}}
code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11px}}
.swatch{{display:inline-block;width:11px;height:11px;border-radius:3px;border:1px solid #0002;margin-right:5px;vertical-align:middle}}
.nb{{background:#e9f8f0;color:var(--green);font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;margin-left:5px}}
.mut{{color:var(--mut);font-weight:500}}
.gwrap{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}}@media(max-width:820px){{.gwrap{{grid-template-columns:1fr}}}}
.gcard{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:11px 13px}}.gcard h4{{font-size:12px;margin:0 0 7px}}
table.g td,table.g th{{padding:3px 6px;font-size:11px}}
.st{{font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px}}.st.ok{{background:#e9f8f0;color:var(--green)}}.st.no{{background:#f1f3f7;color:var(--mut)}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>NIKE</span><h1>나이키 공식 정형 데이터 · nike.com</h1>
<span class=live>● REAL · __NEXT_DATA__</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{len(wide)}</div><div class=l>공식 상품(정형 레코드)</div></div>
    <div class=tile><div class=b>{len(deep)}</div><div class=l>상품 스펙(원산지·사이즈)</div></div>
    <div class=tile><div class=b>{n_skus}</div><div class=l>GTIN-사이즈 SKU</div></div>
    <div class=tile><div class=b>{len(lines)}</div><div class=l>모델 라인</div></div>
  </div>
  <div class=note>🔑 이건 <b>마켓 제목 파싱이 아니라 nike.com 공식 소스의 권위 속성</b>입니다. 그래서 우리 제목 파싱본에선 비어있던
    <b>정규화 컬러(base color)·성별·카테고리·원산지·GTIN·사이즈 전체</b>가 전부 채워져 있습니다. 조인 키 = 스타일코드.</div>

  <div class=sect>① 공식 상품 카탈로그 (정형 레코드 {len(wide)}건)</div>
  <div class=sub>스타일코드 · 제품명 · 성별/카테고리 · 공식 컬러명 · 정규화 base color(스와치) · 뱃지</div>
  <div class=scroll><table><tr><th>스타일코드</th><th>제품명</th><th>성별/카테고리</th><th>공식 컬러명</th><th>base color</th><th>뱃지</th></tr>{wrows}</table></div>

  <div class=sect>② 상품 스펙 (deep {len(deep)}건 — 원산지·sport·사이즈)</div>
  <div class=sub>성별 · 공식 컬러 · 원산지(제조국) · 스포츠 분류 · 사이즈 수 · 사이즈 범위(KR mm)</div>
  <div class=scroll><table><tr><th>스타일코드</th><th>성별</th><th>공식 컬러</th><th>원산지</th><th>sport</th><th class=num>사이즈수</th><th>범위</th></tr>{drows}</table></div>

  <div class=sect>③ GTIN-사이즈 단위 (가장 깊은 정형: 스타일코드 × 사이즈 → 바코드)</div>
  <div class=sub>전체 {n_skus} SKU는 nike_official_skus.csv 참조 · 아래는 상위 3개 SKU 샘플</div>
  <div class=gwrap>{gtin_cards}</div>

  <p class=foot><b>실데이터/정직성.</b> 출처 = nike.com/kr 실제 브라우저 세션의 <code>__NEXT_DATA__</code>(검색 Wall + 상품 페이지),
    2026-06-26 수집. nike.com은 Akamai 봇차단으로 서버측 스크립트 불가 → 실제 브라우저 경로로 same-origin fetch.
    wide는 라인별 검색 첫 페이지(라인당 ~24, 무한스크롤 전), deep은 우리 카탈로그와 스타일코드 정확일치한 SKU의 상품 페이지. 가격 필드는 본 뷰에서 제외(정형 속성 중심).</p>
</div></body></html>"""


def main():
    wide, deep = load()
    p1 = write_catalog_csv(wide)
    p2 = write_specs_csv(deep)
    p3, n_skus = write_skus_csv(deep)
    with open(os.path.join(OUT, "nike_structured.html"), "w", encoding="utf-8") as f:
        f.write(render(wide, deep, n_skus))
    print(f"공식 상품 {len(wide)} · 스펙 {len(deep)} · GTIN-사이즈 {n_skus}")
    print(f"→ {os.path.basename(p1)}, {os.path.basename(p2)}, {os.path.basename(p3)}, nike_structured.html")


if __name__ == "__main__":
    main()
