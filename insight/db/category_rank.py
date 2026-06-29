#!/usr/bin/env python3
"""카테고리 속성 랭킹 + 번들 '대표 비정형 데이터' materialize — 주간 배치(서빙 레이어).

문제: 트리(1차/2차/3차)로 속성별 인사이트를 '전부' 뽑으니, 고객이 안 봐도 될 희소 속성
(context.who.household, when.scene, trust.* 등 극소수 카탈로그에만 있는 것)까지 노출된다.

해법(추출은 그대로 보존, 노출만 큐레이션):
  카테고리(category_l1=DISP_CTGR1_NM) → 그 안의 카탈로그(번들 base + 변형) 전체를 훑어
  속성(dim_path)별로 '몇 개 카탈로그가 언급했나(coverage)'와 'cited_examples 합'을 집계해 랭킹.
  → category_attribute_rank 컬렉션에 카테고리별 속성 랭킹 저장.
  하이브리드 선정: 상위 N(기본 5) + 최소 커버리지 가드(기본 0.20) 둘 다 만족하는 dim 만 '대표'.
  → 각 번들 product 에 representative 필드 materialize(그 번들이 실제 가진 point 중 대표 dim만,
     cited_examples 순 상위 per-dim). taxonomy(원천)는 절대 건드리지 않음 → 가역·안전.

추출 파이프라인과 독립이라(이미 적재된 Mongo 만 읽음) 주 1회 배치로 돌리면 누적 인사이트가 반영된다.

  MONGO_URI="mongodb://localhost:47017/?directConnection=true" \
    python3 db/category_rank.py [--top-n 5] [--min-coverage 0.2] [--per-dim 3] [--dry-run]
"""
import os
import sys
import argparse
from collections import defaultdict, Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import load_mongo                       # walk_points, dim_label 재사용
from pymongo import MongoClient, UpdateOne

walk_points = load_mongo.walk_points
dim_label = load_mongo.dim_label


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cat_key(d):
    """랭킹 grouping 키 — canonical(category_l1) 우선, 없으면 coarse(category)."""
    return d.get("category_l1") or d.get("category") or "(미분류)"


def doc_dims(doc):
    """이 카탈로그 doc 의 {dim_path: [points]} (taxonomy 워킹). 빈 dim 은 제외."""
    out = {}
    for dim_path, pts in walk_points((doc.get("taxonomy") or {})):
        if pts:
            out[dim_path] = pts
    return out


def aggregate(db, coverage_by="bundle"):
    """pass1: 카테고리별 dim 통계 집계(스트리밍).

    coverage_by='bundle'(기본): 한 dim 을 '몇 개 번들(base+변형 통틀어)이 언급했나' → 변형 델타가
      일반 속성(맛/식감)을 희석하는 문제를 피하고 '카테고리 내 N% 상품이 가진 속성'에 충실.
    coverage_by='catalog': 변형 포함 모든 카탈로그 doc 기준(원문 표현 그대로).
    cite_sum/point_count 는 항상 카탈로그 전체 합(언급량 신호, 동점 tiebreak)."""
    proj = {"_id": 1, "parent_uid": 1, "category_l1": 1, "category": 1, "type": 1, "taxonomy": 1}
    stats = defaultdict(lambda: {"n_catalogs": 0, "n_scored": 0, "n_bundles": 0, "n_bundles_scored": 0,
                                 "dims": defaultdict(lambda: {"cov_docs": 0, "cov_bundles": 0,
                                                              "cite_sum": 0, "point_count": 0})})
    # 번들 단위 dim 집합(중복 카운트 방지) — cat -> bundle -> set(dim)
    bundle_dims = defaultdict(lambda: defaultdict(set))
    bundle_seen = defaultdict(set)       # cat -> set(bundle_uid)  (분모용 distinct)
    for doc in db.products.find({}, proj):
        cat = cat_key(doc)
        c = stats[cat]
        c["n_catalogs"] += 1
        is_variant = doc.get("type") == "variant"
        bundle_uid = doc.get("parent_uid") if is_variant else doc["_id"]
        if not is_variant:
            c["n_bundles"] += 1
        bundle_seen[cat].add(bundle_uid)
        dims = doc_dims(doc)
        if dims:
            c["n_scored"] += 1
        for dim_path, pts in dims.items():
            ds = c["dims"][dim_path]
            ds["cov_docs"] += 1
            ds["point_count"] += len(pts)
            ds["cite_sum"] += sum((p.get("cited_examples") or 0) for p in pts)
            bundle_dims[cat][bundle_uid].add(dim_path)
    # 번들 커버리지 환산
    for cat, c in stats.items():
        scored_bundles = {b for b, ds in bundle_dims[cat].items() if ds}
        c["n_bundles_scored"] = len(scored_bundles)
        for b, ds in bundle_dims[cat].items():
            for dim_path in ds:
                c["dims"][dim_path]["cov_bundles"] += 1
        c["_coverage_by"] = coverage_by
    return stats


