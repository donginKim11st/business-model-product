#!/usr/bin/env python3
"""
브랜드 대면 데모 — 네이버 / 11번가 / 쿠팡.

가상 브랜드(VIORA)의 카탈로그를 3개 마켓의 합성 리스팅에 매칭해, 브랜드가 보는
화면을 생성한다: (해자) 동일 사업자가 여러 상호 뒤에서 파는 것 식별 + 크로스마켓
제품 통합 → 공식가 침해/병행수입/허위'정품' 탐지 → 조치. (= 폴센트가 안 하는 것)

honesty: 쿠팡은 실데이터 미연동(게이트) → 쿠팡 수치는 '참고치'로 분리 표기하고
'접근 가능 채널(네이버+11번가)' 기준 수치를 병기한다. 셀러 식별은 사업자번호 기반
거의 결정적(near-100%), 마켓 간 제품 통합은 held-out ~50%의 어려운 문제.

    python3 demo_brand.py   →  outputs/brand_demo.html, outputs/brand_demo_result.json
"""
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pig.blocking import HybridBlocker
from pig.resolve import resolve
from pig.seller_resolve import resolve_sellers
from pig.normalize import extract_attributes

MKT = {"naver": "네이버", "11st": "11번가", "coupang": "쿠팡"}
GATED = {"coupang"}  # channels not yet data-connected in production (legal/access gate)
_NOISE = re.compile(r"\b(공식판매|공식|정품|본사직영|최저가|무료배송|당일발송|당일출고|새상품|미개봉)\b")


def clean_name(title):
    t = _NOISE.sub("", title.replace("[병행수입]", ""))
    return re.sub(r"\s+", " ", t).strip()


def won(n):
    return f"{n:,}원"


def mkt(m):
    star = "*" if m in GATED else ""
    return MKT.get(m, m) + star


def violation_type(rec, attrs):
    """Classify an unauthorized listing into an actionable taxonomy."""
    if rec["is_official"]:
        return None
    if attrs["is_graymarket"]:
        return "gray"            # 병행수입/면세 — channel/legal exposure
    if "정품" in rec["title"]:
        return "false_authentic"  # 미승인 셀러가 '정품' 표기 → 상표/허위표시 소지
    return "undercut"            # 단순 미승인 저가


VIO_LABEL = {"gray": "병행수입", "false_authentic": "허위 '정품' 표기", "undercut": "미승인 저가"}


def load():
    with open(os.path.join(HERE, "data", "brand_demo.json"), encoding="utf-8") as f:
        return json.load(f)


