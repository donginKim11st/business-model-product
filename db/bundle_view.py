#!/usr/bin/env python3
"""번들 결합 화면 — 정성 인사이트(대표) + product-identity-graph(가격사다리/판매처)를 한 노드에.

상품 정체성 그래프(같은 상품을 크로스마켓으로 묶은 엔티티)와 비정형 인사이트가 MongoDB 한 번들 아래
수렴한다. 이 화면은 그 둘을 합쳐 보여준다:
  · 좌  대표 인사이트       : representative_view.customer_view (고객 친화) + 셀러 상세
  · 우  하위 카탈로그(SKU)  : 번들 아래 변형(SKU) 목록 + SKU별 크로스몰 가격사다리(price_summary)
                              + 최저가 판매처(offers: mall·platform·price·중고/새)
  → 고객은 "이 상품 이런 점이 좋고, 최저가 ₩X (N개 몰)"을, 셀러는 "속성 포지셔닝 + 몰별 가격경쟁"을 한눈에.

  MONGO_URI=... python3 db/bundle_view.py --html data/bundle_view.html [--bundle NK_AF1_BLACK] [--skus 6]
"""
import os
import sys
import html
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import representative_view as rv
import consumer_guide as cg          # 소비자 정직 가이드 데이터(패키지별) 재사용
import seller_dashboard as sd        # 셀러 인텔리전스 데이터(패키지별) 재사용
from pymongo import MongoClient


def _won(n):
    return f"₩{int(n):,}" if isinstance(n, (int, float)) else "—"


def _num(n):
    if not isinstance(n, (int, float)):
        return "—"
    return f"{n/10000:.1f}만" if n >= 10000 else f"{int(n):,}"


def _buzz_html(buzz):
    """제품 언급량(실제 API 카운트). 네이버=정확 · 유튜브=YouTube 추정치."""
    if not buzz:
        return ""
    nb, ns = buzz.get("naver_blog"), buzz.get("naver_shop")
    yt = buzz.get("youtube"); yst = buzz.get("youtube_status")
    parts = []
    if nb is not None:
        parts.append(f"📝 블로그 <b>{_num(nb)}</b>")
    if ns is not None:
        parts.append(f"🛒 쇼핑 <b>{_num(ns)}</b>")
    if yt is not None:
        parts.append(f"🎬 유튜브 <b>~{_num(yt)}</b>")
    elif yst == "pending":
        parts.append("🎬 유튜브 <span class=pend>수집예정</span>")
    if not parts:
        return ""
    return ("<div class=buzz>📣 현재 언급량 " + " · ".join(parts)
            + " <span class=buzznote>네이버=실측 · 유튜브=추정</span></div>")


def _esc(s):
    return html.escape(str(s or ""))


def identity_panel(db, base, max_skus=12, offers_per_sku=3):
    """번들 하위 카탈로그 패널. 두 모델 지원:
      catalog 모드(식품 pd_ctlg): 패키지 product.catalogs = [{ctlg_no, disp(풀네임), size, count, has_insight}].
      price 모드(신발 identity-graph): 변형(SKU) product + 크로스몰 가격사다리(price_summary)/판매처(offers)."""
    bundle_uid = base["_id"]
    cats = base.get("catalogs")
    if cats:                              # ── 식품: 우리 DB의 패키지 & 카탈로그(ctlg_no)(+가격 있으면 사다리) ──
        out, all_min, all_max, malls = [], [], [], set()
        n_priced = 0
        for c in cats[:max_skus]:
            ps = c.get("price_summary") or {}
            offers, trend = [], []
            if ps.get("min"):
                n_priced += 1
                all_min.append(ps["min"]); all_max.append(ps["max"])
                cuid = str(c.get("ctlg_no"))
                offers = list(db.offers.find(
                    {"product_uid": cuid}, {"mall": 1, "platform": 1, "price": 1, "used": 1, "url": 1}
                ).sort("price", 1).limit(offers_per_sku))
                for o in db.offers.find({"product_uid": cuid}, {"mall": 1}):
                    if o.get("mall"):
                        malls.add(o["mall"])
                trend = [(t["date"], t["min"]) for t in db.price_history.find(
                    {"ctlg_no": c.get("ctlg_no")}, {"date": 1, "min": 1}).sort("date", 1)]
            out.append(dict(c, offers=offers, trend=trend))
        n_ins = sum(1 for c in cats if (c.get("insight") or {}).get("dims"))
        agg = {"n_catalogs": len(cats), "n_with_insight": n_ins, "n_priced": n_priced,
               "min": min(all_min) if all_min else None, "max": max(all_max) if all_max else None,
               "n_malls": len(malls)}
        return {"mode": "catalog", "agg": agg, "catalogs": out,
                "n_more": max(0, len(cats) - max_skus)}

    kids = list(db.products.find(        # ── 신발: 변형 SKU + 가격 ──
        {"parent_uid": bundle_uid, "type": "variant"},
        {"_id": 1, "variant_value": 1, "style_code": 1, "price_summary": 1, "flags": 1}))
    # 가격 보유 SKU 우선, 몰 수 많은 순
    kids.sort(key=lambda k: -((k.get("price_summary") or {}).get("n_malls") or 0))
    skus = []
    all_min, all_max, malls = [], [], set()
    for k in kids[:max_skus]:
        ps = k.get("price_summary") or {}
        offers = list(db.offers.find(
            {"product_uid": k["_id"]},
            {"mall": 1, "platform": 1, "price": 1, "used": 1, "url": 1}).sort("price", 1).limit(offers_per_sku))
        if ps.get("min"):
            all_min.append(ps["min"])
        if ps.get("max"):
            all_max.append(ps["max"])
        for o in db.offers.find({"product_uid": k["_id"]}, {"mall": 1}):
            if o.get("mall"):
                malls.add(o["mall"])
        skus.append({"sku": k.get("style_code") or k.get("variant_value") or k["_id"].split("::")[-1],
                     "uid": k["_id"], "ps": ps, "multi_mall": (k.get("flags") or {}).get("multi_mall"),
                     "offers": offers})
    agg = {"n_skus_total": len(kids), "n_skus_shown": len(skus),
           "min": min(all_min) if all_min else None, "max": max(all_max) if all_max else None,
           "n_malls": len(malls)}
    return {"mode": "price", "agg": agg, "skus": skus}


