#!/usr/bin/env python3
"""
'진짜 제품이라면' 화면 목업 — 화면 설계(임원/데이터 렌즈 수렴)를 실제 SCINIC 데이터로
시각화. 다화면 콘솔(대시보드 / 제품 가격 진실 / 셀러 정체성), 의존성 없는 HTML+SVG.

    python3 naver_dossier_v3.py     # outputs/naver_crossmarket_v3.json 필요
    python3 product_mockup.py       # → outputs/product_mockup.html
"""
import html
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
GATED = {"쿠팡"}


def won(n):
    return f"{n:,}"


def per_sku(p):
    ms = p["members"]
    official = p.get("official_unit")
    acc = [m for m in ms if m["mall"] not in GATED]
    acc_low = min((m["unit"] for m in acc), default=None)
    all_low = min(m["unit"] for m in ms)
    return official, acc_low, all_low


# ---------- SVG: per-unit dot-plot with MAP line ----------
def dotplot(p, w=640, h=132):
    ms = sorted(p["members"], key=lambda m: m["unit"])
    official = p.get("official_unit")
    vals = [m["unit"] for m in ms] + ([official] if official else [])
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.14 or 1000
    dmin, dmax = lo - pad, hi + pad
    L, R, axisY = 22, 22, 74
    plotw = w - L - R

    def X(v):
        return L + (v - dmin) / (dmax - dmin) * plotw

    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px">']
    # undercut zone (left of MAP)
    if official:
        parts.append(f'<rect x="{L}" y="20" width="{X(official)-L:.0f}" height="76" fill="#fdecec"/>')
        parts.append(f'<line x1="{X(official):.0f}" y1="14" x2="{X(official):.0f}" y2="100" stroke="#d23b3b" stroke-width="2"/>')
        parts.append(f'<text x="{X(official):.0f}" y="12" fill="#d23b3b" font-size="10" font-weight="700" text-anchor="middle">우리 공식가 {won(official)}원</text>')
    parts.append(f'<line x1="{L}" y1="{axisY}" x2="{w-R}" y2="{axisY}" stroke="#cfd6e2" stroke-width="1"/>')
    lowest = ms[0]
    for i, m in enumerate(ms):
        cx = X(m["unit"])
        cy = axisY - 16 + (i % 3) * 16
        is_low = m is lowest
        is_off = m["official"]
        r = 8 if is_low else (6 if is_off else 5)
        fill = "#d23b3b" if is_low else ("#2d6cdf" if is_off else "#94a3b8")
        dash = ' stroke-dasharray="2 2" stroke="#697586" stroke-width="1.5"' if m["mall"] in GATED else ""
        parts.append(f'<circle cx="{cx:.0f}" cy="{cy}" r="{r}" fill="{fill}" fill-opacity="0.9"{dash}/>')
    # labels for lowest & axis ends
    parts.append(f'<text x="{X(lowest["unit"]):.0f}" y="{axisY-16+(0%3)*16-12}" fill="#d23b3b" font-size="10" font-weight="800" text-anchor="middle">제일 쌈 {won(lowest["unit"])}</text>')
    parts.append(f'<text x="{L}" y="{axisY+22}" fill="#94a3b8" font-size="9">{won(int(dmin))}원(1개)</text>')
    parts.append(f'<text x="{w-R}" y="{axisY+22}" fill="#94a3b8" font-size="9" text-anchor="end">{won(int(dmax))}원(1개)</text>')
    parts.append('</svg>')
    return "".join(parts)


def hbar(label, value, maxv, color, sub=""):
    pct = (value / maxv * 100) if maxv else 0
    return (f'<div class=barrow><span class=bl>{html.escape(label)}</span>'
            f'<span class=bt><span class=bf style="width:{pct:.0f}%;background:{color}"></span></span>'
            f'<span class=bv>{sub}</span></div>')