def global_coverage(stats, by_bundle):
    """전역 baseline: dim 별 (그 dim 가진 번들 합) / (추출된 번들 합). lift 분모."""
    num = defaultdict(int); den = 0
    for c in stats.values():
        den += (c["n_bundles_scored"] if by_bundle else c["n_scored"])
        for dim, ds in c["dims"].items():
            num[dim] += (ds["cov_bundles"] if by_bundle else ds["cov_docs"])
    den = den or 1
    return {dim: n / den for dim, n in num.items()}


def rank_category(c, top_n, min_coverage, rank_by="hybrid", gcov=None,
                  min_support=2, min_bundles=3, pin_dims=()):
    """dim 랭킹. rank_by:
       coverage = 가장 많이 언급된 속성(전역에서 흔한 강점/맛/사양이 상위).
       lift     = 전역 대비 이 카테고리에서 '두드러진' 속성(카테고리 차별화: 식감/사용맥락/용량).
       hybrid   = coverage×lift (흔하면서도 이 카테고리에 특징적인 속성).
    top_dims = 정렬 상위 N ∩ coverage≥min_coverage ∩ 절대지지(번들수)≥min_support.
    pin_dims = 이 prefix(예: verdict.strengths)는 top-N 컷을 면제하고 항상 포함(품질 가드는 유지) →
      hybrid 가 강점을 밀어내도 고객뷰 '좋아요' 헤드라인이 비지 않게.
    소수 번들 카테고리(n_bundles_scored<min_bundles)는 coverage가 100%로 붕괴해 변별력이 없으므로
    low_confidence 로 표시하고, 그 경우 min_support 를 1로 완화(없는 것보단 보여줌)."""
    by_bundle = c.get("_coverage_by", "bundle") == "bundle"
    low_conf = c["n_bundles_scored"] < min_bundles
    eff_support = 1 if low_conf else min_support
    pin_dims = tuple(pin_dims or ())
    denom = (c["n_bundles_scored"] if by_bundle else c["n_scored"]) or 1
    gcov = gcov or {}
    ranked = []
    for dim, ds in c["dims"].items():
        cov_n = ds["cov_bundles"] if by_bundle else ds["cov_docs"]
        coverage = cov_n / denom
        lift = coverage / (gcov.get(dim) or 1e-9)      # 전역 대비 과대표 정도
        ranked.append({"dim": dim, "label": dim_label(dim),
                       "coverage": round(coverage, 4), "lift": round(lift, 3),
                       "cov_docs": ds["cov_docs"], "cov_bundles": ds["cov_bundles"],
                       "cite_sum": ds["cite_sum"], "point_count": ds["point_count"]})
    if rank_by == "lift":
        key = lambda r: (r["lift"], r["coverage"])
    elif rank_by == "hybrid":
        key = lambda r: (r["coverage"] * r["lift"], r["coverage"])
    else:                                              # coverage(기본)
        key = lambda r: (r["coverage"], r["cite_sum"])
    ranked.sort(key=key, reverse=True)
    top = []
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
        support = r["cov_bundles"] if by_bundle else r["cov_docs"]
        passes_guard = r["coverage"] >= min_coverage and support >= eff_support
        pinned = bool(pin_dims) and r["dim"].startswith(pin_dims)   # top-N 컷 면제(가드는 유지)
        r["pinned"] = pinned and passes_guard and r["rank"] > top_n
        if passes_guard and (r["rank"] <= top_n or pinned):
            top.append(r["dim"])
    return ranked, top, low_conf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=int(os.environ.get("RANK_TOP_N", "5")))
    ap.add_argument("--min-coverage", type=float, default=float(os.environ.get("RANK_MIN_COV", "0.2")))
    ap.add_argument("--per-dim", type=int, default=int(os.environ.get("RANK_PER_DIM", "3")),
                    help="번들 대표에서 dim 당 노출할 point 최대수")
    ap.add_argument("--coverage-by", choices=["bundle", "catalog"],
                    default=os.environ.get("RANK_COVERAGE_BY", "bundle"),
                    help="coverage 분모: bundle(기본, 변형 희석 방지) | catalog(변형 포함 doc)")
    ap.add_argument("--rank-by", choices=["coverage", "lift", "hybrid"],
                    default=os.environ.get("RANK_BY", "hybrid"),
                    help="hybrid(기본, 흔함×차별화) | coverage(가장 많이 언급) | lift(순수 차별화)")
    ap.add_argument("--min-support", type=int, default=int(os.environ.get("RANK_MIN_SUPPORT", "2")),
                    help="대표 dim 의 최소 절대지지(그 dim 가진 번들 수). 소수 카테고리는 1로 완화")
    ap.add_argument("--min-bundles", type=int, default=int(os.environ.get("RANK_MIN_BUNDLES", "3")),
                    help="이 미만 번들 카테고리는 low_confidence(coverage 100% 붕괴 변별력 없음)")
    ap.add_argument("--pin-dims", default=os.environ.get("RANK_PIN_DIMS", "verdict.strengths"),
                    help="top-N 컷과 무관하게 항상 대표에 포함할 dim prefix(콤마구분). 빈 문자열이면 끔")
    ap.add_argument("--dry-run", action="store_true", help="DB 쓰기 없이 카테고리별 랭킹만 출력")
    args = ap.parse_args()
    pin_dims = tuple(p.strip() for p in args.pin_dims.split(",") if p.strip())

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]
    params = {"top_n": args.top_n, "min_coverage": args.min_coverage, "per_dim": args.per_dim,
              "coverage_by": args.coverage_by, "rank_by": args.rank_by,
              "min_support": args.min_support, "min_bundles": args.min_bundles,
              "pin_dims": list(pin_dims)}

    print(f"pass1: 카테고리 속성 집계 중 (top_n={args.top_n}, min_cov={args.min_coverage}, "
          f"coverage_by={args.coverage_by}, rank_by={args.rank_by}) ...")
    stats = aggregate(db, coverage_by=args.coverage_by)
    gcov = global_coverage(stats, args.coverage_by == "bundle")
    gen = now_iso()

    cat_top = {}                          # category -> [{dim,label,rank,coverage,lift}]
    cat_lowconf = {}                       # category -> bool
    rank_docs = []
    for cat, c in sorted(stats.items(), key=lambda kv: -kv[1]["n_catalogs"]):
        ranked, top, low_conf = rank_category(c, args.top_n, args.min_coverage, args.rank_by, gcov,
                                              args.min_support, args.min_bundles, pin_dims)
        cat_lowconf[cat] = low_conf
        cat_top[cat] = [{"dim": r["dim"], "label": r["label"], "rank": r["rank"],
                         "coverage": r["coverage"], "lift": r["lift"]} for r in ranked if r["dim"] in top]
        rank_docs.append({"_id": cat, "n_bundles": c["n_bundles"], "n_catalogs": c["n_catalogs"],
                          "n_scored": c["n_scored"], "n_bundles_scored": c["n_bundles_scored"],
                          "low_confidence": low_conf, "params": params, "generated_at": gen,
                          "top_dims": top, "ranked_dims": ranked})

    # 출력 미리보기(상위 카테고리들)
    show_lift = args.rank_by != "coverage"
    for rd in rank_docs[:12]:
        head = " · ".join(
            (f"{r['label']}({r['coverage']:.0%}×{r['lift']:.1f})" if show_lift
             else f"{r['label']}({r['coverage']:.0%})")
            for r in rd["ranked_dims"][:6])
        star = " | 대표:" + ",".join(dim_label(d) for d in rd["top_dims"]) if rd["top_dims"] else ""
        lc = " ⚠소수번들" if rd["low_confidence"] else ""
        print(f"  [{rd['_id'][:22]:22}] 카탈로그 {rd['n_catalogs']:5d}(추출 {rd['n_scored']:5d}){lc} → {head}{star}")
    print(f"... 카테고리 {len(rank_docs)}종")

    if args.dry_run:
        print("dry-run — DB 쓰기 없음.")
        return

    # category_attribute_rank 갱신: 카테고리별 멱등 upsert(빈 윈도우 없음) 후 이전 run 잔존분만 삭제.
    if rank_docs:
        db.category_attribute_rank.bulk_write(
            [UpdateOne({"_id": rd["_id"]}, {"$set": rd}, upsert=True) for rd in rank_docs],
            ordered=False)
    db.category_attribute_rank.delete_many({"generated_at": {"$ne": gen}})   # 사라진 카테고리 정리
    db.category_attribute_rank.create_index("top_dims")

    # pass2: 번들 representative materialize.
    # 번들 = base(또는 standalone) + 그 변형들. 대표 point 는 번들에 속한 '카탈로그들'(base+변형)의
    # 합집합에서 대표 dim 별로 cited_examples 상위 per_dim 만. 식품(base에 인사이트)·신발(변형에 인사이트)
    # 어느 모델이든 동작. 대표 dim 에 해당하는 point 만 모으므로 메모리도 작다.
    print("pass2: 번들 representative 집계 중 (base+변형 합집합) ...")
    proj = {"_id": 1, "parent_uid": 1, "category_l1": 1, "category": 1, "type": 1,
            "keyword": 1, "taxonomy": 1}
    bundle_pts = defaultdict(lambda: defaultdict(list))   # bundle_uid -> dim -> [points]
    bundle_base = {}                                       # bundle_uid -> base doc(메타)
    for doc in db.products.find({}, proj):
        is_variant = doc.get("type") == "variant"
        bundle_uid = doc.get("parent_uid") if is_variant else doc["_id"]
        if not is_variant:
            bundle_base[bundle_uid] = doc
        tops = {t["dim"]: t for t in (cat_top.get(cat_key(doc)) or [])}
        if not tops:
            continue
        for dim, pts in doc_dims(doc).items():
            if dim in tops:
                bundle_pts[bundle_uid][dim].extend(pts)

    ops = []; n_bundle = 0; n_with_rep = 0
    for bundle_uid, base in bundle_base.items():
        n_bundle += 1
        tops = cat_top.get(cat_key(base)) or []
        dims_acc = bundle_pts.get(bundle_uid) or {}
        rep_dims = []
        for t in tops:                    # 카테고리 랭킹 순서 유지
            pts = dims_acc.get(t["dim"])
            if not pts:
                continue                  # 이 번들엔 그 대표 속성 point 없음 → 날조 금지, 스킵
            best = sorted(pts, key=lambda p: -(p.get("cited_examples") or 0))[:args.per_dim]
            rep_dims.append({"dim": t["dim"], "label": t["label"], "rank": t["rank"],
                             "coverage": t["coverage"], "lift": t.get("lift"),
                             "points": [{"point": p.get("point"),
                                         "cited_examples": p.get("cited_examples") or 0,
                                         "evidence": p.get("evidence") or []} for p in best]})
        rep = {"category": cat_key(base), "low_confidence": cat_lowconf.get(cat_key(base), False),
               "params": params, "generated_at": gen, "dims": rep_dims}
        if rep_dims:
            n_with_rep += 1
        ops.append(UpdateOne({"_id": bundle_uid}, {"$set": {"representative": rep}}))
        if len(ops) >= 1000:
            db.products.bulk_write(ops, ordered=False); ops = []
    if ops:
        db.products.bulk_write(ops, ordered=False)
    db.products.create_index("representative.dims.dim")

    print("=" * 64)
    print(f"완료 · 카테고리 {len(rank_docs)}종 → category_attribute_rank "
          f"· 번들 {n_bundle:,} 중 대표 채움 {n_with_rep:,} · {gen}")


if __name__ == "__main__":
    main()