def _ladder_bar(ps, agg):
    """SKU min~max 를 전체 번들 범위 위에 막대로(상대 위치)."""
    lo, hi = agg.get("min"), agg.get("max")
    if not (lo and hi and hi > lo and ps.get("min") and ps.get("max")):
        return ""
    span = hi - lo
    a = max(0, (ps["min"] - lo) / span * 100)
    b = min(100, (ps["max"] - lo) / span * 100)
    med = None
    if ps.get("median"):
        med = min(100, max(0, (ps["median"] - lo) / span * 100))
    medmark = f"<span class=med style='left:{med:.1f}%'></span>" if med is not None else ""
    return (f"<div class=ladder><span class=bar style='left:{a:.1f}%;width:{max(b-a,1.5):.1f}%'></span>{medmark}</div>")


def _sparkline(trend, w=120, h=24):
    """가격 추이 스파크라인(min 시계열) + 변동%. 가격 하락=초록(고객 이득)/상승=빨강."""
    if not trend or len(trend) < 2:
        return ""
    vals = [v for _, v in trend if v]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    n = len(vals)
    pts = " ".join(f"{i/(n-1)*w:.1f},{h-2-(v-lo)/span*(h-4):.1f}" for i, v in enumerate(vals))
    first, last = vals[0], vals[-1]
    chg = (last - first) / first * 100 if first else 0
    color = "#34d399" if chg < -0.5 else ("#f87171" if chg > 0.5 else "#9aa3af")
    arrow = "▼" if chg < -0.5 else ("▲" if chg > 0.5 else "→")
    badge = f"<span class=chg style='color:{color}'>{arrow}{abs(chg):.0f}% <span class=days>{n}일</span></span>"
    return (f"<span class=spark><svg width={w} height={h} viewBox='0 0 {w} {h}' preserveAspectRatio=none>"
            f"<polyline fill=none stroke='{color}' stroke-width=1.5 points='{pts}'/></svg>{badge}</span>")


def _catalog_panel_html(panel):
    """식품: 패키지 하위 카탈로그(ctlg_no/disp) 목록 + (가격 있으면) 크로스몰 가격사다리."""
    agg = panel["agg"]
    rows = []
    for c in panel["catalogs"]:
        ins = "<span class=ins>인사이트</span>" if (c.get("insight") or {}).get("dims") else ""
        code = f"<span class=code>{_esc(c.get('ctlg_no'))}</span>" if c.get("ctlg_no") else ""
        sub = " · ".join(x for x in [c.get("size"), c.get("count")] if x)
        ps = c.get("price_summary") or {}
        price_block = ""
        if ps.get("min"):
            offs = "".join(
                f"<div class=off><a href='{_esc(o.get('url'))}' target=_blank>{_esc(o.get('mall'))}</a>"
                f"<span class=tag>{'중고' if o.get('used') else '새'}</span><b>{_won(o.get('price'))}</b></div>"
                for o in c.get("offers") or [])
            spread = f" <span class=spread>가격차 {ps['spread_pct']}%</span>" if ps.get("spread_pct") else ""
            tr = c.get("trend") or []
            spark = _sparkline(tr)
            if spark:
                trendline = f"<div class=trend>📈 추이 {spark}</div>"
            elif tr:
                trendline = f"<div class=trend>📈 추이: 실측 {len(tr)}일치 누적 중(매일 1점)</div>"
            else:
                trendline = ""
            price_block = (f"<div class=rng>{_won(ps.get('min'))}~{_won(ps.get('max'))} · {ps.get('n_malls','?')}개 몰"
                           f" · 최저 {_esc(ps.get('low_mall'))}{spread}</div>"
                           f"{_ladder_bar(ps, agg)}{trendline}<div class=offs>{offs}</div>")
        rows.append(f"<div class=cat-row><div class=cdisp><b>{_esc(c.get('disp'))}</b>{ins}</div>"
                    f"<div class=cmeta>{code}{(' · '+_esc(sub)) if sub else ''}</div>{price_block}</div>")
    more = (f"<div class=more>… 하위 카탈로그 총 {agg['n_catalogs']}개 중 {len(panel['catalogs'])}개 표시</div>"
            if panel["n_more"] else "")
    priced = f" · 💰 {agg['n_priced']}개 가격적재 · {_won(agg['min'])}~{_won(agg['max'])} · 🏬 {agg['n_malls']}몰" \
        if agg.get("n_priced") else " · 💰 가격 미적재"
    idhead = (f"<div class=idagg>📦 카탈로그(ctlg_no) {agg['n_catalogs']}개 "
              f"· 🧩 인사이트 {agg['n_with_insight']}개{priced}</div>")
    return ("<div class=\"lab id\">🔗 하위 카탈로그 + 가격사다리 (패키지 → ctlg_no → 크로스몰)</div>"
            + idhead + "".join(rows) + more)


