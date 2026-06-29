#!/usr/bin/env python3
"""
셀러 중심 보기 — '셀러명으로 그 구조를 뽑는다'.

제품 중심 대시보드(demo_brand.py)를 뒤집어, 셀러(상호) → 동일 사업자 → 모든 상호 →
내 카탈로그 전체에서의 행위(제품·마켓·가격·할인·위반·증거)를 한 번에 보여준다.

    python3 seller_view.py            # 전체 셀러 요약 + JSON/HTML 생성
    python3 seller_view.py 데일리코스   # 그 상호의 사업자 도시에를 콘솔 출력
"""
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from demo_brand import analyze, load, violation_type, mkt, won, VIO_LABEL, MKT
from pig.normalize import extract_attributes


def build(records):
    A = analyze(records)
    by_id = A["by_id"]
    attrs = {r["id"]: extract_attributes(r) for r in records}

    # listing -> product name (from the resolved product clusters)
    pname = {}
    for p in A["products"]:
        for lid in p["cluster"]:
            pname[lid] = p["name"]

    businesses = {}
    by_name = {}
    for sc in A["sellers"]:  # each seller cluster == one 사업자
        biz = sc["biz_reg_no"] or "미상"
        storefronts = {}
        for lid in sc["listing_ids"]:
            r = by_id[lid]
            v = violation_type(r, attrs[lid])
            detail = {
                "listing_id": lid, "product": pname.get(lid, r["title"]),
                "marketplace": r["marketplace"], "storefront": r["seller_name"],
                "price": r["price"], "official_price": r["official_price"],
                "discount_pct": round((r["official_price"] - r["price"]) / r["official_price"] * 100, 1)
                if r["official_price"] else 0,
                "is_official": r["is_official"],
                "violation": v, "violation_label": VIO_LABEL.get(v),
                "evidence": r["title"],  # 허위 '정품' 표기 등 그대로 보존
            }
            storefronts.setdefault((r["seller_name"], r["marketplace"]), []).append(detail)

        sf_list = [{"name": n, "marketplace": m, "listings": ls}
                   for (n, m), ls in storefronts.items()]
        sf_list.sort(key=lambda s: (s["name"], s["marketplace"]))
        products = sorted({d["product"] for sf in sf_list for d in sf["listings"]})

        biz_obj = {
            "biz_reg_no": biz, "is_official": sc["is_official"],
            "multi_storefront": sc["multi_storefront"],
            "storefront_names": sc["storefront_names"],
            "storefront_count": len(sc["storefront_names"]),
            "marketplaces": sc["marketplaces"],
            "accessible_storefronts": sc.get("accessible_storefronts", []),
            "product_count": len(products), "products": products,
            "listing_count": sc["listing_count"],
            "avg_discount_pct": round(sc["avg_discount_pct"], 1),
            "has_gray": sc["has_gray"], "false_authentic": sc["false_authentic"],
            "storefronts": sf_list,
        }
        businesses[biz] = biz_obj
        for n in sc["storefront_names"]:
            by_name[n] = biz

    return {"by_name": by_name, "businesses": businesses}


def _severity(b):
    return (3 if b["has_gray"] else 0) + (2 if b["multi_storefront"] else 0) + (1 if b["false_authentic"] else 0)


