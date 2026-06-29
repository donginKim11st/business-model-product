#!/usr/bin/env python3
"""
모든 브랜드 추출 CSV(워크플로우 brand_* + nike/nb/dongwon)를 공통 스키마로 통합 →
통합 정형 데이터셋 CSV + 브랜드별 커버리지/검색 대시보드 HTML.

입력: outputs/extract_brand_*.csv (14열 스키마) + outputs/extract_{nike,nb,dongwon}.csv (확장 스키마)
출력: outputs/all_brands.csv, outputs/all_brands_dashboard.html
"""
import csv
import glob
import html
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")

# 통합 공통 스키마
FIELDS = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]

# 브랜드 표시명(슬러그/원천 → 한글)
DISPLAY = {
    "nike": "나이키", "nb": "뉴발란스", "newbalance": "뉴발란스", "dongwon": "동원",
    "nepa": "네파", "blackyak": "블랙야크", "montbell": "몽벨", "eider": "아이더",
    "millet": "밀레", "columbia": "컬럼비아", "kolping": "콜핑",
    "outdoorproducts": "아웃도어프로덕츠", "redface": "레드페이스", "mizuno": "미즈노",
    "westwood": "웨스트우드",
    "fila": "휠라", "puma": "푸마", "crocs": "크록스", "lecaf": "르까프",
    "jansport": "잔스포츠", "arena": "아레나", "proworldcup": "프로월드컵",
    "k2": "케이투", "northface": "노스페이스", "natgeo": "내셔널지오그래픽",
    "starsports": "스타스포츠", "skechers": "스케쳐스", "prospecs": "프로스펙스",
    "worldcup": "월드컵", "vans": "반스", "underarmour": "언더아머", "adidas": "아디다스",
}
# 소재가 이미지로만 있어 텍스트 추출 불가로 확인된 브랜드(정직 표기용)
IMAGE_GOSI = {"미즈노", "몽벨", "아웃도어프로덕츠", "웨스트우드"}


def norm_row(r):
    """어떤 스키마든 공통 FIELDS dict로 정규화 (n_sizes 등 무시)."""
    out = {k: (r.get(k) or "").strip() for k in FIELDS}
    if not out["brand"]:
        out["brand"] = DISPLAY.get(out["source"], out["source"])
    # mfg_date가 attributes(JSON)에 든 경우(nb) 끌어올림
    if not out["mfg_date"] and r.get("attributes"):
        try:
            out["mfg_date"] = (json.loads(r["attributes"]).get("mfg_date") or "")
        except Exception:
            pass
    return out


def load_all():
    rows, sources = [], []
    files = sorted(glob.glob(os.path.join(OUT, "extract_brand_*.csv")))
    files += [os.path.join(OUT, f"extract_{s}.csv") for s in ("nike", "nb", "dongwon")]
    for f in files:
        if not os.path.exists(f):
            continue
        rs = list(csv.DictReader(open(f, encoding="utf-8-sig")))
        if len(rs) < 3:  # 1~2행짜리 데모(동원 등) 제외, 아디다스 PoC(3행)는 포함
            sources.append((os.path.basename(f), len(rs), "제외(부분)"))
            continue
        for r in rs:
            rows.append(norm_row(r))
        sources.append((os.path.basename(f), len(rs), "포함"))
    return rows, sources


def disp_brand(r):
    """몰(source) 기준 표시명 — 콜핑몰의 BTR/슈넬 등 입점 브랜드, 휠라 KIDS 라인을
    제각각 brand 컬럼 대신 '몰=공식 브랜드' 하나로 묶는다."""
    return DISPLAY.get(r["source"], r["brand"] or r["source"])


def by_brand(rows):
    bd = defaultdict(lambda: defaultdict(int))
    for r in rows:
        b = disp_brand(r)
        bd[b]["n"] += 1
        for c in ("color", "price", "sizes", "origin", "material", "mfg_date"):
            if r[c]:
                bd[b][c] += 1
    return bd