def _price_panel_html(panel):
    """신발: 변형 SKU + 크로스몰 가격사다리/판매처(identity-graph)."""
    agg = panel["agg"]
    sku_rows = []
    for s in panel["skus"]:
        ps = s["ps"]
        offs = "".join(
            f"<div class=off><a href='{_esc(o.get('url'))}' target=_blank>{_esc(o.get('mall'))}</a>"
            f"<span class=plat>{_esc(o.get('platform'))}</span>"
            f"<span class=tag>{'중고' if o.get('used') else '새'}</span>"
            f"<b>{_won(o.get('price'))}</b></div>" for o in s["offers"])
        mm = " <span class=mm>다중몰</span>" if s["multi_mall"] else ""
        rng = (f"{_won(ps.get('min'))}~{_won(ps.get('max'))} · {ps.get('n_malls','?')}개 몰"
               f" · 최저 {_esc(ps.get('low_mall'))}" if ps.get("min") else "가격 정보 없음")
        spread = f" <span class=spread>가격차 {ps['spread_pct']}%</span>" if ps.get("spread_pct") else ""
        sku_rows.append(
            f"<div class=sku><div class=skuh><b>{_esc(s['sku'])}</b>{mm}</div>"
            f"<div class=rng>{rng}{spread}</div>{_ladder_bar(ps, agg)}<div class=offs>{offs}</div></div>")
    more = (f"<div class=more>… SKU 총 {agg['n_skus_total']}개 중 {agg['n_skus_shown']}개 표시</div>"
            if agg["n_skus_total"] > agg["n_skus_shown"] else "")
    idhead = (f"<div class=idagg>📦 SKU {agg['n_skus_total']}개 · 💰 {_won(agg['min'])}~{_won(agg['max'])}"
              f" · 🏬 {agg['n_malls']}개 판매처</div>")
    return ("<div class=\"lab id\">🔗 product-identity-graph — 가격·판매처</div>"
            + idhead + "".join(sku_rows) + more)


def _evlinks(evs):
    """근거 링크(원문 후기/영상). evs=[{src?,url,quote}] 또는 [{url,quote}]."""
    out = []
    for e in (evs or [])[:3]:
        u = e.get("url")
        if not u:
            continue
        lbl = "영상" if (e.get("src") or e.get("source")) == "youtube" else "후기"
        out.append(f"<a class=evl href='{_esc(u)}' target=_blank rel=noopener "
                   f"title='{_esc(e.get('quote') or '')}'>📎{lbl}</a>")
    return "".join(out)


def _guide_block(g):
    """소비자 정직 가이드(패키지별) — 좌측 인사이트와 중복 피해 '주요몰 최저가 + 솔직한 단점 + 근거'에 집중."""
    if not g:
        return ""
    pr = (g.get("price") or {}); head = pr.get("head")
    if head:
        minor = "" if head.get("major") else "<span class=cg-minor>소형 판매처</span>"
        price = (f"<div class=cg-price><span class=cg-p>{_won(head.get('price'))}</span>"
                 f"<span class=cg-m>주요몰 최저 · <b>{_esc(head.get('mall'))}</b>{minor} · 비교 {head.get('n_malls')}몰</span></div>")
    else:
        price = "<div class=cg-price><span class=cg-m>가격 비교 준비 중</span></div>"
    cons = g.get("cons") or []
    if cons:
        items = "".join(f"<li>{_esc(c['text'])} <span class=cg-n>{('· '+str(c['n'])+'건') if c.get('n') else ''}</span>"
                        f" {_evlinks(c.get('evs'))}</li>" for c in cons[:5])
        consb = f"<div class=cg-bad><div class=cg-h>🤔 솔직한 단점 — 후기에서 나온 아쉬운 점 ({g.get('n_cons')})</div><ul>{items}</ul></div>"
    else:
        consb = "<div class=cg-none>🔎 수집된 후기에서 두드러진 단점 언급은 없었어요 (단점이 '없다'가 아니라 자주 등장하지 않았다는 뜻)</div>"
    return ("<div class=\"lab guide\">🛒 소비자 정직 가이드 <span class=labnote>광고·협찬 없음 · 주요몰 최저가 + 솔직한 단점</span></div>"
            f"<div class=cg-wrap>{price}{consb}</div>")


