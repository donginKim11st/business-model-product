#!/usr/bin/env python3
"""identity 정형 팩트(outputs CSV) → insights_demo product 합류 (T2).

canonical join: insight 가 상품 우주(uid)를 정의하고, identity 가 정형 스핀
(brand/style_code/color/price/고시)을 만든다. 이 backfill 은 identity 산출 CSV 를
insight_uid 로 product 에 합류한다 — food_price_backfill 패턴(targeted $set)을 복제하므로
재적재(demo_load_trees 증분 skip)와 다른 backfill($set) 산출을 덮어쓰지 않는다.

합류 모양(Eng 리뷰):
  · per-SKU 정형 → products.catalogs[i].identity  (ctlg_no 매치, price_summary 와 동일 위치)
  · 상품 레벨    → products.identity = {brand, status, n_facts, fetched_at}
    status: pending(초기) | done(팩트 합류) | empty(씨앗됐으나 산출 없음=식품 과도기) | error

재개 안전: identity.status 있는 상품은 건너뜀(--refresh 로 재합류). 식품(추출기 없음)은
status:empty 로 마킹돼 재씨앗 루프를 돌지 않는다(youtube 패턴).

  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/identity_backfill.py --csv identity/outputs/all_brands.csv
"""
import os
import sys
import csv
import time
import argparse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))   # insight/db -> insight -> repo root
DEFAULT_CSV = os.path.join(REPO_ROOT, "identity", "outputs", "all_brands.csv")