def build():
    data = json.load(open(os.path.join(OUT, "naver_crossmarket_v3.json"), encoding="utf-8"))
    prods = [p for p in data["products"] if p.get("official_unit")]
    # exposure floor (accessible) vs ceiling (incl. coupang)
    floor = ceil = 0
    for p in prods:
        off, acc_low, all_low = per_sku(p)
        if acc_low and acc_low < off:
            floor += off - acc_low
        if all_low < off:
            ceil += off - all_low
    undercut = [p for p in prods if p["undercut_pct"] > 0]
    coup = [p for p in data["products"] if p.get("has_coupang")]
    # SELLER-NAME footprint (셀러명 기준 — 네이버 API가 주는 mallName). 사업자번호 미사용.
    sellers = {}
    for p in prods:
        off = p.get("official_unit")
        for m in p["members"]:
            s = sellers.setdefault(m["mall"], {"name": m["mall"], "skus": set(), "listings": 0,
                                               "disc": [], "official": m["official"], "gray": False})
            s["skus"].add(p["name"])
            s["listings"] += 1
            if off and m["unit"] < off:
                s["disc"].append((off - m["unit"]) / off)
            if any(k in m["title"] for k in ("병행수입", "면세")):
                s["gray"] = True
    seller_list = []
    for s in sellers.values():
        s["n_sku"] = len(s["skus"])
        s["avg_disc"] = (sum(s["disc"]) / len(s["disc"]) * 100) if s["disc"] else 0
        s["sku_list"] = sorted(s["skus"])
        seller_list.append(s)
    # offenders = non-official sellers undercutting official, ranked by catalog reach then depth
    offenders = sorted([s for s in seller_list if not s["official"] and s["disc"]],
                       key=lambda s: (-s["n_sku"], -s["avg_disc"]))[:10]
    hero = max(prods, key=lambda p: p["n_listings"])
    return data, prods, undercut, coup, floor, ceil, offenders, seller_list, hero