def lookup(view, query):
    """셀러명(상호) 또는 사업자번호로 도시에 1건 출력."""
    biz = view["by_name"].get(query) or (query if query in view["businesses"] else None)
    if not biz:
        cands = [n for n in view["by_name"] if query in n]
        print(f"'{query}' 셀러를 찾지 못했습니다." + (f" 혹시: {', '.join(cands)}" if cands else ""))
        print("등록된 상호:", " · ".join(sorted(view["by_name"])))
        return
    b = view["businesses"][biz]
    role = "공식" if b["is_official"] else ("동일사업자 다중상호" if b["multi_storefront"] else "외부 셀러")
    print("=" * 64)
    print(f" '{query}' →  사업자 {b['biz_reg_no']}  [{role}]")
    print("=" * 64)
    print(f" 상호 {b['storefront_count']}개: {' · '.join(b['storefront_names'])}")
    print(f" 마켓: {' · '.join(mkt(m) for m in b['marketplaces'])}"
          + (f"   (접근가능채널 상호 {len(b['accessible_storefronts'])})" if not b["is_official"] else ""))
    print(f" 제품 {b['product_count']}종 · 리스팅 {b['listing_count']} · 평균할인 −{b['avg_discount_pct']}%")
    flags = [t for t, on in [("병행수입", b["has_gray"]), ("허위정품", b["false_authentic"]),
                             ("다중상호", b["multi_storefront"])] if on]
    if flags:
        print(" 위반:", " / ".join(flags))
    print("-" * 64)
    for sf in b["storefronts"]:
        print(f" [{sf['name']} · {mkt(sf['marketplace'])}]")
        for d in sf["listings"]:
            tag = f" ⚠{d['violation_label']}" if d["violation_label"] else (" ✓공식" if d["is_official"] else "")
            print(f"   - {d['product']:24} {won(d['price']):>9} (공식 {won(d['official_price'])}, "
                  f"−{d['discount_pct']}%){tag}")
            if d["violation"] == "false_authentic":
                print(f"       증거: \"{d['evidence']}\"  ← 미승인인데 '정품' 표기")
    print("=" * 64)


