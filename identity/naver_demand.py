#!/usr/bin/env python3
"""
'수요 × 가격' — 가격 스냅샷에 수요 신호를 붙여 actionable하게.
수요 프록시 = 네이버 블로그 언급량(search/blog.json 의 total). 같은 NAVER 키 재사용.
시계열 baseline 으로 outputs/snapshots/ 에 날짜별 저장(다음 실행 시 변화 비교).

    python3 naver_dossier_v3.py     # 먼저 가격/제품 데이터 생성
    NAVER_CLIENT_ID=.. NAVER_CLIENT_SECRET=.. python3 naver_demand.py
"""
import datetime
import html
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")


def blog_total(keyword, cid, csec):
    url = "https://openapi.naver.com/v1/search/blog.json?" + urllib.parse.urlencode(
        {"query": keyword, "display": 1})
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", cid)
    req.add_header("X-Naver-Client-Secret", csec)
    with urllib.request.urlopen(req, timeout=10) as r:
        return int(json.load(r).get("total", 0))


def demand_kw(name, size):
    t = name.replace(size or "", "")
    toks = [x for x in t.split() if not re.match(r"^\d", x)]
    return " ".join(toks[:5]).strip()


def main():
    cid, csec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        print("✗ NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 필요"); sys.exit(1)
    data = json.load(open(os.path.join(OUT, "naver_crossmarket_v3.json"), encoding="utf-8"))
    prods = [p for p in data["products"] if p.get("official_unit")]
    prods = sorted(prods, key=lambda p: -p["undercut_pct"])[:14]  # 블로그 호출 제한

    rows = []
    for p in prods:
        kw = demand_kw(p["name"], p.get("size"))
        try:
            demand = blog_total(kw, cid, csec)
        except Exception as e:
            demand = 0
            print("  (blog 오류:", kw, e, ")")
        rows.append({"name": p["name"], "keyword": kw, "demand": demand,
                     "undercut_pct": p["undercut_pct"], "n_malls": p["n_malls"],
                     "official_unit": p["official_unit"], "lowest_unit": p["lowest_unit"],
                     "lowest_mall": p["lowest_mall"], "has_coupang": p.get("has_coupang")})

    dmax = max((r["demand"] for r in rows), default=1) or 1
    umax = max((r["undercut_pct"] for r in rows), default=1) or 1
    dmed = sorted(r["demand"] for r in rows)[len(rows) // 2] if rows else 0
    umed = sorted(r["undercut_pct"] for r in rows)[len(rows) // 2] if rows else 0
    for r in rows:
        # 우선순위 = 수요(정규화) × 침해율 — 잘 팔리면서 많이 깎인 것
        r["priority"] = round(math.sqrt(r["demand"] / dmax) * (r["undercut_pct"] / 100), 4)
        r["quadrant"] = ("최우선" if r["demand"] >= dmed and r["undercut_pct"] >= umed else
                         ("수요높음·가격안정" if r["demand"] >= dmed else
                          ("저수요·고침해" if r["undercut_pct"] >= umed else "저수요·안정")))
    rows.sort(key=lambda r: -r["priority"])

    today = datetime.date.today().isoformat()
    os.makedirs(os.path.join(OUT, "snapshots"), exist_ok=True)
    snap = os.path.join(OUT, "snapshots", f"demand_price_{today}.json")
    json.dump({"date": today, "rows": rows}, open(snap, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    # 이전 스냅샷과 변화(있으면)
    prev = sorted(f for f in os.listdir(os.path.join(OUT, "snapshots"))
                  if f.startswith("demand_price_") and f != f"demand_price_{today}.json")
    changes = []
    if prev:
        old = {r["name"]: r for r in json.load(open(os.path.join(OUT, "snapshots", prev[-1]), encoding="utf-8"))["rows"]}
        for r in rows:
            o = old.get(r["name"])
            if o and o["lowest_unit"] != r["lowest_unit"]:
                changes.append(f"{r['name'][:24]}: 최저 {o['lowest_unit']:,}→{r['lowest_unit']:,}")

    _render(rows, dmed, umed, dmax, umax, today, prev, changes)
    print(f"제품 {len(rows)} · 수요 범위 {min(r['demand'] for r in rows):,}~{dmax:,} · 스냅샷 {today}")
    for r in rows[:6]:
        print(f"  [{r['quadrant']:11}] {r['name'][:28]:30} 수요 {r['demand']:>6,} · −{r['undercut_pct']}% · 점수 {r['priority']}")
    print("outputs/demand_price.html, snapshots/demand_price_%s.json 생성" % today)


def _render(rows, dmed, umed, dmax, umax, today, prev, changes):
    W, H, PAD = 760, 360, 46
    def X(d): return PAD + (math.log10(max(d, 1)) / math.log10(max(dmax, 10))) * (W - 2 * PAD)
    def Y(u): return H - PAD - (u / (umax or 1)) * (H - 2 * PAD)
    dots = []
    for i, r in enumerate(rows):
        hot = r["quadrant"] == "최우선"
        dots.append(f'<circle cx="{X(r["demand"]):.0f}" cy="{Y(r["undercut_pct"]):.0f}" r="{8 if hot else 5}" '
                    f'fill="{"#d23b3b" if hot else "#94a3b8"}" fill-opacity="0.85"/>'
                    f'<text x="{X(r["demand"]):.0f}" y="{Y(r["undercut_pct"])-10:.0f}" font-size="9" '
                    f'text-anchor="middle" fill="#465569">{html.escape(r["name"].split()[1] if len(r["name"].split())>1 else r["name"][:6])}</text>')
    qx, qy = X(dmed), Y(umed)
    table = "".join(
        f"<tr class='{'hot' if r['quadrant']=='최우선' else ''}'><td>{html.escape(r['name'][:32])}</td>"
        f"<td class=num>{r['demand']:,}</td><td class=num>−{r['undercut_pct']}%</td>"
        f"<td class=num>{r['lowest_unit']:,}원<br><span class=mut>{html.escape(r['lowest_mall'])}</span></td>"
        f"<td><span class=pill>{r['quadrant']}</span></td></tr>" for r in rows)
    chg = ("".join(f"<li>{html.escape(c)}</li>" for c in changes) if changes else
           (f"<li class=mut>이전 스냅샷({prev[-1].split('_')[-1][:10] if prev else '—'}) 대비 변화 없음</li>" if prev
            else "<li class=mut>baseline 1회차 — 다음 실행 시 변화가 여기에 표시됩니다</li>"))
    open(os.path.join(OUT, "demand_price.html"), "w", encoding="utf-8").write(f"""<!doctype html>
<html lang=ko><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>수요 × 가격 — SCINIC</title><style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--red:#d23b3b;--card:#fff}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:840px;margin:0 auto;padding:18px 20px 56px}}
h1{{font-size:17px;margin:0 0 2px}}.sub{{color:var(--mut);font-size:12.5px;margin-bottom:14px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px 17px;margin-bottom:14px}}
.q{{font-size:10px;fill:var(--mut)}} table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line)}}th{{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.3px;font-weight:700;background:#fafbfe}}
tr:last-child td{{border-bottom:none}}td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}tr.hot{{background:#fff5f5}}
.pill{{font-size:10px;font-weight:800;padding:2px 7px;border-radius:999px;background:#eef1f7;color:#465569}}tr.hot .pill{{background:#fdecec;color:var(--red)}}
.mut{{color:var(--mut)}}.foot{{color:var(--mut);font-size:11px;border-top:1px solid var(--line);padding-top:10px;margin-top:6px;line-height:1.6}}
ul{{margin:6px 0;padding-left:18px;font-size:12.5px}}
</style></head><body><div class=wrap>
<h1>수요 × 가격 — SCINIC</h1><div class=sub>가격 한 컷에 <b>수요(블로그 언급량)</b>를 붙여 '무엇부터'를 가림 · {today} · 실데이터</div>
<div class=card><h3 style="margin:0 0 4px;font-size:13px">📈 수요×가격 4분면</h3>
<div class=mut style="font-size:11px;margin-bottom:6px">오른쪽=수요↑(로그) · 위=공식가 아래↑ · <b style="color:var(--red)">우상단(빨강)=최우선</b>(잘 팔리는데 많이 깎임)</div>
<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px">
<line x1="{qx:.0f}" y1="{PAD}" x2="{qx:.0f}" y2="{H-PAD}" stroke="#cfd6e2" stroke-dasharray="3 3"/>
<line x1="{PAD}" y1="{qy:.0f}" x2="{W-PAD}" y2="{qy:.0f}" stroke="#cfd6e2" stroke-dasharray="3 3"/>
<text x="{W-PAD}" y="{PAD-6}" class=q text-anchor=end fill="#d23b3b">↑침해 ·수요→  = 최우선</text>
<text x="{PAD}" y="{H-PAD+18}" class=q>수요 적음</text><text x="{W-PAD}" y="{H-PAD+18}" class=q text-anchor=end>수요 많음(블로그 언급)</text>
{''.join(dots)}</svg></div>
<div class=card><h3 style="margin:0 0 8px;font-size:13px">📋 우선순위 (수요 × 침해율)</h3>
<table><tr><th>제품</th><th>수요(블로그)</th><th>공식가 아래</th><th>최저(1개)</th><th>분면</th></tr>{table}</table></div>
<div class=card><h3 style="margin:0 0 6px;font-size:13px">🔔 변화 감지 (시계열)</h3><ul>{chg}</ul></div>
<p class=foot><b>정직성.</b> 수요 = 네이버 블로그 언급량(`search/blog.json` total) — 진짜 판매량의 <b>프록시</b>(버즈 기준, 정확한 판매량 아님).
가격은 1개당. 스냅샷을 <code>outputs/snapshots/</code>에 날짜별 저장 → 재실행 시 변화 비교(시계열 시작점).
더 정밀한 수요엔 다나와 리뷰수·평점, 검색량(데이터랩), 마켓 랭킹 추가 가능.</p>
</div></body></html>""")


if __name__ == "__main__":
    main()
