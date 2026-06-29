#!/usr/bin/env python3
"""
수요 × 가격 (업그레이드) — 수요 프록시를 블로그 언급량 → **다나와 리뷰수·평점**으로.
다나와 vssearch JSON API(`prod.danawa.com/api/vssearch/searchProducts.php`)에서 제품별
review_count + star_point + min_price 를 받아 우리 SCINIC 가격 데이터에 조인.
stdlib만(urllib). 키 불필요(다나와 공개 AJAX).

    python3 naver_dossier_v3.py   # 가격/제품 데이터 먼저
    python3 danawa_demand.py
"""
import datetime
import html
import json
import math
import os
import re
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
API = "https://prod.danawa.com/api/vssearch/searchProducts.php"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_TAG = re.compile(r"<[^>]+>")
_STOP = {"싸이닉", "더", "심플", "엔조이", "정품", "ml", "g"}


def danawa_search(keyword):
    q = urllib.parse.urlencode({"keyword": keyword, "page": 1, "limit": 24})
    req = urllib.request.Request(API + "?" + q)
    req.add_header("User-Agent", UA)
    req.add_header("Referer", "https://search.danawa.com/dsearch.php?query=" + urllib.parse.quote(keyword))
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Accept", "application/json, text/javascript, */*; q=0.01")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            js = json.load(r)
    except Exception:
        return []
    prods = (js.get("result") or {}).get("products") or []
    out = []
    for p in prods:
        out.append({"name": _TAG.sub("", p.get("productName") or ""),
                    "star": p.get("starPoint"), "reviews": int(p.get("reviewCount") or 0),
                    "min_price": int(p.get("minPrice") or 0)})
    return out


def _toks(name):
    return {t for t in re.split(r"[^가-힣a-zA-Z0-9]+", name.lower()) if len(t) >= 2 and t not in _STOP}


def best_match(product, cands):
    """사이즈 일치 + 토큰 최다 겹침 중 리뷰 많은 것."""
    size = product.get("size")
    ptoks = _toks(product["name"])
    scored = []
    for c in cands:
        if size and size not in c["name"].replace(" ", ""):
            continue
        overlap = len(ptoks & _toks(c["name"]))
        if overlap < 2:
            continue
        scored.append((overlap, c["reviews"], c))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return scored[0][2]


def kw_for(product):
    toks = [t for t in product["name"].split() if not re.match(r"^\d", t)]
    return " ".join(toks[:5])


def main():
    data = json.load(open(os.path.join(OUT, "naver_crossmarket_v3.json"), encoding="utf-8"))
    prods = sorted((p for p in data["products"] if p.get("official_unit")),
                   key=lambda p: -p["undercut_pct"])[:14]
    rows = []
    for p in prods:
        cands = danawa_search(kw_for(p))
        m = best_match(p, cands)
        rows.append({"name": p["name"], "reviews": m["reviews"] if m else 0,
                     "rating": (m["star"] if m else None), "danawa_min": m["min_price"] if m else None,
                     "undercut_pct": p["undercut_pct"], "n_malls": p["n_malls"],
                     "official_unit": p["official_unit"], "lowest_unit": p["lowest_unit"],
                     "lowest_mall": p["lowest_mall"], "matched": bool(m),
                     "danawa_name": m["name"] if m else None})
    rmax = max((r["reviews"] for r in rows), default=1) or 1
    rmed = sorted(r["reviews"] for r in rows)[len(rows) // 2] if rows else 0
    umed = sorted(r["undercut_pct"] for r in rows)[len(rows) // 2] if rows else 0
    umax = max((r["undercut_pct"] for r in rows), default=1) or 1
    for r in rows:
        r["priority"] = round(math.sqrt(r["reviews"] / rmax) * (r["undercut_pct"] / 100), 4)
        r["quadrant"] = ("최우선" if r["reviews"] >= rmed and r["undercut_pct"] >= umed else
                         ("수요높음·가격안정" if r["reviews"] >= rmed else
                          ("저수요·고침해" if r["undercut_pct"] >= umed else "저수요·안정")))
    rows.sort(key=lambda r: -r["priority"])

    today = datetime.date.today().isoformat()
    os.makedirs(os.path.join(OUT, "snapshots"), exist_ok=True)
    json.dump({"date": today, "source": "danawa_reviews", "rows": rows},
              open(os.path.join(OUT, "snapshots", f"danawa_demand_{today}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    _render(rows, rmed, umed, rmax, umax, today)
    print(f"제품 {len(rows)} · 다나와 매칭 {sum(r['matched'] for r in rows)} · 리뷰 범위 {min(r['reviews'] for r in rows):,}~{rmax:,}")
    for r in rows[:8]:
        print(f"  [{r['quadrant']:11}] {r['name'][:26]:28} 리뷰 {r['reviews']:>6,} 평점 {r['rating']} · −{r['undercut_pct']}% · 점수 {r['priority']}")
    print("outputs/danawa_demand.html 생성")


def _render(rows, rmed, umed, rmax, umax, today):
    W, H, PAD = 760, 360, 46
    def X(d): return PAD + (math.log10(max(d, 1)) / math.log10(max(rmax, 10))) * (W - 2 * PAD)
    def Y(u): return H - PAD - (u / (umax or 1)) * (H - 2 * PAD)
    dots = []
    for r in rows:
        hot = r["quadrant"] == "최우선"
        lab = r["name"].split()[1] if len(r["name"].split()) > 1 else r["name"][:6]
        dots.append(f'<circle cx="{X(r["reviews"]):.0f}" cy="{Y(r["undercut_pct"]):.0f}" r="{8 if hot else 5}" '
                    f'fill="{"#d23b3b" if hot else "#94a3b8"}" fill-opacity="0.85"/>'
                    f'<text x="{X(r["reviews"]):.0f}" y="{Y(r["undercut_pct"])-10:.0f}" font-size="9" '
                    f'text-anchor="middle" fill="#465569">{html.escape(lab)}</text>')
    qx, qy = X(rmed), Y(umed)
    trows = "".join(
        f"<tr class='{'hot' if r['quadrant']=='최우선' else ''}'><td>{html.escape(r['name'][:30])}"
        f"{'' if r['matched'] else ' <span class=mut>(다나와 매칭X)</span>'}</td>"
        f"<td class=num>{r['reviews']:,}</td><td class=num>{('★'+str(r['rating'])) if r['rating'] else '—'}</td>"
        f"<td class=num>−{r['undercut_pct']}%</td><td class=num>{r['lowest_unit']:,}원<br>"
        f"<span class=mut>{html.escape(r['lowest_mall'])}</span></td><td><span class=pill>{r['quadrant']}</span></td></tr>"
        for r in rows)
    open(os.path.join(OUT, "danawa_demand.html"), "w", encoding="utf-8").write(f"""<!doctype html>
<html lang=ko><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>수요×가격 (다나와 리뷰) — SCINIC</title><style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--red:#d23b3b;--amber:#b9770b;--card:#fff}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:860px;margin:0 auto;padding:18px 20px 56px}}h1{{font-size:17px;margin:0 0 2px}}
.sub{{color:var(--mut);font-size:12.5px;margin-bottom:14px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px 17px;margin-bottom:14px}}
.q{{font-size:10px}}table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.3px;font-weight:700;background:#fafbfe}}tr:last-child td{{border-bottom:none}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}tr.hot{{background:#fff5f5}}.mut{{color:var(--mut)}}
.pill{{font-size:10px;font-weight:800;padding:2px 7px;border-radius:999px;background:#eef1f7;color:#465569}}tr.hot .pill{{background:#fdecec;color:var(--red)}}
.foot{{color:var(--mut);font-size:11px;border-top:1px solid var(--line);padding-top:10px;margin-top:6px;line-height:1.6}}
</style></head><body><div class=wrap>
<h1>수요 × 가격 — SCINIC <span style="font-size:12px;color:var(--amber)">v2 · 다나와 리뷰수·평점</span></h1>
<div class=sub>수요 = <b>다나와 누적 리뷰수</b>(블로그 언급보다 정확한 구매 프록시) + 평점 · {today} · 실데이터</div>
<div class=card><h3 style="margin:0 0 4px;font-size:13px">📈 수요×가격 4분면</h3>
<div class=mut style="font-size:11px;margin-bottom:6px">오른쪽=리뷰수↑(로그) · 위=공식가 아래↑ · <b style="color:var(--red)">우상단=최우선</b>(잘 팔리는데 많이 깎임)</div>
<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px">
<line x1="{qx:.0f}" y1="{PAD}" x2="{qx:.0f}" y2="{H-PAD}" stroke="#cfd6e2" stroke-dasharray="3 3"/>
<line x1="{PAD}" y1="{qy:.0f}" x2="{W-PAD}" y2="{qy:.0f}" stroke="#cfd6e2" stroke-dasharray="3 3"/>
<text x="{W-PAD}" y="{PAD-6}" class=q text-anchor=end fill="#d23b3b">↑침해 ·리뷰→ = 최우선</text>
<text x="{PAD}" y="{H-PAD+18}" class=q fill="#697586">리뷰 적음</text><text x="{W-PAD}" y="{H-PAD+18}" class=q text-anchor=end fill="#697586">리뷰 많음(잘 팔림)</text>
{''.join(dots)}</svg></div>
<div class=card><h3 style="margin:0 0 8px;font-size:13px">📋 우선순위 (리뷰수 × 침해율)</h3>
<table><tr><th>제품</th><th>다나와 리뷰</th><th>평점</th><th>공식가 아래</th><th>최저(1개)</th><th>분면</th></tr>{trows}</table></div>
<p class=foot><b>정직성.</b> 수요 = 다나와 vssearch API의 <b>누적 리뷰수</b> = 판매량 프록시(시점 누적, 최근 속도 아님) + 평점(만족도).
가격은 1개당(네이버 기준). 다나와 제품 매칭은 토큰+사이즈 자동 매칭이라 일부 'X' 가능. 스냅샷 저장 → 시계열 비교.
다음 정밀화: 리뷰 '증가 속도'(최근 N일), 데이터랩 검색량, 마켓 랭킹.</p>
</div></body></html>""")


if __name__ == "__main__":
    main()
