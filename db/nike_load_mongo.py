#!/usr/bin/env python3
"""나이키 에어포스 블랙 크로스마켓 → MongoDB 적재 (product-identity-graph 편입, 몽고판).

db/load_mongo.py 의 3컬렉션 모델(products/sources/chunks)과 같은 `insights` DB에,
가격 사다리를 위한 네 번째 컬렉션 `offers`를 더한다.
  · products : package 1 + 변형(스타일코드 SKU) N. 변형은 parent_uid로 자식. 가격요약 임베드.
  · offers   : SKU별 몰 리스팅(가격/중고/셀러구분/플랫폼). product_uid로 product 참조.
               (관계형 offer 테이블의 몽고판 — 집계는 aggregation pipeline)

기존 변형축은 용량/개수지만 신발 변형축은 컬러웨이(스타일코드) — 같은 트리 구조에 의미만 다르게.
멱등: 이 카탈로그의 product/offer uid 집합을 통째로 교체.

사용:
  MONGO_URI="mongodb://localhost:47017/?directConnection=true" \
  python3 db/nike_load_mongo.py data/nike_crossmarket.json
"""
import argparse
import json
import os

from pymongo import MongoClient, ReplaceOne

PKG_UID = "NK_AF1_BLACK"
PKG_KEYWORD = "나이키 에어포스 1 블랙 (검색 카탈로그)"
CATEGORY = "footwear"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", nargs="?", default="data/nike_crossmarket.json")
    args = ap.parse_args()

    data = json.load(open(args.json, encoding="utf-8"))
    products = data["products"]

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")).insights

    pdocs, odocs, uids = [], [], []

    # 1) package(루트)
    pdocs.append({
        "_id": PKG_UID, "parent_uid": None, "type": "package",
        "variant_value": None, "keyword": PKG_KEYWORD, "category": CATEGORY,
        "sources": {"naver": data.get("n_listings", 0)},
        "flags": {"is_resale_market": True, "has_used": True, "source_api": "naver_shopping_search"},
        "summary": {k: data[k] for k in ("n_listings", "n_mall", "n_sku", "n_multi_mall", "no_code") if k in data},
    })
    uids.append(PKG_UID)

    # 2) 변형(스타일코드 SKU) + offers
    for p in products:
        code = p["code"]
        if not code or "::" in code:
            continue
        vuid = f"{PKG_UID}::{code}"
        uids.append(vuid)
        pdocs.append({
            "_id": vuid, "parent_uid": PKG_UID, "type": "variant",
            "variant_value": code, "keyword": f"나이키 {p['name']} ({code})", "category": CATEGORY,
            "style_code": code, "sources": {"naver": p["n_listings"]},
            "flags": {"multi_mall": p["n_malls"] > 1, "all_used": p["new"] == 0},
            "price_summary": {"min": p["min"], "max": p["max"], "median": p["median"],
                              "spread_pct": p["spread_pct"], "low_mall": p["low_mall"],
                              "n_malls": p["n_malls"], "n_listings": p["n_listings"],
                              "used": p["used"], "new": p["new"]},
        })
        for i, m in enumerate(p["members"]):
            odocs.append({
                "_id": f"{vuid}|off{i}", "product_uid": vuid, "style_code": code,
                "mall": m["mall"], "seller_kind": m.get("kind"), "platform": m.get("plat"),
                "price": m["price"], "used": bool(m.get("used")),
                "shipping_fee": None,  # 네이버 검색 API 미제공 → 상세페이지 수집 시 채움
                "url": m.get("link"), "title": m.get("title"),
            })

    # 멱등: 이 카탈로그 uid 집합 통째 교체
    db.products.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in pdocs])
    db.offers.delete_many({"product_uid": {"$in": uids}})
    if odocs:
        db.offers.insert_many(odocs, ordered=False)

    # 인덱스
    db.products.create_index("parent_uid"); db.products.create_index("type")
    db.products.create_index("category"); db.products.create_index("style_code")
    db.offers.create_index("product_uid"); db.offers.create_index("style_code")
    db.offers.create_index("mall"); db.offers.create_index("used")

    n_var = sum(1 for d in pdocs if d["type"] == "variant")
    print(f"적재 완료: package 1 · variant(SKU) {n_var} · offer {len(odocs)}  "
          f"(products={db.products.count_documents({})} offers={db.offers.count_documents({})})")
    print(f"  카탈로그 _id={PKG_UID}  DB=insights")


if __name__ == "__main__":
    main()
