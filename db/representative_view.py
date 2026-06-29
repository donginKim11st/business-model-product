#!/usr/bin/env python3
"""번들 representative(정형 큐레이션) → 셀러뷰 / 고객뷰 두 화면으로 투영.

같은 representative 데이터를 청중에 맞게 다르게 보여준다:
  · seller_view  : 분석 화면 — dim·coverage·lift·rank·언급수까지. "이 카테고리에서 N% 번들이 언급" 등
                   판단근거(셀러가 자기 상품 포지셔닝/보완점을 보는 용도).
  · customer_view: 쇼핑 화면 — 고객 친화 섹션(좋아요/맛·식감/활용/참고)으로 재구성, 내부 수치(coverage/
                   lift/rank/dim_path) 숨김, 근거수는 '실제 후기 N건' 소셜프루프로 환산, 약점은 부드럽게,
                   셀러 전용(타사 비교 등)은 비노출.

representative 의 dim 분류는 category_rank.py 산출을 그대로 쓰고(추출/랭킹 불변), 여기선 표현만 바꾼다.

  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/representative_view.py --html rep_views.html [--per-cat 1]
  (모듈로 import 해서 seller_view(rep)/customer_view(rep) 함수만 써도 됨 — 앱/API 서빙용.)
"""
import os
import sys
import html
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import load_mongo  # walk_points, dim_label


def taxonomy_to_dims(tax, per_dim=3, max_dims=6):
    """nested taxonomy(youtube/raw block) → representative.dims 형태. customer_view 에 바로 투입 가능."""
    dims = []
    for dim_path, pts in load_mongo.walk_points(tax or {}):
        if not pts:
            continue
        best = sorted(pts, key=lambda p: -(p.get("cited_examples") or 0))[:per_dim]
        dims.append({"dim": dim_path, "label": load_mongo.dim_label(dim_path),
                     "points": [{"point": p.get("point"), "cited_examples": p.get("cited_examples") or 0,
                                 "evidence": p.get("evidence") or []} for p in best]})
    dims.sort(key=lambda d: -sum(p["cited_examples"] for p in d["points"]))
    return dims[:max_dims]

# dim_path → 고객 섹션. (섹션키, 정렬우선, 이모지, 제목, 셀러전용여부)
# 셀러전용=True 면 고객뷰에서 숨김. context.* 는 '이렇게 즐겨요'로 통합.
SECTIONS = [
    ("good",    "👍", "이런 점이 좋아요"),
    ("taste",   "😋", "맛·식감"),
    ("spec",    "📋", "제품 특징"),
    ("pack",    "📦", "용량·보관·가격"),
    ("use",     "🎯", "이렇게 즐겨요"),
    ("gift",    "🎁", "선물로도"),
    ("caveat",  "💡", "구매 전 참고하세요"),
]
SECTION_TITLE = {k: (emoji, title) for k, emoji, title in SECTIONS}
SECTION_ORDER = {k: i for i, (k, *_ ) in enumerate(SECTIONS)}


def section_of(dim):
    """dim_path → (고객 섹션키 | None=고객뷰 숨김)."""
    d = dim or ""
    if d.startswith("verdict.strengths"):
        return "good"
    if d.startswith("verdict.weaknesses") or d.startswith("context.why.negative_concern"):
        return "caveat"
    if d.startswith("verdict.compare"):
        return None                                   # 타사 비교 = 셀러 전용, 고객 비노출
    if d.startswith("verdict.trust"):
        return "spec"                                 # 원산지/인증 = 제품 특징으로
    if d.startswith("aspect.taste") or d.startswith("aspect.texture") or d.startswith("aspect.sensory"):
        return "taste"
    if d.startswith("aspect.size") or d.startswith("aspect.care") or d.startswith("aspect.price_range"):
        return "pack"
    if d.startswith("aspect"):                        # spec/routine 등
        return "spec"
    if d.startswith("context.gift"):
        return "gift"
    if d.startswith("context"):                       # who/when/where/why(positive) 통합
        return "use"
    return "use"


def _mentions(points):
    return sum(len(p.get("evidence") or []) or (p.get("cited_examples") or 0) for p in points)