# --------------------------- HTML (셀러 도시에) ---------------------------
def render(view):
    biz_items = sorted(view["businesses"].values(), key=lambda b: (b["is_official"], -_severity(b), -b["listing_count"]))
    cards = []
    for b in biz_items:
        if b["is_official"]:
            badge = "<span class='pill green'>공식</span>"
        elif b["multi_storefront"]:
            badge = "<span class='pill red'>동일사업자 다중상호</span>"
        else:
            badge = "<span class='pill'>외부 셀러</span>"
        if b["has_gray"]:
            badge += "<span class='pill amber'>병행수입</span>"
        if b["false_authentic"]:
            badge += "<span class='pill amber'>허위정품</span>"
        names = " · ".join(html.escape(n) for n in b["storefront_names"])
        rows = []
        for sf in b["storefronts"]:
            for d in sf["listings"]:
                if d["is_official"]:
                    vcell = "<span class='muted'>공식</span>"
                else:
                    vcell = f"<span class='pill amber xs'>{d['violation_label']}</span>" if d["violation_label"] in ("병행수입", "허위 '정품' 표기") else "<span class='muted'>미승인 저가</span>"
                rows.append(f"<tr><td>{html.escape(d['product'])}</td>"
                            f"<td>{html.escape(sf['name'])}<br><span class='sub'>{mkt(sf['marketplace'])}</span></td>"
                            f"<td class='num'>{won(d['price'])}</td>"
                            f"<td class='num'>{('−'+str(d['discount_pct'])+'%') if d['discount_pct']>0 else '공식가'}</td>"
                            f"<td>{vcell}</td></tr>")
        acc = (f" · <span class='muted'>접근가능채널 상호 {len(b['accessible_storefronts'])}</span>"
               if not b["is_official"] else "")
        action = "" if b["is_official"] else ("<div class='act-row'><button class='act'>⚠ 경고 발송</button>"
                  "<button class='act ghost'>🚩 마켓 신고</button><button class='act ghost'>⬇ 증거패킷</button></div>")
        cards.append(f"""
        <div class="card {'hot' if b['multi_storefront'] and not b['is_official'] else ''}">
          <div class="hd"><b>{names}</b> {badge}</div>
          <div class="meta">사업자 <code>{html.escape(b['biz_reg_no'])}</code> · 상호 {b['storefront_count']} ·
            제품 {b['product_count']}종 · 리스팅 {b['listing_count']} · 평균 −{b['avg_discount_pct']}%{acc}</div>
          <table><tr><th>제품</th><th>상호/마켓</th><th>판매가</th><th>침해</th><th>위반</th></tr>{''.join(rows)}</table>
          {action}
        </div>""")

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>VIORA · 셀러별 보기 (데모)</title>
<style>
:root{{--ink:#15181d;--muted:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--brand-d:#1e4fa8;
 --red:#d23b3b;--red-bg:#fdecec;--green:#1a9d57;--green-bg:#e6f7ee;--amber:#b9770b;--amber-bg:#fff6e6;--card:#fff;}}
*{{box-sizing:border-box}} body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;
 color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:920px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(244,246,250,.93);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:var(--brand);color:#fff;font-weight:900;padding:5px 10px;border-radius:8px;letter-spacing:1px}}
h1{{font-size:17px;margin:0;font-weight:800}} .demo{{background:var(--amber-bg);color:var(--amber);font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.lead{{font-size:13px;background:#fff;border:1px solid var(--line);border-left:4px solid var(--brand);border-radius:11px;padding:12px 14px;margin:18px 0}}
.lead b{{color:var(--brand-d)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px 16px;margin:13px 0}}
.card.hot{{border-color:#f0a3a3;background:#fff7f7}}
.hd{{font-size:15px;margin-bottom:5px}} .meta{{font-size:11.5px;color:var(--muted);margin-bottom:9px}}
code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11.5px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}} th,td{{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line)}}
th{{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.3px;font-weight:700;background:#fafbfe}}
tr:last-child td{{border-bottom:none}} td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.sub{{color:var(--muted);font-size:11px}} .muted{{color:var(--muted)}}
.pill{{display:inline-block;font-size:10px;font-weight:800;padding:2px 7px;border-radius:999px;margin-left:3px;background:#eef1f7;color:#465569}}
.pill.red{{background:var(--red-bg);color:var(--red)}} .pill.amber{{background:var(--amber-bg);color:var(--amber)}} .pill.green{{background:var(--green-bg);color:var(--green)}}
.pill.xs{{font-size:9.5px;padding:1px 6px;margin:0}}
.act-row{{margin-top:10px}} .act{{background:var(--red);color:#fff;border:none;border-radius:8px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer;margin-right:5px;font-family:inherit}}
.act.ghost{{background:#fff;color:var(--ink);border:1px solid var(--line)}}
.foot{{color:var(--muted);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}
</style></head><body>
<header><div class="wrap"><span class="logo">VIORA</span><h1>셀러별 보기 (사업자 도시에)</h1>
<span class="demo">DEMO · 가상 데이터</span></div></header>
<div class="wrap">
  <div class="lead">제품이 아니라 <b>셀러(사업자) 기준</b>으로 본 같은 구조 — 상호 하나를 누르면 그 뒤의 사업자,
    같은 사업자가 쓰는 다른 상호, 내 카탈로그 전체에서의 행위가 한 번에 나옵니다. (CLI: <code>python3 seller_view.py 데일리코스</code>)</div>
  {''.join(cards)}
  <p class="foot"><b>데모 / 정직성.</b> 가상 데이터(VIORA). 사업자 묶기는 <b>사업자등록번호 기준(거의 결정적)</b>,
    실데이터에서 번호 누락·분할 시 주소·연락처·배송지 등 퍼지 신호로 보완. 쿠팡*은 접근/법률 게이트 후 합류.</p>
</div></body></html>"""


def main():
    view = build(load()["listings"])
    if len(sys.argv) > 1:
        lookup(view, " ".join(sys.argv[1:]).strip())
        return

    out = os.path.join(HERE, "outputs")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "seller_view.json"), "w", encoding="utf-8") as f:
        json.dump(view, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out, "seller_view.html"), "w", encoding="utf-8") as f:
        f.write(render(view))

    print("=" * 60)
    print(" 셀러별 보기 — 사업자 요약")
    print("=" * 60)
    for b in sorted(view["businesses"].values(), key=lambda b: (b["is_official"], -_severity(b))):
        role = "공식" if b["is_official"] else ("★다중상호" if b["multi_storefront"] else "외부")
        print(f" {b['biz_reg_no']} [{role:5}] {' · '.join(b['storefront_names'])}"
              f"  → 제품{b['product_count']} 리스팅{b['listing_count']} 평균−{b['avg_discount_pct']}%")
    print("-" * 60)
    print(" 셀러명으로 조회:  python3 seller_view.py <상호>")
    print(" 예:              python3 seller_view.py 데일리코스")
    print(" outputs/seller_view.json, seller_view.html 생성")


if __name__ == "__main__":
    main()