def render(rows, bd, sources):
    total = len(rows)
    nbrands = len(bd)
    # 전체 커버리지
    def cov(field):
        f = sum(1 for r in rows if r[field])
        return round(f / total * 100) if total else 0
    # 브랜드별 표
    brows = ""
    for b in sorted(bd, key=lambda x: -bd[x]["n"]):
        d = bd[b]
        n = d["n"]
        def pc(c):
            v = round(d[c] / n * 100)
            cls = "c-hi" if v >= 90 else ("c-mid" if v >= 30 else ("c-lo" if v > 0 else "c-no"))
            return f"<td class='cov {cls}'>{v}%</td>"
        img = " <span class=imgflag title='소재가 PDP 이미지에 있어 텍스트 추출 불가'>🖼️이미지고시</span>" if b in IMAGE_GOSI else ""
        brows += (f"<tr><td><b>{html.escape(b)}</b>{img}</td><td class=num>{n}</td>"
                  + pc("color") + pc("price") + pc("origin") + pc("material") + pc("mfg_date") + "</tr>")
    # 통합 검색 테이블
    trows = ""
    TABLE_CAP = 4000  # 60k 통임베드 방지 — 표시 상한(검색은 이 범위 내). 전체는 CSV 참조.
    for r in rows[:TABLE_CAP]:
        g = {"MEN": "남", "WOMEN": "여", "KIDS": "키즈", "UNISEX": "공용"}.get(r["gender"], r["gender"])
        pv = (r["price"] or "").split(".")[0]  # '149000.0' → '149000'
        price = f"{int(pv):,}" if pv.isdigit() else (r["price"] or "")
        q = html.escape((r["brand"] + " " + r["style_code"] + " " + r["name"]).lower())
        trows += (f"<tr data-q=\"{q}\"><td>{html.escape(r['brand'])}</td>"
                  f"<td><code>{html.escape(r['style_code'])}</code></td>"
                  f"<td>{html.escape(r['name'][:40])}</td>"
                  f"<td>{html.escape(r['color'][:18])}</td>"
                  f"<td class=num>{price}</td>"
                  f"<td>{html.escape(g)}</td>"
                  f"<td>{html.escape(r['origin'][:14])}</td>"
                  f"<td>{html.escape(r['material'][:30])}</td></tr>")
    src_note = " · ".join(f"{n}({c}{'' if s=='포함' else ' '+s})" for n, c, s in sources)

    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>브랜드 공식몰 통합 정형 데이터</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--card:#fff;--green:#1a9d57;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:1180px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(20,22,26,.96);color:#fff;padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#fff;color:#111;font-weight:900;padding:5px 11px;border-radius:8px;font-size:12px;letter-spacing:1px}}