def seller_view(rep):
    """분석 화면용 구조 — 정형 신호 그대로."""
    rep = rep or {}
    dims = []
    for d in rep.get("dims") or []:
        dims.append({
            "label": d.get("label"), "dim": d.get("dim"), "rank": d.get("rank"),
            "coverage": d.get("coverage"), "lift": d.get("lift"),
            "mentions": _mentions(d.get("points") or []),
            "points": [{"text": p.get("point"), "cited": p.get("cited_examples") or 0,
                        "evidence": p.get("evidence") or []} for p in (d.get("points") or [])],
        })
    return {"category": rep.get("category"), "low_confidence": rep.get("low_confidence", False),
            "generated_at": rep.get("generated_at"), "dims": dims}


def customer_view(rep, max_per_section=3, max_sections=5):
    """쇼핑 화면용 구조 — 친화 섹션·소셜프루프, 내부 수치 숨김.
    headline=대표 강점 한 줄, sections=청중 친화 묶음, review_count=근거 합(소셜프루프)."""
    rep = rep or {}
    buckets = {}                                      # 섹션키 -> [(text, mentions)]
    total_ev = 0
    headline = None
    seen = set()                                      # 같은 문장이 여러 섹션에 중복되지 않게(강점∩맛 등)
    for d in rep.get("dims") or []:
        sec = section_of(d.get("dim"))
        if sec is None:                               # 셀러 전용 → 고객뷰 제외
            continue
        for p in (d.get("points") or []):
            text = (p.get("point") or "").strip()
            if not text:
                continue
            evlist = p.get("evidence") or []
            ev = len(evlist) or (p.get("cited_examples") or 0)
            if headline is None and sec == "good":
                headline = text
            norm = text.lower().replace(" ", "")
            if norm in seen:                          # 중복 문장 스킵(소셜프루프 카운트는 1회만)
                continue
            seen.add(norm)
            total_ev += ev
            # 근거 출처(원문 URL+인용) — 광고 아닌 것 우선, 포인트당 최대 3개
            evs = []
            for e in sorted(evlist, key=lambda x: 1 if x.get("is_ad") else 0):
                if e.get("url"):
                    evs.append({"src": e.get("source"), "url": e.get("url"),
                                "quote": (e.get("quote") or "")[:90]})
                if len(evs) >= 3:
                    break
            buckets.setdefault(sec, []).append({"text": text, "mentions": ev, "evs": evs})
    # 섹션 정렬 + 컷
    sections = []
    for key in sorted(buckets, key=lambda k: SECTION_ORDER.get(k, 99)):
        emoji, title = SECTION_TITLE[key]
        items = sorted(buckets[key], key=lambda x: -x["mentions"])[:max_per_section]
        sections.append({"key": key, "emoji": emoji, "title": title, "items": items})
    if headline is None and sections:                 # 강점이 없으면 첫 섹션 첫 항목을 헤드라인으로
        headline = sections[0]["items"][0]["text"]
    return {"headline": headline, "sections": sections[:max_sections],
            "review_count": total_ev, "low_confidence": rep.get("low_confidence", False)}


# ── HTML 데모(좌: 셀러뷰 / 우: 고객뷰) ──────────────────────────────────────
def _esc(s):
    return html.escape(str(s or ""))


