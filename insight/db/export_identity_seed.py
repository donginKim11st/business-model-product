#!/usr/bin/env python3
"""insight product → identity 씨앗 CSV (T3).

canonical join: insight 가 상품 우주(uid)를 정의하고, identity 는 insight 의 상품 목록을
'씨앗'으로 받아 실행한다(synthetic listings / 독립 브랜드 카탈로그가 아니라). 이 스크립트가
그 씨앗을 만든다 — identity.status 가 아직 없는 product 를 큐로 보고 CSV 로 내보낸다.

씨앗 행 = (product, catalog) 단위. identity 는 이 행으로 정형 팩트를 채우고 insight_uid 를
보존한 CSV 를 돌려준다(T4) → identity_backfill(T2)이 uid 로 합류.

category-agnostic: category_l1 을 그대로 전달(특정 카테고리 분기 없음). identity 가
category_l1 로 카테고리별 추출기를 고른다.

  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/export_identity_seed.py --out identity/seeds/seed.csv
"""
import os
import sys
import csv
import argparse

SEED_COLUMNS = ["insight_uid", "ctlg_no", "keyword", "category_l1", "disp"]


def product_to_seed_rows(product):
    """순수 함수: product 문서 → 씨앗 행 list.

    package 는 catalogs[] 의 ctlg_no 보유 SKU 마다 1행(per-SKU 정형 합류 대상).
    catalogs 없음/ctlg_no 없음 → 상품 레벨 1행(ctlg_no=None, disp=keyword).
    T3 회귀 가드의 단위 검증 지점."""
    uid = product.get("_id")
    kw = product.get("keyword") or ""
    cat = product.get("category_l1")
    rows = []
    for c in product.get("catalogs") or []:
        ctlg = c.get("ctlg_no")
        if ctlg in (None, ""):
            continue
        rows.append({"insight_uid": uid, "ctlg_no": str(ctlg), "keyword": kw,
                     "category_l1": cat, "disp": c.get("disp") or kw})
    if not rows:                                    # catalog 없는 product(변형 등) → 상품 레벨 1행
        rows.append({"insight_uid": uid, "ctlg_no": None, "keyword": kw,
                     "category_l1": cat, "disp": kw})
    return rows


def write_seed(rows, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SEED_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="identity/seeds/seed.csv", help="씨앗 CSV 출력 경로")
    ap.add_argument("--limit", type=int, default=0, help="처리 product 수(0=전체)")
    ap.add_argument("--refresh", action="store_true",
                    help="identity.status 있어도 포함(기본은 status 부재 큐만)")
    ap.add_argument("--category", default=None, help="특정 category_l1 만 내보내기(선택)")
    args = ap.parse_args()

    from pymongo import MongoClient
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]

    q = {} if args.refresh else {"identity.status": {"$exists": False}}
    if args.category:
        q["category_l1"] = args.category
    cur = db.products.find(q, {"_id": 1, "keyword": 1, "category_l1": 1, "catalogs": 1})
    rows = []
    n_prod = 0
    for p in cur:
        rows.extend(product_to_seed_rows(p))
        n_prod += 1
        if args.limit and n_prod >= args.limit:
            break

    write_seed(rows, args.out)
    n_sku = sum(1 for r in rows if r["ctlg_no"])
    print(f"씨앗 export: product {n_prod:,} → {len(rows):,}행 (SKU {n_sku:,} · 상품레벨 {len(rows)-n_sku:,}) "
          f"→ {args.out}")
    if n_prod == 0:
        print("  (큐 비어 있음: identity.status 부재 product 없음 → 단계 skip)")


if __name__ == "__main__":
    main()