h1{{font-size:16px;margin:0;font-weight:800}}
.live{{background:var(--green);color:#fff;font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.note{{background:#eef3fc;border:1px solid #cfe0fb;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:#2452a8}}
.sect{{font-size:13px;color:var(--ink);font-weight:800;margin:22px 0 8px}}
table{{width:100%;border-collapse:collapse;font-size:12px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;background:#fafbfe;position:sticky;top:0;letter-spacing:.3px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}tr:last-child td{{border-bottom:none}}
code{{background:#eef1f6;padding:1px 5px;border-radius:5px;font-size:11px}}
.cov{{text-align:center;font-weight:800}}.c-hi{{background:#e7f7ee;color:#1a9d57}}.c-mid{{background:#fff6e6;color:#b9770b}}.c-lo{{background:#fdecec;color:#d23b3b}}.c-no{{background:#f1f3f7;color:#aab}}
.imgflag{{font-size:9px;font-weight:700;color:#b9770b;background:#fff6e6;padding:1px 5px;border-radius:5px;margin-left:4px}}
.scroll{{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:10px}}
.bar{{display:flex;gap:8px;align-items:center;margin:10px 0 14px}}
input[type=search]{{flex:1;padding:8px 12px;border:1px solid var(--line);border-radius:9px;font-size:13px}}
.filt{{font-size:11px;color:var(--mut)}}.filt b{{color:var(--ink)}}
.foot{{color:var(--mut);font-size:11px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>BRANDS</span><h1>브랜드 공식몰 통합 정형 데이터</h1>
<span class=live>● 순수 Python · 서버측</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{total:,}</div><div class=l>총 정형 레코드</div></div>
    <div class=tile><div class=b>{nbrands}</div><div class=l>브랜드</div></div>
    <div class=tile><div class=b>{cov('origin')}%</div><div class=l>제조국 채움</div></div>
    <div class=tile><div class=b>{cov('material')}%</div><div class=l>소재 채움</div></div>
  </div>
  <div class=note>🔑 30개 목표 중 <b>현재 {nbrands}개 브랜드</b> 확보분. 모두 <b>공식몰에서 순수 Python(서버측)</b>으로 추출, 공통 스키마로 통합.
    <b>소재·제조국·제조년월(상품정보제공고시)</b>은 텍스트로 노출한 몰은 채워지고, <span class=imgflag>🖼️이미지고시</span> 표시 브랜드는 고시가 PDP 이미지에 박혀있어 텍스트 추출 불가(비전/OCR 필요).</div>

  <div class=sect>① 브랜드별 레코드 · 필드 커버리지</div>
  <table><tr><th>브랜드</th><th class=num>레코드</th><th>컬러</th><th>가격</th><th>제조국</th><th>소재</th><th>제조년월</th></tr>{brows}</table>

  <div class=sect>② 통합 정형 테이블 ({total:,}행)</div>
  <div class=bar><input type=search id=q placeholder="브랜드·스타일코드·이름 검색…" oninput="filt()">
    <span class=filt id=cnt><b>{total:,}</b>행</span></div>
  <div class=scroll><table id=tbl><tr><th>브랜드</th><th>스타일코드</th><th>이름</th><th>컬러</th><th class=num>가격</th><th>성별</th><th>제조국</th><th>소재</th></tr>{trows}</table></div>

  <p class=foot><b>실데이터/정직성.</b> 출처 = 각 브랜드 공식 한국몰, 2026-06-26, 순수 Python urllib(서버측). 통합 소스: {html.escape(src_note)}.
    나이키·아디다스 등 Akamai몰은 거주지IP/브라우저 필요. 콜핑 등 일부는 세션리밋으로 부분추출→제외. 소재 0%이거나 🖼️ 표시 브랜드는 고시가 이미지라 비전 추출 대상.</p>
</div>
<script>
function filt(){{var q=document.getElementById('q').value.toLowerCase().trim();
var rows=document.querySelectorAll('#tbl tr[data-q]');var n=0;
rows.forEach(function(r){{var h=!q||r.getAttribute('data-q').indexOf(q)>=0;r.style.display=h?'':'none';if(h)n++;}});
document.getElementById('cnt').innerHTML='<b>'+n.toLocaleString()+'</b>행';}}
</script></body></html>"""


def main():
    rows, sources = load_all()
    bd = by_brand(rows)
    # 통합 CSV
    with open(os.path.join(OUT, "all_brands.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, "all_brands_dashboard.html"), "w", encoding="utf-8") as f:
        f.write(render(rows, bd, sources))
    print(f"통합 {len(rows)}행 / {len(bd)}브랜드 → all_brands.csv, all_brands_dashboard.html")
    for b in sorted(bd, key=lambda x: -bd[x]["n"]):
        d = bd[b]
        print(f"  {b:14} {d['n']:4}행 | 소재 {round(d['material']/d['n']*100):3}% · 제조국 {round(d['origin']/d['n']*100):3}%")


if __name__ == "__main__":
    main()
