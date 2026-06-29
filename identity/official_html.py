#!/usr/bin/env python3
"""
official_extract.py 산출물(공통 스키마 CSV들)을 하나의 HTML 대시보드로.
입력: outputs/extract_*.csv (nike/nb/dongwon)
출력: outputs/official_dashboard.html
보여주는 것: 브랜드별 수집 방식(후크) · 필드 커버리지 · 통합 정형 테이블(검색).
"""
import csv
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")

SOURCES = ["nike", "nb", "dongwon"]
META = {  # 대화에서 확인한 사실
    "nike":    {"label": "Nike", "site": "nike.com/kr", "block": "Akamai (거주지 IP 통과)",
                "hook": "__NEXT_DATA__ (HTML 속 JSON)", "color": "#111"},
    "nb":      {"label": "New Balance", "site": "nbkorea.com", "block": "없음",
                "hook": "DOM data-속성(selDetail) + 상세 정규식", "color": "#cc0000"},
    "dongwon": {"label": "동원몰", "site": "dongwonmall.com", "block": "없음",
                "hook": "JSON-LD (schema.org Product)", "color": "#0a6cff"},
}
COV_FIELDS = ["style_code", "name", "color", "price", "n_sizes", "origin", "material", "category"]


def load():
    data = {}
    for s in SOURCES:
        p = os.path.join(OUT, f"extract_{s}.csv")
        data[s] = list(csv.DictReader(open(p, encoding="utf-8-sig"))) if os.path.exists(p) else []
    return data


def coverage(rows, field):
    if not rows:
        return 0
    f = sum(1 for r in rows if (r.get(field) or "").strip() not in ("", "0"))
    return round(f / len(rows) * 100)


