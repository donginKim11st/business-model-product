#!/usr/bin/env python3
"""나이키 에어포스 블랙 크로스마켓 → PostgreSQL 적재 (product-identity-graph 편입).

data/nike_crossmarket.json(스타일코드로 cross-mall 해소한 결과)을 기존 스키마에 싣는다:
  · package product 1건  = "나이키 에어포스 1 블랙 (검색 카탈로그)"
  · variant product N건  = 스타일코드 SKU (variant_value=코드, parent=package) ← '패키지 하위 카탈로그 이름'
  · offer M건            = 각 SKU의 몰별 리스팅(가격 사다리). db/schema_offer.sql 의 offer 테이블.

기존 변형축은 용량/개수지만, 신발의 변형축은 '컬러웨이(스타일코드)' — 같은 트리(parent_uid) 구조에
의미만 다르게 싣는다. 인사이트(point/evidence/faq)는 아직 비어 있고, 인사이트 파이프라인이 같은 uid로 채운다.

멱등: package uid를 DELETE(CASCADE) 후 재삽입 → variant·offer 전부 정리되고 다시 들어간다.
적재 순서 주의: 인사이트 load.py 가 같은 uid를 재적재하면 CASCADE로 offer가 지워지므로, 그 뒤 본 스크립트를 다시 돌릴 것.

사용:
  export PGHOST=localhost PGPORT=55432 PGUSER=postgres PGPASSWORD=insight PGDATABASE=insights
  python3 db/nike_load.py data/nike_crossmarket.json
"""
import argparse
import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values, Json

PKG_UID = "NK_AF1_BLACK"
PKG_KEYWORD = "나이키 에어포스 1 블랙 (검색 카탈로그)"
CATEGORY = "footwear"


def insert_product(cur, uid, parent_uid, type_, variant_value, keyword, sources, flags, raw_block, n_items):
    cur.execute("""
        INSERT INTO product (uid, parent_uid, bndl_grp, type, variant_value, keyword, category,
                             analyzed_count, ad_flagged, sources, verification,
                             overall_recommendation, flags, note_hash, raw_block, n_items)
        VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,0,%s,%s,NULL,%s,NULL,%s,%s)""",
        (uid, parent_uid, type_, variant_value, keyword, CATEGORY,
         n_items, Json(sources), Json({}), Json(flags), Json(raw_block), n_items))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", nargs="?", default="data/nike_crossmarket.json")
    args = ap.parse_args()

    data = json.load(open(args.json, encoding="utf-8"))
    products = data["products"]

    conn = psycopg2.connect()  # PG* 환경변수
    conn.autocommit = False
    cur = conn.cursor()

    # 멱등: 패키지(루트) 삭제 → 변형·offer CASCADE 제거
    cur.execute("DELETE FROM product WHERE uid = %s", (PKG_UID,))

    # 1) package(루트) product
    pkg_flags = {"is_resale_market": True, "has_used": True, "source_api": "naver_shopping_search"}
    pkg_raw = {k: data[k] for k in ("n_listings", "n_mall", "n_sku", "n_multi_mall", "no_code") if k in data}
    insert_product(cur, PKG_UID, None, "package", None, PKG_KEYWORD,
                   {"naver": data.get("n_listings", 0)}, pkg_flags, pkg_raw, data.get("n_listings"))

    # 2) variant(스타일코드 SKU) product + offer
    n_var = n_off = 0
    offer_rows = []
    for p in products:
        code = p["code"]
        if not code or "::" in code:
            continue
        vuid = f"{PKG_UID}::{code}"
        vkw = f"나이키 {p['name']} ({code})"
        vflags = {
            "n_malls": p["n_malls"], "spread_pct": p["spread_pct"],
            "used": p["used"], "new": p["new"],
            "multi_mall": p["n_malls"] > 1, "all_used": p["new"] == 0,
        }
        vraw = {"code": code, "name": p["name"], "min": p["min"], "max": p["max"],
                "median": p["median"], "spread_pct": p["spread_pct"], "low_mall": p["low_mall"],
                "n_malls": p["n_malls"], "n_listings": p["n_listings"]}
        insert_product(cur, vuid, PKG_UID, "variant", code, vkw,
                       {"naver": p["n_listings"]}, vflags, vraw, p["n_listings"])
        n_var += 1
        for m in p["members"]:
            offer_rows.append((vuid, m["mall"], m.get("kind"), m.get("plat"), m["price"],
                               bool(m.get("used")), code, None, m.get("link"), m.get("title")))
            n_off += 1

    if offer_rows:
        execute_values(cur, """
            INSERT INTO offer (product_uid, mall, seller_kind, platform, price, used,
                               style_code, shipping_fee, url, title)
            VALUES %s""", offer_rows)

    conn.commit()
    conn.close()
    print(f"적재 완료: package 1 · variant(SKU) {n_var} · offer {n_off}")
    print(f"  카탈로그 uid={PKG_UID}  (category={CATEGORY})")


if __name__ == "__main__":
    main()