# 카테고리 불가지(category-agnostic) 설계: 정형 컬럼 집합을 카테고리별로 하드코딩하지 않는다.
# 의류면 style_code/color/소재·제조국, 식품이면 원재료명/유통기한, 가전이면 모델/소비전력 …
# identity 가 산출한 CSV 의 컬럼을 그대로 수용한다. 아래 두 집합만 의미가 고정:
#   META_COLS  : 조인키/상품레벨 → per-SKU 서브독에서 제외
#   GOSI_HINT  : '법정 정보고시'로 알려진 컬럼명(있으면 gosi 로 묶음). 카테고리별로 다름 — 확장 가능.
# 그 외 모든 컬럼은 카테고리 불문 top-level 로 통과(passthrough) → 어떤 카테고리든 수용.
META_COLS = {"insight_uid", "ctlg_no", "brand"}
GOSI_HINT = {
    # 의류/신발
    "origin", "material", "mfg_date",
    # 식품
    "food_type", "ingredients", "manufacturer", "expiry", "storage",
    # 가전/일반
    "model_year", "power", "voltage", "capacity", "cert", "caution",
    # 화장품/생활
    "volume", "weight", "usage", "warning",
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(v):
    v = (v or "").strip() if isinstance(v, str) else v
    return v or None


def _row_sku_identity(row, fetched_at):
    """CSV row → per-SKU identity 서브독 (category-agnostic, column passthrough).

    META_COLS(조인키/brand) 제외 모든 비어있지 않은 컬럼을 수용한다. 어떤 카테고리의
    정형 스키마든 컬럼명 그대로 보관 → 카테고리별 분기/하드코딩 없음.
    GOSI_HINT 에 든 컬럼은 gosi 서브독으로 묶어 정리(나머지는 top-level)."""
    fields = {k: _clean(v) for k, v in row.items() if k not in META_COLS and _clean(v) is not None}
    gosi = {k: fields.pop(k) for k in list(fields) if k in GOSI_HINT}
    d = dict(fields)
    if gosi:
        d["gosi"] = gosi
    d.setdefault("source", "identity")
    d["fetched_at"] = fetched_at
    return d


def build_identity_update(catalogs, rows, fetched_at=None):
    """순수 함수: product 의 catalogs + identity rows → (새 catalogs, products.identity 서브독).

    catalogs: 기존 product.catalogs (list[dict]). in-place 변이하지 않고 복사본 반환.
    rows: 이 product(insight_uid)에 매칭된 identity CSV row 들(dict list).
    반환: (catalogs_after, identity_subdoc). rows 비면 status:empty.

    매칭: row.ctlg_no 가 catalog.ctlg_no 와 일치하면 그 SKU 에 identity 부착.
    ctlg_no 없는 row(의류 등 insight ctlg_no 미매핑)는 상품 레벨 brand 에만 기여.
    기존 catalog 의 다른 필드(price_summary, insight 등)는 보존(키 단위 set).
    T2 회귀 가드의 단위 검증 지점.
    """
    fetched_at = fetched_at or now_iso()
    cats = [dict(c) for c in (catalogs or [])]               # 얕은 복사(원본 미변이)
    by_ctlg = {str(c.get("ctlg_no")): c for c in cats if c.get("ctlg_no") is not None}
    n_facts = 0
    brand = None
    for row in rows or []:
        if not brand:
            brand = _clean(row.get("brand"))
        ctlg = _clean(str(row.get("ctlg_no"))) if row.get("ctlg_no") not in (None, "") else None
        if ctlg and ctlg in by_ctlg:
            by_ctlg[ctlg]["identity"] = _row_sku_identity(row, fetched_at)  # 키 단위 set → price_summary 등 보존
            n_facts += 1
        else:
            n_facts += 1                                     # 상품 레벨 기여(브랜드 등)
    if not (rows):
        ident = {"brand": None, "status": "empty", "n_facts": 0, "fetched_at": fetched_at}
    else:
        ident = {"brand": brand, "status": "done", "n_facts": n_facts, "fetched_at": fetched_at}
    return cats, ident


def read_identity_csv(path):
    """identity outputs CSV → {insight_uid: [row, ...]}. insight_uid 없는 row 는 무시(stamp 안 됨)."""
    by_uid = {}
    n = n_skip = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            uid = _clean(row.get("insight_uid"))
            if not uid:
                n_skip += 1
                continue
            by_uid.setdefault(uid, []).append(row)
            n += 1
    return by_uid, n, n_skip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=DEFAULT_CSV, help="identity 산출 CSV(insight_uid 컬럼 필요)")
    ap.add_argument("--limit", type=int, default=0, help="처리 product 수(0=전체)")
    ap.add_argument("--refresh", action="store_true", help="identity.status 있어도 재합류")
    ap.add_argument("--mark-empty", action="store_true",
                    help="CSV 에 없는 pending product 도 status:empty 로 마킹(씨앗 큐 드레인). "
                         "기본은 CSV 에 있는 product 만 처리.")
    ap.add_argument("--dry-run", action="store_true", help="DB 미수정, 대상만 출력")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        sys.exit(f"✗ CSV 없음: {args.csv} (T3 export_identity_seed + T4 identity 실행 후 생성)")
    by_uid, n_rows, n_skip = read_identity_csv(args.csv)
    print(f"identity CSV: {n_rows:,} row · {len(by_uid):,} product (insight_uid 없는 {n_skip} row 무시)")

    from pymongo import MongoClient
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]

    # 처리 대상: identity.status 없는(또는 --refresh) product.
    # 기본은 CSV 에 등장하는 uid 만(비용/명확성). --mark-empty 면 pending 전체 드레인.
    if args.mark_empty:
        q = {} if args.refresh else {"identity.status": {"$exists": False}}
        cur = db.products.find(q, {"_id": 1, "keyword": 1, "catalogs": 1})
        targets = list(cur)
    else:
        ids = list(by_uid.keys())
        q = {"_id": {"$in": ids}}
        if not args.refresh:
            q["identity.status"] = {"$exists": False}
        targets = list(db.products.find(q, {"_id": 1, "keyword": 1, "catalogs": 1}))
    if args.limit:
        targets = targets[:args.limit]
    print(f"합류 대상 product {len(targets):,}개 (refresh={args.refresh}, mark_empty={args.mark_empty}, "
          f"dry_run={args.dry_run})")
    print("=" * 64)

    t0 = time.time(); n_done = n_empty = 0
    for i, pkg in enumerate(targets, 1):
        uid = pkg["_id"]
        rows = by_uid.get(uid, [])
        if not rows and not args.mark_empty:
            continue
        cats_after, ident = build_identity_update(pkg.get("catalogs") or [], rows)
        if args.dry_run:
            print(f"  [{i}/{len(targets)}] {uid} {pkg.get('keyword','')[:24]} → "
                  f"status={ident['status']} n_facts={ident['n_facts']}")
        else:
            setdoc = {"identity": ident}
            if rows:
                setdoc["catalogs"] = cats_after          # per-SKU identity 부착분 반영
            db.products.update_one({"_id": uid}, {"$set": setdoc})
        if ident["status"] == "done":
            n_done += 1
        else:
            n_empty += 1
    if not args.dry_run:
        db.products.create_index("identity.status")
    print("=" * 64)
    print(f"완료 · done={n_done} empty={n_empty} · {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