def analyze(records):
    by_id = {r["id"]: r for r in records}
    attrs = {r["id"]: extract_attributes(r) for r in records}
    run = resolve(records, HybridBlocker())

    sellers = resolve_sellers(records)
    biz_to_cluster = {s["biz_reg_no"]: s for s in sellers if s["biz_reg_no"]}
    for sc in sellers:
        items = [by_id[i] for i in sc["listing_ids"]]
        nonoff = [r for r in items if not r["is_official"]]
        discs = [(r["official_price"] - r["price"]) / r["official_price"] for r in nonoff if r["official_price"]]
        sc["avg_discount_pct"] = (sum(discs) / len(discs) * 100) if discs else 0
        sc["has_gray"] = any(attrs[r["id"]]["is_graymarket"] for r in items)
        sc["false_authentic"] = any(violation_type(r, attrs[r["id"]]) == "false_authentic" for r in items)
        sc["accessible_storefronts"] = sorted({r["seller_name"] for r in items if r["marketplace"] not in GATED})

    def is_multi(biz):
        c = biz_to_cluster.get(biz)
        return bool(c and c["multi_storefront"] and not c["is_official"])

    products = []
    for cluster in run["clusters"]:
        items = [by_id[i] for i in cluster]
        official = next((r for r in items if r["is_official"]), None)
        official_price = items[0]["official_price"]
        lowest = min(items, key=lambda r: r["price"])
        acc_items = [r for r in items if r["marketplace"] not in GATED]
        acc_lowest = min(acc_items, key=lambda r: r["price"]) if acc_items else None
        biz_set = sorted({r["biz_reg_no"] for r in items})
        names = sorted({r["seller_name"] for r in items})
        vios = [(r, violation_type(r, attrs[r["id"]])) for r in items]
        severity = 0  # 3=병행수입, 2=허위정품/다중상호, 1=미승인저가, 0=없음
        for r, v in vios:
            if v == "gray":
                severity = max(severity, 3)
            elif v == "false_authentic" or is_multi(r["biz_reg_no"]):
                severity = max(severity, 2)
            elif v == "undercut":
                severity = max(severity, 1)
        products.append({
            "cluster": sorted(cluster),
            "severity": severity,
            "name": clean_name((official or items[0])["title"]),
            "category": attrs[items[0]["id"]]["category"],
            "official_price": official_price,
            "lowest": lowest, "lowest_is_multi": is_multi(lowest["biz_reg_no"]),
            "acc_lowest": acc_lowest,
            "acc_undercut_pct": ((official_price - acc_lowest["price"]) / official_price * 100)
                                if acc_lowest and acc_lowest["price"] < official_price else 0,
            "undercut_pct": ((official_price - lowest["price"]) / official_price * 100)
                            if lowest["price"] < official_price else 0,
            "marketplaces": sorted({r["marketplace"] for r in items}),
            "biz_count": len(biz_set), "storefront_count": len(names),
            "gray": [r for r, v in vios if v == "gray"],
            "below_official": lowest["price"] < official_price,
            "acc_below_official": bool(acc_lowest and acc_lowest["price"] < official_price),
        })
    # lead with actionable severity, not raw discount depth
    products.sort(key=lambda p: (-p["severity"], -p["undercut_pct"]))

    # actionable violations = gray OR false-authentic OR from a multi-storefront biz
    actionable = 0
    for r in records:
        v = violation_type(r, attrs[r["id"]])
        if v in ("gray", "false_authentic") or is_multi(r["biz_reg_no"]):
            actionable += 1

    # rough margin-exposure estimate on the ACCESSIBLE channels only (네이버+11번가),
    # per-unit illustration (구매량 미반영) — clearly labelled as an estimate.
    impact_won = sum(max(0, r["official_price"] - r["price"]) for r in records
                     if not r["is_official"] and r["marketplace"] not in GATED
                     and r["price"] < r["official_price"])

    external = [s for s in sellers if not s["is_official"]]
    multi = [s for s in external if s["multi_storefront"]]
    kpis = {
        "sku": len(products), "listings": len(records),
        "actionable": actionable,
        "external_sellers": len(external),
        "multi_storefront_sellers": len(multi),
        "gray_listings": sum(1 for r in records if attrs[r["id"]]["is_graymarket"]),
        "undercut_all": sum(1 for p in products if p["below_official"]),
        "undercut_accessible": sum(1 for p in products if p["acc_below_official"]),
        "impact_won": impact_won,
        "deepest_all": max((p["undercut_pct"] for p in products), default=0),
        "deepest_accessible": max((p["acc_undercut_pct"] for p in products), default=0),
    }
    return {"run": run, "products": products, "sellers": sellers, "by_id": by_id,
            "kpis": kpis, "multi": multi}


# --------------------------- render ---------------------------
def chips(markets):
    return "".join(f"<span class='chip'>{mkt(m)}</span>" for m in markets)