def _seller_block(s):
    """셀러 인텔리전스(패키지별) — 속성랭킹(좌측 기존)과 중복 피해 '카테고리 갭·약점·가격경쟁력'에 집중."""
    if not s:
        return ""
    parts = []
    if s.get("gap"):
        parts.append(f"<div class=sl-row><span class=sl-k>⚠️ 카테고리 갭(중시되나 약함)</span>"
                     f"<span class=sl-v>{' · '.join(_esc(x) for x in s['gap'])}</span></div>")
    if s.get("cov"):
        parts.append(f"<div class=sl-row><span class=sl-k>✅ 충족 속성</span>"
                     f"<span class=sl-v>{' · '.join(_esc(x) for x in s['cov'])}</span></div>")
    pp = s.get("price") or {}
    if pp.get("min"):
        comp = (f"{_won(pp.get('min'))}~{_won(pp.get('max'))} · 최저 {_esc(pp.get('low_mall'))} · {pp.get('n_malls')}몰"
                + (f" · 가격차 {pp.get('spread')}%" if pp.get('spread') else ""))
        parts.append(f"<div class=sl-row><span class=sl-k>💰 가격 경쟁력</span><span class=sl-v>{comp}</span></div>")
    if s.get("weak"):
        items = "".join(f"<li>{_esc(w['t'])} <span class=cg-n>{('· '+str(w['n'])+'건') if w.get('n') else ''}</span>"
                        f" {_evlinks(w.get('ev'))}</li>" for w in s["weak"][:3])
        parts.append(f"<div class=sl-weak><div class=cg-h>🔧 개선 포인트(약점)</div><ul>{items}</ul></div>")
    if not parts:
        return ""
    return ("<div class=\"lab seller\">🏷️ 셀러 인텔리전스 <span class=labnote>약점·갭 + 가격 경쟁력 · 카테고리 보드는 하단 nav</span></div>"
            f"<div class=sl-wrap>{''.join(parts)}</div>")


def _card_detail(r):
    """카드 펼침 영역: 좌(고객 대표 인사이트) + 우(카탈로그/가격), 아래 소비자 가이드·셀러 스택."""
    cv = rv.customer_view(r["rep"]); sv = rv.seller_view(r["rep"]); panel = r["panel"]
    head = f"<div class=headline>“{_esc(cv['headline'])}”</div>" if cv["headline"] else ""
    secs = "".join(
        f"<div class=sec><div class=st>{s['emoji']} {_esc(s['title'])}</div><ul>"
        + "".join(f"<li>{_esc(it['text'])}</li>" for it in s["items"]) + "</ul></div>"
        for s in cv["sections"])
    proof = f"<div class=proof>🗣️ 실제 후기 {cv['review_count']}건 기반</div>" if cv["review_count"] else ""
    sdims = "".join(
        f"<div class=sdim><b>#{d['rank']} {_esc(d['label'])}</b>"
        f"<span class=metric>coverage {d['coverage']:.0%} · lift ×{(d['lift'] or 0):.1f} · 언급 {d['mentions']}</span></div>"
        for d in sv["dims"])
    seller = f"<details class=sellerbox><summary>🏷️ 셀러 분석(속성 랭킹)</summary>{sdims}</details>"
    buzz = _buzz_html(r.get("buzz"))
    right = _catalog_panel_html(panel) if panel["mode"] == "catalog" else _price_panel_html(panel)
    stack = _guide_block(r.get("guide")) + _seller_block(r.get("seller"))
    return (f"<div class=cols><div class=col><div class=\"lab cust\">🔍 인사이트 — 대표(고객)</div>"
            f"{buzz}{head}{secs}{proof}{seller}</div><div class=col>{right}</div></div>"
            + (f"<div class=stack>{stack}</div>" if stack else ""))