def render(data):
    total = sum(len(v) for v in data.values())
    # 방식 비교 테이블
    method_rows = ""
    for s in SOURCES:
        m = META[s]
        method_rows += (f"<tr><td><span class=dot style='background:{m['color']}'></span>"
                        f"<b>{m['label']}</b><br><span class=mut>{m['site']}</span></td>"
                        f"<td>{html.escape(m['block'])}</td><td><code>{html.escape(m['hook'])}</code></td>"
                        f"<td class=num>{len(data[s])}</td></tr>")
    # 커버리지 매트릭스
    cov_head = "".join(f"<th>{f}</th>" for f in COV_FIELDS)
    cov_rows = ""
    for s in SOURCES:
        cells = ""
        for f in COV_FIELDS:
            c = coverage(data[s], f)
            cls = "c-hi" if c >= 90 else ("c-mid" if c >= 30 else ("c-lo" if c > 0 else "c-no"))
            cells += f"<td class='cov {cls}'>{c}%</td>"
        cov_rows += f"<tr><td><b>{META[s]['label']}</b></td>{cells}</tr>"
    # 통합 테이블 (브랜드별 묶어 스크롤)
    table_rows = ""
    for s in SOURCES:
        for r in data[s]:
            m = META[s]
            sizes = (r.get("sizes") or "").replace("|", " ")
            table_rows += (
                f"<tr data-src='{s}' data-q=\"{html.escape((r.get('style_code','')+' '+r.get('name','')).lower())}\">"
                f"<td><span class=src style='background:{m['color']}'>{m['label']}</span></td>"
                f"<td><code>{html.escape(r.get('style_code',''))}</code></td>"
                f"<td>{html.escape(r.get('name',''))}</td>"
                f"<td>{html.escape(r.get('color',''))}</td>"
                f"<td class=num>{html.escape(str(r.get('price','')))}</td>"
                f"<td class=num>{html.escape(str(r.get('n_sizes','')))}</td>"
                f"<td>{html.escape(r.get('origin',''))}</td>"
                f"<td>{html.escape(r.get('category',''))}</td></tr>")
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>공식몰 통합 정형 데이터 · 자동 추출</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--card:#fff;--green:#1a9d57;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:1120px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(20,22,26,.96);color:#fff;padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#fff;color:#111;font-weight:900;padding:5px 11px;border-radius:8px;font-size:12px;letter-spacing:1px}}
h1{{font-size:16px;margin:0;font-weight:800}}
.live{{background:var(--green);color:#fff;font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:22px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.sect{{font-size:13px;color:var(--ink);font-weight:800;margin:22px 0 8px}}
table{{width:100%;border-collapse:collapse;font-size:12px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;background:#fafbfe;letter-spacing:.3px;position:sticky;top:0}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}tr:last-child td{{border-bottom:none}}
.mut{{color:var(--mut)}}code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11px}}
.dot,.src{{display:inline-block}}.dot{{width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}}
.src{{color:#fff;font-size:10px;font-weight:800;padding:2px 7px;border-radius:6px}}
.cov{{text-align:center;font-weight:800}}.c-hi{{background:#e7f7ee;color:#1a9d57}}.c-mid{{background:#fff6e6;color:#b9770b}}.c-lo{{background:#fdecec;color:#d23b3b}}.c-no{{background:#f1f3f7;color:#aab}}
.scroll{{max-height:560px;overflow:auto;border:1px solid var(--line);border-radius:10px}}
.bar{{display:flex;gap:8px;align-items:center;margin:10px 0 14px}}
input[type=search]{{flex:1;padding:8px 12px;border:1px solid var(--line);border-radius:9px;font-size:13px}}
.filt{{font-size:11px;color:var(--mut)}}.filt b{{color:var(--ink)}}
.note{{background:#eef3fc;border:1px solid #cfe0fb;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:#2452a8}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}
</style></head><body>
<header><div class=wrap><span class=logo>OFFICIAL</span><h1>공식몰 통합 정형 데이터 · 자동 추출</h1>
<span class=live>● 순수 Python · 서버측</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{total}</div><div class=l>총 정형 레코드</div></div>
    <div class=tile><div class=b>{len(SOURCES)}</div><div class=l>브랜드(어댑터)</div></div>
    <div class=tile><div class=b>{len(data['nb'])}</div><div class=l>NB 신발 전수(남/여/키즈)</div></div>
    <div class=tile><div class=b>{len(COV_FIELDS)}</div><div class=l>공통 스키마 핵심필드</div></div>
  </div>
  <div class=note>🔑 세 공식몰을 <b>같은 프레임워크(<code>official_extract.py</code>)·공통 스키마</b>로 추출. 사이트마다 다른 건
    <b>데이터를 박아둔 방식(후크)</b>뿐이고, 그게 곧 <b>필드 커버리지 차이</b>로 드러납니다 — 아래 매트릭스 참고.</div>

  <div class=sect>① 브랜드별 수집 방식</div>
  <table><tr><th>브랜드</th><th>봇차단</th><th>데이터 후크</th><th class=num>레코드</th></tr>{method_rows}</table>

  <div class=sect>② 필드 커버리지 (채워진 비율) — 후크가 정형적일수록 높음</div>
  <table><tr><th>브랜드</th>{cov_head}</tr>{cov_rows}</table>
  <div class=filt style="margin-top:6px">Nike(JSON)는 거의 전 필드 100% · NB(DOM정규식)는 사이즈만 100%·원산지/소재 희박 · 동원(JSON-LD)은 식품이라 신발필드 N/A(중량·평점은 attributes).</div>

  <div class=sect>③ 통합 정형 테이블 ({total}행)</div>
  <div class=bar><input type=search id=q placeholder="스타일코드·이름 검색…" oninput="filt()">
    <span class=filt id=cnt><b>{total}</b>행</span></div>
  <div class=scroll><table id=tbl><tr><th>브랜드</th><th>스타일코드</th><th>이름</th><th>컬러</th><th class=num>가격</th><th class=num>사이즈</th><th>원산지</th><th>카테고리</th></tr>{table_rows}</table></div>

  <p class=foot><b>실데이터/정직성.</b> 출처 = 각 공식몰, 2026-06-26, 전부 순수 Python urllib(서버측, 브라우저 없음).
    Nike는 Akamai라 거주지 IP에서만 200(데이터센터=403). NB 신발 전수=productList.action 페이징(348). 동원=JSON-LD.
    빈 칸은 '오류'가 아니라 '해당 소스가 그 필드를 정형적으로 노출하지 않음'(②의 커버리지가 그 척도).</p>
</div>
<script>
function filt(){{
  var q=document.getElementById('q').value.toLowerCase().trim();
  var rows=document.querySelectorAll('#tbl tr[data-q]');var n=0;
  rows.forEach(function(r){{var hit=!q||r.getAttribute('data-q').indexOf(q)>=0;r.style.display=hit?'':'none';if(hit)n++;}});
  document.getElementById('cnt').innerHTML='<b>'+n+'</b>행';
}}
</script>
</body></html>"""


def main():
    data = load()
    out = os.path.join(OUT, "official_dashboard.html")
    open(out, "w", encoding="utf-8").write(render(data))
    print("브랜드별:", {s: len(data[s]) for s in SOURCES}, "→", os.path.relpath(out))


if __name__ == "__main__":
    main()