def render(A):
    k = A["kpis"]
    by_id = A["by_id"]
    external = [s for s in A["sellers"] if not s["is_official"]]
    hero = max((s for s in external if s["multi_storefront"]),
               key=lambda s: s["storefront_count"], default=None)

    # ---- HERO: identity collapse ----
    hero_block = ""
    if hero:
        prods_hit = sorted({by_id[i]["entity_id"] for i in hero["listing_ids"]})
        sf_nodes = "".join(
            f"<div class='sf'>{html.escape(n)}</div>" for n in hero["storefront_names"])
        acc = hero["accessible_storefronts"]
        acc_note = (f"접근 가능 채널(네이버·11번가)만으로도 상호 {len(acc)}개"
                    f"({html.escape(' · '.join(acc))})로 동일사업자 적발 유지"
                    if len(acc) > 1 else "접근 가능 채널 단독으로는 단일 상호로 보임")
        hero_block = f"""
    <div class="hero">
      <div class="hero-tag">🕵️ 동일사업자 적발 — 폴센트류 가격앱이 구조적으로 못 그리는 그림</div>
      <div class="collapse">
        <div class="sfs">{sf_nodes}</div>
        <div class="arrow">→</div>
        <div class="bizbox"><span class="lab">동일 사업자</span><b>{html.escape(hero['biz_reg_no'])}</b>
          <span class="lab">상호 {hero['storefront_count']}개 · {len(prods_hit)}개 제품 · 평균 −{hero['avg_discount_pct']:.0f}%</span></div>
      </div>
      <div class="hero-foot">
        <span class="acc">✓ {acc_note}</span>
        <span class="actions"><span class="lab">조치(데모):</span>
          <button class="act">⚠ 경고 발송</button><button class="act">🚩 마켓 신고</button>
          <button class="act ghost">👁 모니터링</button>
          <button class="act ghost">⬇ 증거패킷</button></span>
      </div>
      <div class="hero-evi">증거패킷에 자동 포함: 리스팅별 <b>허위 '정품' 표기</b> 문구 · 사업자번호 · 마켓 · 가격 · 캡처시각
        — 셀러 정지/내용증명에 바로 쓰는 자료 (가격앱은 못 주는 것)</div>
    </div>"""

    # ---- seller cluster table ----
    srows = []
    for s in sorted(external, key=lambda s: (-int(s["multi_storefront"]), -s["listing_count"])):
        tags = ""
        if s["multi_storefront"]:
            tags += "<span class='pill red'>다중상호</span>"
        if s["has_gray"]:
            tags += "<span class='pill amber'>병행수입</span>"
        if s["false_authentic"]:
            tags += "<span class='pill amber'>허위정품</span>"
        srows.append(f"""
        <tr class="{'hot' if s['multi_storefront'] else ''}">
          <td><code>{html.escape(s['biz_reg_no'] or '미상')}</code></td>
          <td><b>{html.escape(' · '.join(s['storefront_names']))}</b><br>{tags}</td>
          <td>{chips(s['marketplaces'])}</td>
          <td class='num'>{s['listing_count']}</td>
          <td class='num' style='color:var(--red);font-weight:700'>−{s['avg_discount_pct']:.0f}%</td>
          <td><button class='act sm'>경고</button><button class='act sm ghost'>신고</button></td>
        </tr>""")

    # ---- product table (supporting evidence) ----
    prows = []
    for p in A["products"]:
        low = p["lowest"]
        vt = violation_type(low, extract_attributes(low))
        who = f"{html.escape(low['seller_name'])}({mkt(low['marketplace'])})"
        if p["lowest_is_multi"]:
            who += " <span class='pill red xs'>다중상호</span>"
        vio_badge = f"<span class='pill amber xs'>{VIO_LABEL.get(vt, '')}</span>" if vt in ("gray", "false_authentic") else ""
        gap = (f"<b style='color:var(--red)'>−{p['undercut_pct']:.0f}%</b>"
               if p["below_official"] else "<span class='muted'>공식가</span>")
        acc_gap = (f"<span class='muted xs'>접근가능 −{p['acc_undercut_pct']:.0f}%</span>"
                   if p["acc_below_official"] else "<span class='muted xs'>접근가능 침해없음</span>")
        bar = max(8, 100 - p["undercut_pct"])
        prows.append(f"""
        <tr>
          <td><b>{html.escape(p['name'])}</b><br>
            <span class='sub'>{chips(p['marketplaces'])} · 사업자 {p['biz_count']} · 상호 {p['storefront_count']} · {len(p['cluster'])} 리스팅 → 1 제품 통합</span> {vio_badge}</td>
          <td class='num'>{won(p['official_price'])}<br><span class='sub'>공식가 라인</span></td>
          <td><div class='bar'><div class='fill' style='width:{bar:.0f}%'></div><span class='lowmark' style='left:{bar:.0f}%'></span></div>
            <span class='sub'>최저 {won(low['price'])} · {who}</span></td>
          <td class='num'>{gap}<br>{acc_gap}</td>
        </tr>""")

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VIORA · 채널 가격 거버넌스 모니터 (데모)</title>
<style>
:root{{--ink:#15181d;--muted:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--brand-d:#1e4fa8;
 --red:#d23b3b;--red-bg:#fdecec;--green:#1a9d57;--amber:#b9770b;--amber-bg:#fff6e6;--card:#fff;}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',Segoe UI,Roboto,sans-serif;
 color:var(--ink);background:var(--bg);line-height:1.5;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1000px;margin:0 auto;padding:0 20px 56px}}
