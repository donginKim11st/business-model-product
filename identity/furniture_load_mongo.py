#!/usr/bin/env python3
"""가구/인테리어 GEO 매핑 → MongoDB 적재 (insights DB).

  python3 furniture_load_mongo.py                # furniture_geo_mapped.jsonl 전량
  MONGO_URI=mongodb://… python3 furniture_load_mongo.py

컬렉션: insights.furniture_products (신설 — 기존 products 3컬렉션 모델과 분리 보관,
        같은 DB라 join/집계는 aggregation 으로 가능)
문서: _id = prd_id(<mall>_<model_no>) · 멱등 upsert(ReplaceOne).

인덱스: mall, l1_category, (l1_category, l2_category), attributes.manufacturer, price
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient, ReplaceOne

HERE = os.path.dirname(os.path.abspath(__file__))
JSONL = os.path.join(HERE, "outputs", "furniture_geo_mapped.jsonl")
VARIANTS = os.path.join(HERE, "outputs", "furniture_geo_variants.jsonl")
CAT_VARIANTS = os.path.join(HERE, "outputs", "catalog_variants_furniture.csv")
URI = os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")


def catalog_variant_doc(r):
    """catalog_variants_furniture.csv 1행 → furniture_catalog_variants 문서.

    같은 (catalog_key, variant_attrs)에 상품(url)이 여럿 매핑되므로 url까지 포함해
    내용 해시로 _id를 만든다 — 재빌드해도 동일 행이면 동일 _id(멱등 upsert)."""
    key = "\t".join((r["catalog_key"], r["variant_attrs"], r["url"],
                     r["title_commerce"], r["price"]))
    vid = "fcv_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:20]
    attrs = json.loads(r["variant_attrs"]) if r["variant_attrs"] else {}
    doc = {
        "_id": vid,
        "type": "furniture_catalog_variant",
        "catalog_key": r["catalog_key"],
        "mall": r["mall"],
        "title_commerce": r["title_commerce"],
        "name": r["name"],
        "attributes": {k: v for k, v in attrs.items() if v} or None,
        "price": int(r["price"]) if str(r["price"]).isdigit() else None,
        "url": r["url"],
    }
    return {k: v for k, v in doc.items() if v is not None}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else JSONL
    recs = [json.loads(l) for l in open(path, encoding="utf-8")]
    now = datetime.now(timezone.utc)

    ops = []
    for r in recs:
        prd_id = r["prd_id"]
        if not prd_id or prd_id.endswith("_"):
            continue
        attrs = {k: v for k, v in r["attributes"].items() if v}  # sparse — 빈 값 제외
        doc = {
            "_id": prd_id,
            "type": "furniture_product",
            "mall": r["source"]["mall"],
            "name": r["name"],
            "price": int(r["price"]) if str(r["price"]).isdigit() else None,
            "currency": "KRW",
            "meta_category": r["meta_category"],
            "l1_category": r["l1_category"] or None,
            "l2_category": r["l2_category"] or None,
            "attributes": attrs,
            "url": r["source"]["url"],
            "extract_source": r["source"].get("extract_source", "공식몰PDP"),
            "schema_version": "geo-v2.10",
            "loaded_at": now,
        }
        ops.append(ReplaceOne({"_id": prd_id}, doc, upsert=True))

    db = MongoClient(URI, serverSelectionTimeoutMS=5000).insights
    col = db.furniture_products
    if ops:
        res = col.bulk_write(ops, ordered=False)
        print(f"[mongo] upsert {res.upserted_count} · modified {res.modified_count} "
              f"· 총 {col.count_documents({})}건 (insights.furniture_products)")
    col.create_index([("mall", ASCENDING)])
    col.create_index([("l1_category", ASCENDING), ("l2_category", ASCENDING)])
    col.create_index([("attributes.manufacturer", ASCENDING)])
    col.create_index([("price", ASCENDING)])

    # ── 변형(색상×사이즈) — furniture_variants (parent_uid 자식, 기존 products 모델 관례) ──
    if os.path.exists(VARIANTS):
        vops = []
        for l in open(VARIANTS, encoding="utf-8"):
            v = json.loads(l)
            vid = v["variant_id"]
            attrs = {k: x for k, x in v["attributes"].items() if x}
            doc = {
                "_id": vid,
                "type": "furniture_variant",
                "parent_uid": v["parent_uid"],
                "mall": v["source"]["mall"],
                "name": v["name"],
                "variant_value": v["variant_value"] or None,
                "variant_color": v.get("variant_color") or None,
                "variant_size": v.get("variant_size") or None,
                "price": int(v["price"]) if str(v["price"]).isdigit() else None,
                "l1_category": v["l1_category"] or None,
                "l2_category": v["l2_category"] or None,
                "attributes": attrs,
                "url": v["source"]["url"],
                "loaded_at": now,
            }
            doc = {k: x for k, x in doc.items() if x is not None}
            vops.append(ReplaceOne({"_id": vid}, doc, upsert=True))
        vcol = db.furniture_variants
        n_up = n_mod = 0
        for i in range(0, len(vops), 5000):
            res = vcol.bulk_write(vops[i:i + 5000], ordered=False)
            n_up += res.upserted_count
            n_mod += res.modified_count
        vcur = [op._filter["_id"] for op in vops]
        vstale = vcol.delete_many({"_id": {"$nin": vcur}})
        print(f"[mongo] furniture_variants upsert {n_up} · modified {n_mod} "
              f"· stale 제거 {vstale.deleted_count} · 총 {vcol.count_documents({})}건")
        vcol.create_index([("parent_uid", ASCENDING)])
        vcol.create_index([("l1_category", ASCENDING), ("l2_category", ASCENDING)])
        vcol.create_index([("variant_color", ASCENDING)])

    # ── 카탈로그 — furniture_catalogs (title_geo 층) ──
    cat_csv = os.path.join(HERE, "outputs", "catalogs_furniture.csv")
    if os.path.exists(cat_csv):
        import csv as _csv
        cops = []
        for r in _csv.DictReader(open(cat_csv, encoding="utf-8-sig")):
            cid = r["catalog_key"]
            doc = {
                "_id": cid, "type": "furniture_catalog",
                "brand": r["brand"], "title_geo": r["title_geo"],
                "product_name": r["product_name"],
                "l1_category": r["l1"] or None, "l2_category": r["l2"] or None,
                "model_code": r["model_code"] or None,
                "colors": [c for c in r["colors"].split("|") if c],
                "n_products": int(r["n_products"]), "n_variants": int(r["n_variants"]),
                "price_min": int(r["price_min"]) if r["price_min"] else None,
                "price_max": int(r["price_max"]) if r["price_max"] else None,
                "bundle_flag": r["bundle_flag"] == "1",
                "needs_review": r["needs_review"] or None,
                "sample_url": r["sample_url"], "loaded_at": now,
            }
            doc = {k: x for k, x in doc.items() if x is not None}
            cops.append(ReplaceOne({"_id": cid}, doc, upsert=True))
        ccol = db.furniture_catalogs
        n_up = n_mod = 0
        for i in range(0, len(cops), 5000):
            res = ccol.bulk_write(cops[i:i + 5000], ordered=False)
            n_up += res.upserted_count
            n_mod += res.modified_count
        current_ids = [op._filter["_id"] for op in cops]
        stale = ccol.delete_many({"_id": {"$nin": current_ids}})
        print(f"[mongo] furniture_catalogs upsert {n_up} · modified {n_mod} "
              f"· stale 제거 {stale.deleted_count} · 총 {ccol.count_documents({})}건")
        ccol.create_index([("brand", ASCENDING)])
        ccol.create_index([("title_geo", ASCENDING)])
        ccol.create_index([("l1_category", ASCENDING), ("l2_category", ASCENDING)])

    # ── 카탈로그 변형 — furniture_catalog_variants (title_commerce SKU 층, 168K) ──
    if os.path.exists(CAT_VARIANTS):
        import csv as _csv
        docs = {}
        for r in _csv.DictReader(open(CAT_VARIANTS, encoding="utf-8-sig")):
            d = catalog_variant_doc(r)
            d["loaded_at"] = now
            docs[d["_id"]] = d  # 완전 동일 행은 자연 dedupe
        vcol2 = db.furniture_catalog_variants
        n_up = n_mod = 0
        vops2 = [ReplaceOne({"_id": vid}, d, upsert=True) for vid, d in docs.items()]
        for i in range(0, len(vops2), 5000):
            res = vcol2.bulk_write(vops2[i:i + 5000], ordered=False)
            n_up += res.upserted_count
            n_mod += res.modified_count
        # stale: 이번 적재에 없는 문서 제거 ($nin 168K 회피 — loaded_at 기준)
        vstale2 = vcol2.delete_many({"loaded_at": {"$lt": now}})
        print(f"[mongo] furniture_catalog_variants upsert {n_up} · modified {n_mod} "
              f"· stale 제거 {vstale2.deleted_count} · 총 {vcol2.count_documents({})}건")
        vcol2.create_index([("catalog_key", ASCENDING)])
        vcol2.create_index([("mall", ASCENDING)])
        vcol2.create_index([("title_commerce", ASCENDING)])
        vcol2.create_index([("attributes.color", ASCENDING)])

    print("[mongo] 인덱스 보장 완료")


if __name__ == "__main__":
    main()