def render_html(rows):
    """rows: [{uid, keyword, category, rep}] → 좌우 비교 HTML."""
    cards = []
    for r in rows:
        sv = seller_view(r["rep"]); cv = customer_view(r["rep"])
        # 셀러 패널
        s_rows = []
        for d in sv["dims"]:
            pts = "".join(f"<li>{_esc(p['text'])} <span class=cite>· 근거 {len(p['evidence'])}</span></li>"
                          for p in d["points"])
            s_rows.append(
                f"<div class=dim><div class=dh><b>#{d['rank']} {_esc(d['label'])}</b>"
                f"<span class=metric>coverage {d['coverage']:.0%} · lift ×{(d['lift'] or 0):.1f} · 언급 {d['mentions']}</span>"
                f"</div><ul>{pts}</ul></div>")
        seller = "".join(s_rows) or "<div class=empty>대표 데이터 없음</div>"
        # 고객 패널
        c_secs = []
        for s in cv["sections"]:
            items = "".join(f"<li>{_esc(it['text'])}</li>" for it in s["items"])
            c_secs.append(f"<div class=sec><div class=st>{s['emoji']} {_esc(s['title'])}</div><ul>{items}</ul></div>")
        cust_body = "".join(c_secs) or "<div class=empty>표시할 내용 없음</div>"
        head = f"<div class=headline>“{_esc(cv['headline'])}”</div>" if cv["headline"] else ""
        proof = f"<div class=proof>🗣️ 실제 후기 {cv['review_count']}건에서 언급된 내용</div>" if cv["review_count"] else ""
        lc = " <span class=lc>※ 표본 적음</span>" if sv["low_confidence"] else ""
        cards.append(f"""
        <div class=card>
          <div class=ctitle>{_esc(r['keyword'])} <span class=cat>{_esc(r['category'])}{lc}</span> <span class=uid>{_esc(r['uid'])}</span></div>
          <div class=cols>
            <div class=col><div class="lab seller">🏷️ 셀러 화면 (분석)</div>{seller}</div>
            <div class=col><div class="lab cust">🛒 고객 화면 (쇼핑)</div>{head}{cust_body}{proof}</div>
          </div>
        </div>""")
    css = """
    body{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:#0f1115;color:#e6e8eb;margin:0;padding:24px;}
    h1{font-size:20px;margin:0 0 4px} .sub{color:#9aa3af;font-size:13px;margin-bottom:20px}
    .card{background:#171a21;border:1px solid #262b36;border-radius:14px;padding:16px 18px;margin-bottom:18px}
    .ctitle{font-size:16px;font-weight:700;margin-bottom:12px}
    .cat{color:#7dd3fc;font-size:12px;font-weight:600;margin-left:6px}.uid{color:#5b6472;font-size:11px;font-weight:400}
    .lc{color:#f59e0b}
    .cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
    @media(max-width:780px){.cols{grid-template-columns:1fr}}
    .col{background:#0f1218;border:1px solid #222732;border-radius:10px;padding:12px 14px}
    .lab{font-size:12px;font-weight:700;letter-spacing:.3px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #232936}
    .seller{color:#a3b3c9}.cust{color:#7ee0a0}
    .dim{margin-bottom:10px}.dh{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
    .dh b{font-size:13px}.metric{color:#6b7686;font-size:11px;white-space:nowrap}
    .dim ul,.sec ul{margin:4px 0 0;padding-left:18px}.dim li,.sec li{font-size:13px;margin:3px 0;color:#cbd2db}
    .cite{color:#5b6472;font-size:11px}
    .headline{font-size:15px;font-weight:600;color:#eafff1;background:#15241b;border-radius:8px;padding:8px 12px;margin-bottom:10px}
    .sec{margin-bottom:10px}.st{font-size:13px;font-weight:700;color:#a7f3c8;margin-bottom:2px}
    .sec li{color:#d7dde5}
    .proof{margin-top:8px;color:#86efac;font-size:12px;background:#13201a;border-radius:6px;padding:6px 10px}
    .empty{color:#5b6472;font-size:12px}
    """
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>대표 인사이트 — 셀러뷰 vs 고객뷰</title><style>{css}</style></head><body>"
            f"<h1>대표 비정형 인사이트 — 같은 데이터, 두 화면</h1>"
            f"<div class=sub>좌: 셀러 분석 화면(coverage·lift·언급수) · 우: 고객 쇼핑 화면(친화 섹션·소셜프루프, 내부 수치 숨김)</div>"
            f"{''.join(cards)}</body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="rep_views.html")
    ap.add_argument("--per-cat", type=int, default=1, help="카테고리당 표본 번들 수")
    ap.add_argument("--categories", default=None, help="콤마구분 카테고리 화이트리스트(없으면 전체)")
    args = ap.parse_args()

    from pymongo import MongoClient
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]

    wl = set(c.strip() for c in args.categories.split(",")) if args.categories else None
    rows = []
    cats = db.products.distinct("representative.category")
    for cat in cats:
        if not cat or (wl and cat not in wl):
            continue
        cur = list(db.products.find(
            {"representative.category": cat, "representative.dims.1": {"$exists": True}},
            {"_id": 1, "keyword": 1, "representative": 1}).limit(60))
        cur.sort(key=lambda p: -len((p.get("representative") or {}).get("dims") or []))
        for p in cur[:args.per_cat]:
            rows.append({"uid": p["_id"], "keyword": p.get("keyword"),
                         "category": cat, "rep": p.get("representative")})

    with open(args.html, "w", encoding="utf-8") as f:
        f.write(render_html(rows))
    print(f"HTML 생성 → {args.html} · 번들 {len(rows)}개 · 카테고리 {len(set(r['category'] for r in rows))}종")


if __name__ == "__main__":
    main()