header{{position:sticky;top:0;background:rgba(244,246,250,.93);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:13px 0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap;padding-bottom:0}}
h1{{font-size:17px;margin:0;font-weight:800}}
.logo{{background:var(--brand);color:#fff;font-weight:900;padding:5px 10px;border-radius:8px;letter-spacing:1px}}
.demo{{background:var(--amber-bg);color:var(--amber);font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px}}
.sub{{color:var(--muted);font-size:11.5px}} .muted{{color:var(--muted)}} .xs{{font-size:10.5px}}
.tagline{{font-size:13.5px;color:var(--brand-d);font-weight:700;margin:16px 0 4px}}
.tagline span{{color:var(--muted);font-weight:500}}
.caveat{{background:var(--amber-bg);border:1px solid #ffd591;border-radius:11px;padding:11px 14px;font-size:12px;margin:10px 0 4px;line-height:1.55}}
.caveat b{{color:var(--amber)}}
.kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:9px;margin:16px 0}}
@media(max-width:860px){{.kpis{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:520px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}}
.tile .big{{font-size:21px;font-weight:800;color:var(--ink);letter-spacing:-.4px}}
.tile .lab{{font-size:11px;color:var(--muted);margin-top:2px}}
.tile.moat{{border-color:#f3b5b5;background:#fff7f7}} .tile.moat .big{{color:var(--red)}}
.tile.ctx .big{{color:var(--muted);font-size:18px}}
.monitor{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;
 background:#eef3fd;border:1px solid #cfe0fb;border-radius:11px;padding:10px 14px;font-size:12px;margin:6px 0 2px}}
.monitor b{{color:var(--brand-d)}} .monitor .impact{{white-space:nowrap}} .monitor .impact b{{color:var(--red)}}
.hero{{background:linear-gradient(135deg,#fff,#fff5f5);border:1.5px solid #f0a3a3;border-radius:16px;padding:16px 18px;margin:14px 0}}
.hero-tag{{font-weight:800;color:var(--red);font-size:14px;margin-bottom:12px}}
.collapse{{display:flex;align-items:center;gap:14px;flex-wrap:wrap}}
.sfs{{display:flex;gap:8px;flex-wrap:wrap}}
.sf{{background:#fff;border:1px solid #e7b9b9;border-radius:9px;padding:7px 11px;font-weight:700;font-size:13px;box-shadow:0 1px 3px rgba(210,59,59,.12)}}
.arrow{{font-size:22px;color:var(--red);font-weight:800}}
.bizbox{{background:var(--red);color:#fff;border-radius:11px;padding:9px 15px;display:flex;flex-direction:column;line-height:1.3}}
.bizbox b{{font-size:18px;font-variant-numeric:tabular-nums}} .bizbox .lab{{font-size:11px;opacity:.92}}
.hero-foot{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-top:13px}}
.hero-foot .acc{{color:var(--green);font-size:12.5px;font-weight:700}}
.actions .lab{{font-size:11.5px;color:var(--muted);margin-right:4px}}
.act{{background:var(--red);color:#fff;border:none;border-radius:8px;padding:6px 11px;font-size:12px;font-weight:700;cursor:pointer;margin-left:4px;font-family:inherit}}
.act.ghost{{background:#fff;color:var(--ink);border:1px solid var(--line)}}
.act.sm{{padding:3px 9px;font-size:11px}}
.hero-evi{{margin-top:11px;font-size:11.5px;color:var(--muted);border-top:1px dashed #f0c4c4;padding-top:9px}}
.hero-evi b{{color:var(--red)}}
h2{{font-size:14.5px;margin:24px 0 9px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13px}}
th,td{{text-align:left;padding:9px 11px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--muted);font-weight:700;font-size:10.5px;text-transform:uppercase;letter-spacing:.3px;background:#fafbfe}}
tr:last-child td{{border-bottom:none}} tr.hot{{background:#fff7f7}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.chip{{display:inline-block;background:#eef1f7;color:#465569;font-size:10.5px;font-weight:700;padding:1px 6px;border-radius:6px;margin-right:3px}}
.pill{{display:inline-block;font-size:10px;font-weight:800;padding:2px 7px;border-radius:999px;margin-right:3px}}
.pill.red{{background:var(--red-bg);color:var(--red)}} .pill.amber{{background:var(--amber-bg);color:var(--amber)}}
.pill.xs{{font-size:9.5px;padding:1px 6px}}
.bar{{position:relative;height:7px;background:#e9edf5;border-radius:5px;margin:3px 0}}
.bar .fill{{position:absolute;left:0;top:0;height:100%;background:#cbd5e8;border-radius:5px}}
.bar .lowmark{{position:absolute;top:-3px;width:3px;height:13px;background:var(--red);border-radius:2px;transform:translateX(-1px)}}
code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11.5px}}
.vs{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--brand);border-radius:12px;padding:13px 15px;margin:18px 0;font-size:12.5px}}
.vs b{{color:var(--brand-d)}}
.foot{{color:var(--muted);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}
.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class="wrap">
  <span class="logo">VIORA</span><h1>채널 가격 거버넌스 모니터</h1>
  <span class="sub">네이버 · 11번가 · 쿠팡*</span>
  <span class="demo" style="margin-left:auto">DEMO · 가상 데이터</span>
</div></header>
<div class="wrap">

  <div class="tagline">소비자 가격앱(폴센트류)이 안 하는 것 →
    <span>마켓 간 제품 통합 · <b style="color:var(--brand-d)">동일사업자 다중상호 적발</b> · 병행/허위정품 탐지 · 조치</span></div>

  <div class="caveat">
    <b>읽는 법(정직성).</b> ① 전부 <b>데모용 가상 데이터</b>(가상 브랜드 VIORA). ②
    <b>쿠팡*</b>은 실데이터 미연동(접근/법률 게이트) — 쿠팡 수치는 <b>참고치</b>이며 별표로 표기, 핵심 지표는
    <b>접근 가능 채널(네이버+11번가)</b> 기준을 병기합니다. ③ <b>동일사업자 적발은 사업자등록번호 기반으로 거의 결정적(near-100%)</b>,
    <b>마켓 간 제품 통합</b>은 held-out <b>~50%</b>의 어려운 문제(개선 중) — 둘의 신뢰도는 다릅니다.
  </div>

  <div class="kpis">
    <div class="tile ctx"><div class="big">{k['sku']}</div><div class="lab">모니터링 SKU</div></div>
    <div class="tile ctx"><div class="big">{k['listings']}</div><div class="lab">통합 리스팅</div></div>
    <div class="tile moat"><div class="big">{k['actionable']}</div><div class="lab">조치 필요 위반</div></div>
    <div class="tile moat"><div class="big">{k['multi_storefront_sellers']}</div><div class="lab">동일사업자 다중상호</div></div>
    <div class="tile moat"><div class="big">{k['gray_listings']}<span class="xs">*쿠팡</span></div><div class="lab">병행수입 리스팅</div></div>
    <div class="tile"><div class="big">{k['undercut_accessible']}/{k['sku']}</div><div class="lab">공식가 침해 (접근가능 채널)</div></div>
  </div>

  <div class="monitor">
    <span>🔔 <b>모니터링 모드</b> — 1회 스냅샷이 아니라 지속 감시: 신규 미승인 셀러·신규 병행수입·추가 가격인하를
      <b>스캔마다 비교</b>해 임계 위반 시 이메일·카카오 알림 <span class="muted">(스케줄링은 데모에서 미구현)</span></span>
    <span class="impact">💰 추정 공식가 침해 익스포저 <b>{won(k['impact_won'])}</b>
      <span class="muted xs">/ 접근가능채널 · 1개 기준 · 구매량 미반영 추정</span></span>
  </div>

  {hero_block}

  <h2>🗂 외부 셀러 군집 — 사업자번호 기준 (조치 대상)</h2>
  <table>
    <tr><th>사업자번호</th><th>상호(들) / 위반</th><th>마켓</th><th>리스팅</th><th>평균할인</th><th>조치</th></tr>
    {''.join(srows)}
  </table>
  <p class="sub" style="margin:6px 2px">★ 셀러 식별은 사업자등록번호 <b>GROUP BY</b> — 마켓이 공개하는 필드라 거의 결정적입니다.
    실데이터에서 번호가 없거나 분할되면 주소·연락처·배송지 등 퍼지 신호로 보완합니다.</p>

  <h2>📦 제품별 — 공식가 라인 vs 마켓 최저가 (보조 근거)</h2>
  <table>
    <tr><th>제품 (마켓 간 통합)</th><th>공식가</th><th>마켓 최저가</th><th>침해</th></tr>
    {''.join(prows)}
  </table>
  <p class="sub" style="margin:6px 2px">막대 = 최저가/공식가 비율 · <span style="color:var(--red)">빨간 표식</span> = 마켓 최저가(왼쪽일수록 침해 큼) ·
    위에서부터 <b>위반 심각도순</b>(병행수입 &gt; 허위정품/다중상호 &gt; 미승인 저가). 데모는 6 SKU — 엔진은 전체 카탈로그(수백~수천 SKU)로 확장됩니다.</p>

  <div class="vs">
    <b>폴센트와 뭐가 다른가</b> — 소비자 딜헌팅 앱은 설계상 <b>한 리스팅</b>의 가격을 따라갈 뿐입니다(싸게 사기).
    이 화면은 브랜드 기준으로 <b>같은 상품을 마켓 간 하나로 통합</b>하고, <b>여러 상호 뒤 동일 사업자</b>를 적발하며,
    병행/허위'정품'을 분류해 <b>조치(경고·신고·증거패킷)</b>까지 연결합니다 — 가격을 보는 게 아니라 채널을 거버넌스합니다.
  </div>

  <p class="foot">
    <b>데모 / 정직성.</b> 전부 가상 데이터(VIORA). 화면의 깔끔한 통합은 <b>이 합성 셋 기준 best-case</b>이며,
    실제 운영의 마켓 간 매칭 정확도는 <b>held-out ~50%</b>(개선 중) — 동일사업자 적발(사업자번호 기반)과는 신뢰도가 다릅니다.
    쿠팡*은 접근/법률 게이트 통과 후 단계적 합류. 실데이터에서 가장 먼저 깨질 수 있는 곳: <b>용량/세트/번들 변형</b>
    (30ml↔50ml, 1+1·기획세트)과 <b>사업자번호 누락·분할</b> — 운영 엔진은 제목+속성 매칭 + 퍼지 사업자 신호로 보완 예정.
  </p>
</div></body></html>"""


def main():
    data = load()
    A = analyze(data["listings"])
    k = A["kpis"]

    out_dir = os.path.join(HERE, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "brand_demo.html"), "w", encoding="utf-8") as f:
        f.write(render(A))

    result = {
        "brand": data["_meta"]["brand"], "kpis": k,
        "products": [{"name": p["name"], "official_price": p["official_price"],
                      "lowest_price": p["lowest"]["price"], "lowest_seller": p["lowest"]["seller_name"],
                      "lowest_marketplace": p["lowest"]["marketplace"],
                      "undercut_pct": round(p["undercut_pct"], 1),
                      "accessible_undercut_pct": round(p["acc_undercut_pct"], 1),
                      "biz_count": p["biz_count"], "storefront_count": p["storefront_count"],
                      "marketplaces": p["marketplaces"]} for p in A["products"]],
        "seller_clusters": [{kk: s[kk] for kk in ("biz_reg_no", "storefront_names", "marketplaces",
                             "listing_count", "is_official", "multi_storefront", "avg_discount_pct",
                             "has_gray", "false_authentic")} for s in A["sellers"]],
    }
    with open(os.path.join(out_dir, "brand_demo_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("=" * 62)
    print(" VIORA 브랜드 데모 (네이버·11번가·쿠팡*)")
    print("=" * 62)
    print(f" SKU {k['sku']} · 리스팅 {k['listings']} · 클러스터 {len(A['run']['clusters'])}")
    print(f" 조치 필요 위반 {k['actionable']} · 동일사업자 다중상호 {k['multi_storefront_sellers']} · 병행수입 {k['gray_listings']}(쿠팡)")
    print(f" 공식가 침해: 접근가능 {k['undercut_accessible']}/{k['sku']} (최대 −{k['deepest_accessible']:.0f}%)  |  "
          f"쿠팡 포함 {k['undercut_all']}/{k['sku']} (최대 −{k['deepest_all']:.0f}%)")
    print("-" * 62)
    for s in A["multi"]:
        acc = s["accessible_storefronts"]
        print(f" ⚠ {s['biz_reg_no']}: {' / '.join(s['storefront_names'])} "
              f"({'+'.join(mkt(m) for m in s['marketplaces'])}) 평균 −{s['avg_discount_pct']:.0f}% "
              f"· 접근가능채널 상호 {len(acc)}")
    print("=" * 62)
    print(" outputs/brand_demo.html, brand_demo_result.json 생성")


if __name__ == "__main__":
    main()