def _card_summary(r):
    """카드 접힘 줄: 카테고리 · 상품명 · 헤드라인 · 카탈로그수 · 가격범위 · 추이."""
    cv = rv.customer_view(r["rep"]); agg = r["panel"]["agg"]
    hl = (cv["headline"] or "")[:46]
    quote = f"“{_esc(hl)}”" if hl else "<span class=nodata>인사이트 수집 중…</span>"
    nc = agg.get("n_catalogs") or agg.get("n_skus_total") or 0
    price = f"💰 {_won(agg['min'])}~{_won(agg['max'])}" if agg.get("min") else "💰 가격 적재중"
    # 대표 추이(가격 보유 첫 카탈로그)
    chg_badge = ""
    for c in r["panel"].get("catalogs", []) or []:
        tr = [v for _, v in (c.get("trend") or []) if v]
        if len(tr) >= 2:
            chg = (tr[-1] - tr[0]) / tr[0] * 100
            col = "#34d399" if chg < -0.5 else ("#f87171" if chg > 0.5 else "#9aa3af")
            ar = "▼" if chg < -0.5 else ("▲" if chg > 0.5 else "→")
            chg_badge = f" · <span style='color:{col};font-weight:700'>📈{ar}{abs(chg):.0f}%</span>"
            break
    bz = r.get("buzz") or {}
    buzz_mini = ""
    if bz.get("naver_blog") is not None:
        yt = f" · 🎬~{_num(bz['youtube'])}" if bz.get("youtube") is not None else ""
        buzz_mini = f" · 📣 📝{_num(bz['naver_blog'])}{yt}"
    return (f"<span class=badge>{_esc(r['category'])}</span>"
            f"<span class=kw>{_esc(r['keyword'])}</span>"
            f"<span class=quote>{quote}</span>"
            f"<span class=mini>📦 {nc} · {price}{chg_badge}{buzz_mini}</span>")


def _status_html(s):
    """추출 현황 대시보드 — 어디까지 직접 뽑혔는지(전부 실측)."""
    if not s:
        return ""
    def bar(label, done, total, emoji):
        pct = (done / total * 100) if total else 0
        return (f"<div class=stcell><div class=stlab>{emoji} {label}</div>"
                f"<div class=stnum><b>{done:,}</b>/{total:,} <span class=stpct>{pct:.0f}%</span></div>"
                f"<div class=stbar><span style='width:{pct:.0f}%'></span></div></div>")
    return ("<div class=status><div class=sttitle>📊 직접 수집 현황 (실측)</div><div class=stgrid>"
            + bar("패키지 비정형 인사이트", s["pkg_ins"], s["pkg"], "📝")
            + bar("카탈로그 가격(크로스몰)", s["priced"], s["pkg"], "💰")
            + bar("카탈로그레벨 비정형", s["cat_ins"], s["cat_total"], "🧩")
            + bar("유튜브 인사이트", s["yt_ins"], s["pkg"], "🎬")
            + bar("언급량(네이버)", s["buzz"], s["pkg"], "📣")
            + bar("언급량(유튜브)", s["buzz_yt"], s["pkg"], "🎬")
            + "</div></div>")