# ------------------------- render -------------------------
def render(data, prods, undercut, coup, floor, ceil, offenders, seller_list, hero):
    n_mall = len({m["mall"] for p in data["products"] for m in p["members"]})
    # dashboard: portfolio triage mini range bars (top by undercut)
    triage = sorted(undercut, key=lambda p: -p["undercut_pct"])[:8]
    maxoff = max((o["n_sku"] for o in offenders), default=1)
    off_bars = "".join(hbar(f"{o['name']}", o["n_sku"], maxoff, "#d23b3b",
                            f"제품 {o['n_sku']}개 · 평균 {o['avg_disc']:.0f}% 싸게"
                            + (" · 병행수입" if o["gray"] else "")) for o in offenders)
    triage_rows = "".join(
        f"<tr><td>{html.escape(p['name'][:30])}</td><td class=num>{won(p['official_unit'])}원</td>"
        f"<td class=num>{won(p['lowest_unit'])}원 <span class=mut>{html.escape(p['lowest_mall'])}</span></td>"
        f"<td><span class=ubar><span style='width:{min(100,p['undercut_pct']*2):.0f}%'></span></span> "
        f"<b class=red>{p['undercut_pct']}% 싸게</b></td></tr>" for p in triage)

    hero_off, hero_acc, hero_all = per_sku(hero)
    hero_low = min(hero["members"], key=lambda m: m["unit"])
    member_rows = "".join(
        f"<tr class='{'off' if m['official'] else ''}{' low' if m is hero_low else ''}'>"
        f"<td>{html.escape(m['mall'])}{' <span class=pg>공식</span>' if m['official'] else ''}"
        f"{' <span class=pgate>쿠팡*</span>' if m['mall'] in GATED else ''}</td>"
        f"<td class=num>{won(m['price'])}원</td><td class=num>×{m['qty']}</td>"
        f"<td class=num><b>{won(m['unit'])}</b></td></tr>"
        for m in sorted(hero["members"], key=lambda m: m["unit"])[:8])

    # SKU dot-plot cards (price-truth screen)
    truth_cards = "".join(
        f"""<div class=card><h4>{html.escape(p['name'][:38])} <span class=mut>· {p.get('size') or ''} · 파는 곳 {p['n_malls']}곳
        {'· 쿠팡*' if p.get('has_coupang') else ''}</span></h4>
        {dotplot(p)}
        <div class=legend>공식가보다 {p['undercut_pct']}% 쌈 · 제일 쌈 {won(p['lowest_unit'])}원({html.escape(p['lowest_mall'])})
        <span class=dot style='background:#2d6cdf'></span>우리 공식 <span class=dot style='background:#94a3b8'></span>다른 판매처
        <span class=dot style='background:#d23b3b'></span>제일 쌈 <span class=dot dash></span>쿠팡(아직 연결 안 됨)</div></div>"""
        for p in sorted(undercut, key=lambda p: -p["undercut_pct"])[:6])

    # ----- 셀러명 기준 행적 (사업자번호 미사용) -----
    sell_sorted = sorted([s for s in seller_list if not s["official"]],
                         key=lambda s: (-s["n_sku"], -s["avg_disc"]))
    hs = sell_sorted[0] if sell_sorted else None
    maxsku = max((s["n_sku"] for s in sell_sorted), default=1)
    seller_bars = "".join(hbar(
        s["name"], s["n_sku"], maxsku, "#d23b3b" if s["avg_disc"] > 0 else "#94a3b8",
        f"제품 {s['n_sku']}개 · 평균 {s['avg_disc']:.0f}% 싸게" + (" · 병행수입" if s["gray"] else ""))
        for s in sell_sorted[:14])
    hs_chips = "".join(f"<span class=chip>{html.escape(x[:22])}</span>" for x in hs["sku_list"][:8]) if hs else ""
    off_names = ", ".join(s["name"] for s in seller_list if s["official"]) or "—"
    queue_rows = "".join(
        f"<tr><td><b>{html.escape(s['name'])}</b>{' <span class=pgate style=background:#fff6e6;color:#b9770b>병행수입</span>' if s['gray'] else ''}</td>"
        f"<td>내 제품 {s['n_sku']}개</td><td class=red>평균 {s['avg_disc']:.0f}% 싸게</td>"
        f"<td><button class=act>경고</button><button class='act g'>신고</button><button class='act g'>증거</button></td></tr>"
        for s in sell_sorted[:6])

    # 리뷰·평점·속도 (velocity.json 있으면)
    vrows = []
    vp = os.path.join(OUT, "velocity.json")
    if os.path.exists(vp):
        vrows = json.load(open(vp, encoding="utf-8")).get("rows", [])
    def _f(r):
        try:
            return float(r)
        except (TypeError, ValueError):
            return 0.0
    def _sc(r):
        r = _f(r)
        return "#1a9d57" if r >= 4.7 else ("#b9770b" if r >= 4.4 else "#697586")
    vtable = "".join(
        f"<tr class='{'hot' if i < 3 else ''}'><td>{html.escape(v['name'][:30])}"
        + (" <span class=pg2>지금 활발</span>" if (v.get('recent_ratio') or 0) >= 0.5 else "")
        + f"</td><td class=num style='color:{_sc(v.get('rating'))};font-weight:800'>★{v.get('rating') if v.get('rating') else '—'}</td>"
        f"<td class=num>{v['reviews']:,}</td>"
        f"<td><span class=ubar><span style='width:{int((v.get('recent_ratio') or 0)*100)}%;background:#1a9d57'></span></span> {int((v.get('recent_ratio') or 0)*100)}%</td>"
        f"<td class=num>{(str(v['velocity_per_day'])+'/일') if v.get('velocity_per_day') is not None else '<span class=mut>측정중</span>'}</td>"
        f"<td class=num style='font-size:11px'><b style='color:#1a9d57'>블로그 +{v.get('blog_recent',0)}</b>"
        f"<span class=mut> /30일 (누적 {v.get('blog',0):,})</span>"
        + (f" · 영상 {v['yt']}" if v.get('yt') is not None else "") + "</td>"
        f"<td class=num>−{v['undercut_pct']}%</td></tr>" for i, v in enumerate(vrows))
    _rates = [_f(v.get('rating')) for v in vrows if _f(v.get('rating')) > 0]
    vavg = round(sum(_rates) / len(_rates), 1) if _rates else 0

    # 수요 × 가격 4분면 (실데이터: 누적 리뷰 vs 공식가 아래%)
    vquad = ""
    rv = [v for v in vrows if v.get('reviews')]
    if rv:
        rmax = max(v['reviews'] for v in rv) or 1
        umax = max(v['undercut_pct'] for v in rv) or 1
        rmed = sorted(v['reviews'] for v in rv)[len(rv) // 2]
        umed = sorted(v['undercut_pct'] for v in rv)[len(rv) // 2]
        Wq, Hq, Pq = 700, 300, 44
        def Xq(d): return Pq + (math.log10(max(d, 1)) / math.log10(max(rmax, 10))) * (Wq - 2 * Pq)
        def Yq(u): return Hq - Pq - (u / umax) * (Hq - 2 * Pq)
        dts = []
        for v in rv:
            hot = v['reviews'] >= rmed and v['undercut_pct'] >= umed
            lab = v['name'].split()[1] if len(v['name'].split()) > 1 else v['name'][:6]
            dts.append(f'<circle cx="{Xq(v["reviews"]):.0f}" cy="{Yq(v["undercut_pct"]):.0f}" r="{8 if hot else 5}" '
                       f'fill="{"#d23b3b" if hot else "#94a3b8"}" fill-opacity="0.85"/>'
                       f'<text x="{Xq(v["reviews"]):.0f}" y="{Yq(v["undercut_pct"])-9:.0f}" font-size="9" '
                       f'text-anchor="middle" fill="#465569">{html.escape(lab)}</text>')
        vquad = (f'<div class=card><h3 style="margin:0 0 4px;font-size:13px">📈 수요 × 가격 4분면 (실데이터)</h3>'
                 f'<div class=mut style="font-size:11px;margin-bottom:6px">오른쪽=리뷰 많음(잘 팔림) · 위=공식가 아래↑ · '
                 f'<b style="color:var(--red)">우상단=최우선</b>(잘 팔리는데 많이 깎임)</div>'
                 f'<svg viewBox="0 0 {Wq} {Hq}" width="100%" style="max-width:{Wq}px">'
                 f'<line x1="{Xq(rmed):.0f}" y1="{Pq}" x2="{Xq(rmed):.0f}" y2="{Hq-Pq}" stroke="#cfd6e2" stroke-dasharray="3 3"/>'
                 f'<line x1="{Pq}" y1="{Yq(umed):.0f}" x2="{Wq-Pq}" y2="{Yq(umed):.0f}" stroke="#cfd6e2" stroke-dasharray="3 3"/>'
                 f'<text x="{Pq}" y="{Hq-Pq+16}" font-size="10" fill="#94a3b8">리뷰 적음</text>'
                 f'<text x="{Wq-Pq}" y="{Hq-Pq+16}" font-size="10" fill="#94a3b8" text-anchor="end">리뷰 많음</text>'
                 f'{"".join(dts)}</svg></div>')

    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>내 상품 가격 지킴이 — 화면 예시</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#eef1f6;--brand:#2d6cdf;--brand-d:#1e4fa8;--red:#d23b3b;--red-bg:#fdecec;--green:#1a9d57;--green-bg:#e6f7ee;--amber:#b9770b;--amber-bg:#fff6e6;--violet:#6b4fc0;--card:#fff;--nav:#101726;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5;font-size:13.5px}}
.app{{display:flex;min-height:100vh}}
.nav{{width:194px;background:var(--nav);color:#cfd8ea;padding:16px 12px;flex:none}}
.nav .brand{{font-weight:900;color:#fff;font-size:15px;padding:4px 8px 14px;letter-spacing:.3px}}
.nav .brand span{{color:#6b87c9;font-weight:600;font-size:11px;display:block;margin-top:2px}}
.nav a{{display:block;padding:9px 11px;border-radius:8px;color:#aab8d4;cursor:pointer;font-weight:600;font-size:13px;margin-bottom:2px}}
.nav a.on{{background:#22304d;color:#fff}}
.nav a small{{display:block;color:#6b87c9;font-weight:500;font-size:10.5px}}
.main{{flex:1;min-width:0;padding:20px 24px;max-width:1000px}}
.demo{{background:var(--amber-bg);color:var(--amber);font-weight:800;font-size:10.5px;padding:4px 9px;border-radius:999px}}
h2{{font-size:17px;margin:0 0 3px}}h4{{font-size:13px;margin:0 0 6px}}
.hd{{display:flex;align-items:center;gap:10px;margin-bottom:14px}}.hd .sub{{color:var(--mut);font-size:12px}}
.page{{display:none}}.page.on{{display:block;animation:f .2s}}@keyframes f{{from{{opacity:0}}to{{opacity:1}}}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 14px}}@media(max-width:760px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 13px}}.tile .b{{font-size:20px;font-weight:800}}.tile .l{{font-size:11px;color:var(--mut);margin-top:2px}}.tile.warn .b{{color:var(--red)}}
.row2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:820px){{.row2{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px;margin-bottom:12px}}
.card h3{{font-size:13px;margin:0 0 10px}}
.expo{{display:flex;height:30px;border-radius:8px;overflow:hidden;margin:8px 0;font-size:11px;font-weight:800;color:#fff}}
.expo .f{{background:var(--red);display:flex;align-items:center;justify-content:center}}
.expo .c{{background:repeating-linear-gradient(45deg,#e88,#e88 5px,#f3b5b5 5px,#f3b5b5 10px);display:flex;align-items:center;justify-content:center;color:#7a1f1f}}
.barrow{{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12px}}.bl{{width:90px;flex:none;color:var(--ink);font-weight:600;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bt{{flex:1;background:#eef1f7;border-radius:5px;height:14px;overflow:hidden}}.bf{{display:block;height:100%}}.bv{{width:130px;flex:none;color:var(--mut);font-size:11px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line)}}th{{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.3px;font-weight:700;background:#fafbfe}}tr:last-child td{{border-bottom:none}}td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
tr.off{{background:#f0f6ff}}tr.low{{background:#fff5f5}}
.red{{color:var(--red)}}.mut{{color:var(--mut)}}.pg{{font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;background:var(--green-bg);color:var(--green)}}.pgate{{font-size:9px;font-weight:800;padding:1px 5px;border-radius:999px;background:#eef1f7;color:#697586}}
.ubar{{display:inline-block;width:60px;height:7px;background:#eef1f7;border-radius:4px;vertical-align:middle;overflow:hidden}}.ubar span{{display:block;height:100%;background:var(--red)}}
.legend{{font-size:10.5px;color:var(--mut);margin-top:6px}}.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:middle;margin:0 3px 0 8px}}.dot.dash{{background:#fff;border:1.5px dashed #697586}}
.note{{background:var(--amber-bg);border:1px solid #ffd591;border-radius:10px;padding:9px 12px;font-size:11.5px;margin-bottom:12px}}.note b{{color:var(--amber)}}
.flow{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:6px 0}}
.node{{background:var(--red);color:#fff;border-radius:10px;padding:8px 13px;font-weight:800;text-align:center;line-height:1.3}}.node small{{font-weight:600;opacity:.9;font-size:10px}}
.alias{{background:#fff;border:1px solid #e7b9b9;border-radius:9px;padding:6px 11px;font-weight:700;font-size:12px}}
.mk{{background:#eef1f7;border-radius:7px;padding:5px 10px;font-weight:700;font-size:11.5px}}
.arr{{color:var(--red);font-weight:800;font-size:18px}}
.col{{display:flex;flex-direction:column;gap:6px}}
.chip{{display:inline-block;background:#eef1f7;color:#465569;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:6px;margin:2px 3px 0 0}}
.pg2{{font-size:9px;font-weight:800;padding:1px 6px;border-radius:999px;background:#e6f7ee;color:#1a9d57}}
.act{{background:var(--red);color:#fff;border:none;border-radius:8px;padding:6px 11px;font-size:12px;font-weight:700;cursor:pointer;margin-right:5px;font-family:inherit}}.act.g{{background:#fff;color:var(--ink);border:1px solid var(--line)}}
.foot{{color:var(--mut);font-size:11px;margin-top:16px;border-top:1px solid var(--line);padding-top:10px;line-height:1.6}}
</style></head><body>
<div class=app>
  <nav class=nav>
    <div class=brand>🛡 내 상품 가격 지킴이<span>화면 예시</span></div>
    <a class=on onclick="go('p1',this)">한눈에 보기<small>지금 상황 요약</small></a>
    <a onclick="go('p2',this)">제품별 가격 비교<small>어디가 얼마에 파나</small></a>
    <a onclick="go('p3',this)">판매처별 현황<small>누가 싸게 파나</small></a>
    <a onclick="go('p4',this)">처리할 일<small>경고·신고</small></a>
    <a onclick="go('p5',this)">리뷰·평점<small>잘 팔리나 · 만족도</small></a>
  </nav>
  <main class=main>

  <!-- P1 DASHBOARD -->
  <section class="page on" id=p1>
    <div class=hd><h2>한눈에 보기</h2><span class=demo>실제 데이터 · 네이버 쇼핑</span></div>
    <div class=kpis>
      <div class=tile warn><div class=b>{len(undercut)}</div><div class=l>공식가보다 싸게 팔리는 제품</div></div>
      <div class=tile><div class=b>{len(prods)}</div><div class=l>지켜보는 제품</div></div>
      <div class=tile><div class=b>{n_mall}</div><div class=l>파는 곳(쇼핑몰)</div></div>
      <div class=tile warn><div class=b>{len(coup)}</div><div class=l>쿠팡에도 있는 제품</div></div>
    </div>
    <div class=card>
      <h3>💰 공식가보다 싸게 팔려 새는 돈 (제품 1개 기준·어림치)</h3>
      <div class=expo><div class=f style="flex:{floor}">지금 확인되는 손실 {won(floor)}원</div>
        <div class=c style="flex:{max(1,ceil-floor)}">+쿠팡 {won(ceil-floor)}</div></div>
      <div class=mut style="font-size:11px">■ 네이버+11번가 = <b>지금 바로 확인되는 손실</b> · ▨ 쿠팡은 아직 연결 안 돼 더한 어림치. 단순 가격앱은 못 보여주는 '얼마 새는지' 관점.</div>
    </div>
    <div class=row2>
      <div class=card><h3>🎯 내 상품을 가장 많이 싸게 파는 판매처</h3>{off_bars}
        <div class=mut style="font-size:10.5px;margin-top:6px">쇼핑몰에 표시된 '판매처 이름'으로 묶음 → <b>판매처별 현황</b>에서 자세히.</div></div>
      <div class=card><h3>📋 가장 많이 깎인 제품 순위</h3>
        <table><tr><th>제품</th><th>공식가</th><th>제일 싼 값(1개)</th><th>얼마나 싸게</th></tr>{triage_rows}</table></div>
    </div>
  </section>

  <!-- P2 PRICE TRUTH -->
  <section class=page id=p2>
    <div class=hd><h2>제품별 가격 비교</h2><span class=sub>이 제품이 쇼핑몰마다 1개당 얼마에 팔리나</span></div>
    <div class=card>
      <h3>{html.escape(hero['name'][:44])} <span class=mut>· {hero.get('size') or ''} · 파는 곳 {hero['n_malls']}곳 · 판매글 {hero['n_listings']}개 → 같은 제품 1개로 묶음</span></h3>
      {dotplot(hero, w=820, h=150)}
      <div class=legend><span class=dot style='background:#2d6cdf'></span>우리 공식 <span class=dot style='background:#94a3b8'></span>다른 판매처
        <span class=dot style='background:#d23b3b'></span>제일 쌈 <span class=dot dash></span>쿠팡(아직 연결 안 됨, 점선)</div>
      <div class=row2 style="margin-top:12px">
        <div><h4>묶음 풀어보기 (표시 가격 → 1개당 가격)</h4>
          <div class=mut style="font-size:11.5px;margin-bottom:6px">가장 싼 판매글: {html.escape(hero_low['mall'])} {won(hero_low['price'])}원을 {hero_low['qty']}개로 나누면 = <b class=red>1개당 {won(hero_low['unit'])}원</b>
          → 우리 공식가 {won(hero_off)}원보다 <b class=red>{hero['undercut_pct']}% 쌈</b>. <br>'몇 개 묶음'으로 싸 보이게 하는 걸 1개당 값으로 환산해 드러냅니다.</div></div>
        <div><h4>쇼핑몰별 1개당 가격</h4>
          <table><tr><th>파는 곳</th><th>표시가</th><th>묶음수</th><th>1개당</th></tr>{member_rows}</table></div>
      </div>
    </div>
    <h4 style="margin:14px 0 8px">다른 제품들</h4>
    <div class=row2>{truth_cards}</div>
  </section>

  <!-- P3 SELLER FOOTPRINT (셀러명 기준) -->
  <section class=page id=p3>
    <div class=hd><h2>판매처별 현황</h2><span class=sub>이 판매처가 내 상품을 뭘·얼마에 파는지</span></div>
    <div class=note>판매처는 쇼핑몰에 표시된 <b>'판매처 이름'</b>으로 구분합니다. 우리 공식: {off_names}.
      <span class=mut>(한 판매처가 이름을 여러 개 쓰는 경우 묶기는 추후 보완)</span></div>
    <div class=card>
      <h3>🕵️ 내 상품을 가장 많이 싸게 파는 판매처 — {html.escape(hs['name']) if hs else '—'}</h3>
      <div class=flow>
        <div class=node style="background:var(--red)">{html.escape(hs['name']) if hs else '—'}<br>
          <small>내 제품 {hs['n_sku'] if hs else 0}개를 평균 {hs['avg_disc']:.0f}% 싸게 · 판매글 {hs['listings'] if hs else 0}개{' · 병행수입' if hs and hs['gray'] else ''}</small></div>
        <span class=arr>→</span>
        <div style="flex:1;min-width:200px"><div class=mut style="font-size:11px;margin-bottom:4px">이 판매처가 싸게 파는 내 제품:</div>{hs_chips}</div>
      </div>
      <div style="margin-top:12px"><b style="font-size:12px">처리:</b>
        <button class=act>⚠ 경고 보내기</button><button class="act g">🚩 쇼핑몰에 신고</button><button class="act g">⬇ 증거 모아 받기</button></div>
      <div class=mut style="font-size:11px;margin-top:8px">단순 가격앱은 "이 판매처가 싸다"까지만. 여기는 <b>그 판매처가 내 상품 전체에서 몇 개를, 얼마나 싸게</b> 파는지 한 판매처로 묶어 보여줍니다.</div>
    </div>
    <div class=card><h3>📋 내 상품을 싸게 파는 판매처 순위</h3>{seller_bars}
      <div class=mut style="font-size:10.5px;margin-top:6px">막대 = 이 판매처가 싸게 파는 내 제품 개수. 네이버 가격비교 기준.</div></div>
  </section>

  <!-- P4 ACTION QUEUE -->
  <section class=page id=p4>
    <div class=hd><h2>처리할 일</h2><span class=sub>찾아내는 데서 끝내지 않고 바로 처리까지</span></div>
    <div class=card>
      <table><tr><th>판매처</th><th>싸게 파는 내 제품</th><th>평균 얼마나 싸게</th><th>처리</th></tr>{queue_rows}</table>
      <div class=mut style="font-size:11px;margin-top:8px">위 목록은 실데이터(네이버 쇼핑 판매처) 기준. 조치 버튼은 화면 예시(실제 발송·기록은 정식 버전).</div>
    </div>
  </section>

  <!-- P5 REVIEW / RATING -->
  <section class=page id=p5>
    <div class=hd><h2>리뷰·평점</h2><span class=sub>잘 팔리나(리뷰) · 지금 활발한가 · 만족도(평점) — 다나와 실데이터</span></div>
    <div class=kpis>
      <div class=tile><div class=b>★{vavg}</div><div class=l>평균 평점</div></div>
      <div class=tile><div class=b>{len(vrows)}</div><div class=l>리뷰 매칭 제품</div></div>
      <div class=tile><div class=b>{sum(1 for v in vrows if (v.get('recent_ratio') or 0)>=0.5)}</div><div class=l>지금 활발한 제품</div></div>
      <div class=tile><div class=b>{max((v['reviews'] for v in vrows), default=0):,}</div><div class=l>최다 리뷰</div></div>
    </div>
    {vquad}
    <div class=card>
      <h3 style="margin:0 0 8px;font-size:13px">📦 무엇부터 — 리뷰·평점·속도</h3>
      <table><tr><th>제품</th><th>평점</th><th>누적 리뷰</th><th>최근 활발도</th><th>리뷰 증가</th><th>입소문 증가(최근 30일↑)</th><th>공식가 아래</th></tr>{vtable}</table>
      <div class=mut style="font-size:10.5px;margin-top:8px">
        <b>누적 리뷰</b>=지금까지 얼마나 팔렸나(산 사람 후기) · <b>리뷰 증가</b>=매일 모으면 '하루 +N개'로 실측 ·
        <b>입소문 증가</b>=<b style="color:#1a9d57">블로그 최근 30일 새 글 수</b>(요즘 얼마나 떠드나=관심 모멘텀. 누적과 다름!). 카페는 날짜 데이터 미제공, 영상=유튜브 최근(쿼터 제한) ·
        <b>평점</b>=만족도(★). (예: 파데스킵 선크림 블로그 +15/30일=뜨는 중 vs 퍼스트 에센스 누적 1,168인데 +2=식는 중)</div>
    </div>
  </section>

    <p class=foot><b>화면 예시 / 솔직하게.</b> 전부 <b>네이버 쇼핑 실제 데이터</b>이고, 가격은 모두 '1개당'으로 환산했습니다.
      판매처는 쇼핑몰에 표시된 이름으로 구분합니다. '새는 돈'은 제품 1개 기준 어림치예요.
      쿠팡*은 네이버 가격비교로 일부만 보여서 '아직 연결 안 됨'으로 표시. 처리 흐름·기록은 정식 버전 기능. 그래프는 추가 프로그램 없이 그렸습니다.</p>
  </main>
</div>
<script>function go(id,el){{document.querySelectorAll('.page').forEach(p=>p.classList.remove('on'));document.getElementById(id).classList.add('on');document.querySelectorAll('.nav a').forEach(a=>a.classList.remove('on'));el.classList.add('on');window.scrollTo(0,0);}}</script>
</body></html>"""


def main():
    data, prods, undercut, coup, floor, ceil, offenders, seller_list, hero = build()
    with open(os.path.join(OUT, "product_mockup.html"), "w", encoding="utf-8") as f:
        f.write(render(data, prods, undercut, coup, floor, ceil, offenders, seller_list, hero))
    print(f"화면 목업 생성 · SKU {len(prods)} · 침해 {len(undercut)} · 익스포저 floor {won(floor)} / ceil {won(ceil)}")
    print(f"히어로: {hero['name'][:40]} ({hero['n_malls']}몰)")
    print("outputs/product_mockup.html")


if __name__ == "__main__":
    main()