def render_html(rows, title="대표 인사이트 + 하위 카탈로그", sub="", status=None):
    # 카테고리별 정렬·집계
    rows = sorted(rows, key=lambda r: (r.get("category") or "", r.get("keyword") or ""))
    cats = []
    for r in rows:
        if r.get("category") and r["category"] not in cats:
            cats.append(r["category"])
    cards = []
    for r in rows:
        # 상세는 <template> 안에 두고 카드를 펼칠 때만 DOM에 주입(lazy) → 2,713개여도 초기 렌더 가벼움.
        cards.append(
            f"<details class=card data-cat=\"{_esc(r.get('category'))}\" data-kw=\"{_esc((r.get('keyword') or '').lower())}\">"
            f"<summary>{_card_summary(r)}</summary>"
            f"<template>{_card_detail(r)}</template></details>")
    chips = ("<button class='chip active' data-c='all'>전체 "
             f"<b>{len(rows)}</b></button>"
             + "".join(f"<button class=chip data-c=\"{_esc(c)}\">{_esc(c)} "
                       f"<b>{sum(1 for r in rows if r.get('category')==c)}</b></button>" for c in cats))
    js = """
    const cards=[...document.querySelectorAll('.card')];
    const chips=[...document.querySelectorAll('.chip')];
    let cat='all', q='';
    // lazy: 카드를 처음 펼칠 때만 <template> 상세를 주입(초기 DOM 경량 유지)
    cards.forEach(d=>d.addEventListener('toggle',function(){
      if(d.open && !d.dataset.hyd){const t=d.querySelector('template');
        if(t)d.appendChild(t.content.cloneNode(true)); d.dataset.hyd='1';}
    }));
    function apply(){let n=0;cards.forEach(c=>{
      const ok=(cat==='all'||c.dataset.cat===cat)&&(q===''||c.dataset.kw.includes(q));
      c.style.display=ok?'':'none'; if(ok)n++;});
      document.getElementById('cnt').textContent=n;}
    chips.forEach(ch=>ch.onclick=()=>{chips.forEach(x=>x.classList.remove('active'));
      ch.classList.add('active');cat=ch.dataset.c;apply();});
    document.getElementById('q').oninput=e=>{q=e.target.value.trim().toLowerCase();apply();};
    """
    css = """
    body{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:#0f1115;color:#e6e8eb;margin:0;padding:24px}
    h1{font-size:20px;margin:0 0 4px}.sub{color:#9aa3af;font-size:13px;margin-bottom:20px}
    .card{background:#171a21;border:1px solid #262b36;border-radius:14px;padding:16px 18px;margin-bottom:18px}
    .ctitle{font-size:16px;font-weight:700;margin-bottom:12px}.cat{color:#7dd3fc;font-size:12px;margin-left:6px}
    .uid{color:#5b6472;font-size:11px;font-weight:400}
    .cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:820px){.cols{grid-template-columns:1fr}}
    .col{background:#0f1218;border:1px solid #222732;border-radius:10px;padding:12px 14px}
    .lab{font-size:12px;font-weight:700;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #232936}
    .cust{color:#7ee0a0}.id{color:#facc88}
    .headline{font-size:15px;font-weight:600;color:#eafff1;background:#15241b;border-radius:8px;padding:8px 12px;margin-bottom:10px}
    .sec{margin-bottom:9px}.st{font-size:13px;font-weight:700;color:#a7f3c8;margin-bottom:2px}
    .sec ul{margin:3px 0 0;padding-left:18px}.sec li{font-size:13px;margin:3px 0;color:#d7dde5}
    .proof{margin-top:6px;color:#86efac;font-size:12px;background:#13201a;border-radius:6px;padding:6px 10px}
    .sellerbox{margin-top:10px;font-size:12px;color:#9fb0c5}.sellerbox summary{cursor:pointer;color:#a3b3c9;font-weight:600}
    .sdim{display:flex;justify-content:space-between;gap:8px;padding:3px 0;border-bottom:1px solid #1b212c}
    .sdim b{font-size:12px}.metric{color:#6b7686;font-size:11px;white-space:nowrap}
    .idagg{font-size:12px;color:#fcd9a0;background:#241d12;border-radius:8px;padding:7px 12px;margin-bottom:10px}
    .sku{border:1px solid #242b36;border-radius:9px;padding:9px 11px;margin-bottom:9px;background:#12161d}
    .skuh{font-size:13px;font-weight:700;color:#e6e8eb}.mm{font-size:10px;color:#fca5a5;background:#2a1717;border-radius:4px;padding:1px 5px;margin-left:6px}
    .rng{font-size:12px;color:#cbd2db;margin:3px 0 6px}.spread{color:#f59e0b}
    .ladder{position:relative;height:7px;background:#1c222d;border-radius:4px;margin:4px 0 8px}
    .ladder .bar{position:absolute;top:0;height:7px;background:linear-gradient(90deg,#34d399,#f59e0b);border-radius:4px}
    .ladder .med{position:absolute;top:-2px;width:2px;height:11px;background:#e6e8eb;border-radius:1px}
    .offs{display:flex;flex-direction:column;gap:3px}
    .off{display:flex;align-items:center;gap:7px;font-size:12px;color:#cbd2db}
    .off a{color:#93c5fd;text-decoration:none;min-width:96px}.plat{color:#6b7686;font-size:11px;flex:1}
    .tag{font-size:10px;color:#9aa3af;border:1px solid #313846;border-radius:4px;padding:0 4px}
    .off b{color:#86efac}.more{color:#5b6472;font-size:11px;margin-top:4px}
    .cat-row{border:1px solid #242b36;border-radius:8px;padding:7px 10px;margin-bottom:6px;background:#12161d}
    .cdisp{font-size:13px;color:#e6e8eb}.cdisp b{font-weight:600}
    .ins{font-size:10px;color:#86efac;background:#13201a;border-radius:4px;padding:1px 5px;margin-left:6px}
    .cmeta{font-size:11px;color:#6b7686;margin-top:2px}.code{color:#7d8694}
    .trend{display:flex;align-items:center;gap:6px;font-size:11px;color:#8a93a3;margin:2px 0 6px}
    .spark{display:inline-flex;align-items:center;gap:6px}.spark svg{display:block}
    .chg{font-size:11px;font-weight:700}.days{color:#5b6472;font-weight:400}
    /* 브라우즈 UI */
    .controls{position:sticky;top:0;z-index:5;background:#0f1115;padding:10px 0 12px;margin-bottom:6px;border-bottom:1px solid #222732}
    #q{width:100%;max-width:420px;box-sizing:border-box;background:#171a21;border:1px solid #2b3240;border-radius:9px;
       color:#e6e8eb;font-size:14px;padding:9px 12px;margin-bottom:9px}
    .chips{display:flex;flex-wrap:wrap;gap:6px}
    .chip{background:#171a21;border:1px solid #2b3240;color:#cbd2db;font-size:12px;border-radius:999px;
          padding:5px 11px;cursor:pointer}.chip b{color:#7dd3fc;margin-left:3px}
    .chip.active{background:#1f6feb22;border-color:#3b82f6;color:#dbeafe}
    .card{background:#171a21;border:1px solid #262b36;border-radius:12px;margin-bottom:10px;overflow:hidden}
    .card>summary{list-style:none;cursor:pointer;padding:12px 16px;display:flex;flex-wrap:wrap;align-items:center;gap:10px}
    .card>summary::-webkit-details-marker{display:none}
    .card[open]>summary{border-bottom:1px solid #232936;background:#12161d}
    .card .cols{padding:14px 16px}
    .badge{background:#0e2030;color:#7dd3fc;font-size:11px;font-weight:700;border-radius:6px;padding:2px 8px;white-space:nowrap}
    .kw{font-size:15px;font-weight:700;color:#e6e8eb}
    .quote{color:#86efac;font-size:13px;flex:1;min-width:160px}
    .quote .nodata{color:#5b6472;font-style:italic}
    .mini{color:#9aa3af;font-size:12px;white-space:nowrap}
    .buzz{background:#1a1530;border:1px solid #34306a;border-radius:8px;padding:7px 12px;margin-bottom:10px;
          font-size:12px;color:#c4b5fd}.buzz b{color:#ede9fe}
    .buzznote{color:#7c74a8;font-size:10px;margin-left:4px}
    .pend{color:#6b7686}
    .status{background:#12161d;border:1px solid #232b38;border-radius:12px;padding:12px 16px;margin-bottom:16px}
    .sttitle{font-size:13px;font-weight:700;color:#cbd2db;margin-bottom:10px}
    .stgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
    .stcell{}.stlab{font-size:11px;color:#9aa3af;margin-bottom:3px}
    .stnum{font-size:13px;color:#e6e8eb}.stnum b{color:#7ee0a0}.stpct{color:#6b7686;font-size:11px}
    .stbar{height:5px;background:#1c222d;border-radius:3px;margin-top:4px;overflow:hidden}
    .stbar span{display:block;height:5px;background:linear-gradient(90deg,#34d399,#3b82f6);border-radius:3px}
    /* 결합 스택: 소비자 가이드(cg-) + 셀러(sl-) */
    .stack{padding:2px 16px 16px}
    .lab.guide{color:#fca5a5}.lab.seller{color:#fcd9a0}
    .labnote{font-weight:400;color:#6b7686;font-size:11px;margin-left:6px}
    .cg-wrap,.sl-wrap{background:#0f1218;border:1px solid #222732;border-radius:10px;padding:12px 14px;margin-bottom:12px}
    .cg-price{background:#0e1f22;border:1px solid #164e52;border-radius:9px;padding:9px 12px;margin-bottom:10px}
    .cg-p{font-size:19px;font-weight:800;color:#5eead4}.cg-m{font-size:12px;color:#9aa3af;margin-left:8px}.cg-m b{color:#e6e8eb}
    .cg-minor{font-size:10px;color:#fbbf24;background:#2a210f;border-radius:4px;padding:1px 5px;margin-left:5px}
    .cg-h{font-size:12.5px;font-weight:700;color:#fca5a5;margin-bottom:4px}
    .cg-bad ul{margin:0;padding-left:18px}.cg-bad li{font-size:13px;color:#e2c4c4;margin:3px 0}
    .cg-n{color:#7a6b6b;font-size:11px}
    .cg-none{font-size:12px;color:#8a93a3;background:#13201a;border:1px solid #1f3a2a;border-radius:7px;padding:7px 11px}
    .sl-row{display:flex;gap:10px;font-size:12.5px;padding:5px 0;border-bottom:1px solid #1b212c}
    .sl-k{color:#fcd9a0;font-weight:700;white-space:nowrap;min-width:150px}.sl-v{color:#cbd2db}
    .sl-weak{margin-top:8px}.sl-weak ul{margin:0;padding-left:18px}.sl-weak li{font-size:13px;color:#d7dde5;margin:3px 0}
    .evl{font-size:10.5px;color:#93c5fd;text-decoration:none;border:1px solid #2b3240;border-radius:5px;padding:0 5px;margin-left:3px}
    .evl:hover{background:#1f6feb22}
    """
    sub = sub or "카드를 펼치면 좌(고객 대표 인사이트) + 우(하위 카탈로그 ctlg_no/풀네임 · 가격사다리 · 추이). 칩/검색으로 거르기."
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>번들 결합 화면 — 인사이트 + 카탈로그</title><style>{css}</style></head><body>"
            f"<h1>{_esc(title)}</h1><div class=sub>{_esc(sub)}</div>"
            f"{_status_html(status)}"
            f"<div class=controls><input id=q placeholder='상품명 검색…'>"
            f"<div class=chips>{chips}</div>"
            f"<div class=sub style='margin:8px 0 0'>표시 <b id=cnt>{len(rows)}</b>개</div></div>"
            f"{''.join(cards)}<script>{js}</script></body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="data/bundle_view.html")
    ap.add_argument("--bundle", default=None, help="특정 패키지 uid(예: P7828). 없으면 브라우즈")
    ap.add_argument("--per-cat", type=int, default=0, help="카테고리당 최대 패키지 수(0=무제한)")
    ap.add_argument("--max-cards", type=int, default=0, help="전체 카드 상한(0=무제한, 전체 탐색기)")
    ap.add_argument("--insight-only", action="store_true", help="대표 인사이트 보유 패키지만(기본은 전체)")
    ap.add_argument("--priced-only", action="store_true", help="가격사다리 있는 패키지만")
    ap.add_argument("--skus", type=int, default=12, help="하위 카탈로그/SKU 표시 수")
    args = ap.parse_args()

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]

    # 결합용 패키지별 데이터 — 소비자 가이드/셀러를 각 1회 집계 후 uid로 매핑(상세에 스택)
    cg_by_uid, sd_by_uid = {}, {}
    try:
        cg_by_uid = {e["uid"]: e for e in cg.gather(db)["prod"]}
    except Exception as ex:
        print("  ! 소비자 가이드 데이터 스킵:", ex)
    try:
        sd_by_uid = {e["id"]: e for e in sd.gather(db)["prod"]}
    except Exception as ex:
        print("  ! 셀러 데이터 스킵:", ex)
    print(f"결합 데이터 · 가이드 {len(cg_by_uid):,} · 셀러 {len(sd_by_uid):,}")

    if args.bundle:
        bases = [db.products.find_one({"_id": args.bundle})]
    else:
        # 전체 패키지 탐색기 — type=package 전부(미처리 인사이트 포함). 옵션으로 인사이트/가격 보유만.
        q = {"type": "package"}
        if args.insight_only:
            q["representative.dims.0"] = {"$exists": True}
        if args.priced_only:
            q["catalogs.price_summary.min"] = {"$ne": None}
        bases = list(db.products.find(q))
        # 카테고리 → (인사이트 보유, 가격 보유, 카탈로그 많음, 이름) 순으로 정렬: 풍부한 카드가 위로.
        def _rank(d):
            has_ins = bool((d.get("representative") or {}).get("dims"))
            has_price = any((c.get("price_summary") or {}).get("min") for c in d.get("catalogs") or [])
            return (d.get("category_l1") or d.get("category") or "zz",
                    -int(has_ins), -int(has_price), -(d.get("n_catalogs") or 0), d.get("keyword") or "")
        bases.sort(key=_rank)
        if args.per_cat:
            seen = {}
            kept = []
            for d in bases:
                c = d.get("category_l1") or d.get("category") or ""
                if seen.get(c, 0) >= args.per_cat:
                    continue
                seen[c] = seen.get(c, 0) + 1
                kept.append(d)
            bases = kept
        if args.max_cards:
            bases = bases[:args.max_cards]

    rows = []
    for base in bases:
        if not base:
            continue
        panel = identity_panel(db, base, max_skus=args.skus)
        rows.append({"uid": base["_id"], "keyword": base.get("keyword"),
                     "category": base.get("category_l1") or base.get("category"),
                     "rep": base.get("representative") or {}, "panel": panel,
                     "buzz": base.get("buzz"),
                     "guide": cg_by_uid.get(base["_id"]), "seller": sd_by_uid.get(base["_id"])})

    # 전체 추출 현황(실측) — 화면 표본이 아니라 DB 전체 기준
    cat_total = cat_ins = priced = 0
    for p in db.products.find({"type": "package"},
                              {"catalogs.insight.dims": 1, "catalogs.price_summary.min": 1}):
        cats = p.get("catalogs") or []
        cat_total += len(cats)
        cat_ins += sum(1 for c in cats if (c.get("insight") or {}).get("dims"))
        if any((c.get("price_summary") or {}).get("min") for c in cats):
            priced += 1
    status = {
        "pkg": db.products.count_documents({"type": "package"}),
        "pkg_ins": db.products.count_documents({"type": "package", "representative.dims.0": {"$exists": True}}),
        "priced": priced, "cat_total": cat_total, "cat_ins": cat_ins,
        "yt_ins": db.products.count_documents({"type": "package", "youtube.status": "done"}),
        "buzz": db.products.count_documents({"type": "package", "buzz.naver_blog": {"$exists": True}}),
        "buzz_yt": db.products.count_documents({"type": "package", "buzz.youtube_status": "done"}),
    }
    n_priced = sum(1 for r in rows if (r["panel"]["agg"].get("min")))
    title = f"번들 결합 화면 — 대표 인사이트 + 카탈로그 + 가격 ({len(rows)}개 패키지)"
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(render_html(rows, title=title, status=status,
                            sub=f"가격사다리 보유 {n_priced}/{len(rows)} · 카드 펼치면 인사이트+카탈로그+가격+추이 · 칩/검색으로 거르기"))
    print(f"HTML 생성 → {args.html} · 패키지 {len(rows)}개 (가격보유 {n_priced})")


if __name__ == "__main__":
    main()
