#!/usr/bin/env python3
"""스포츠/아웃도어 30브랜드 정형 → MongoDB 적재 (insights DB).

  python3 sports_load_mongo.py            # all_brands.csv + catalogs.csv 전량

컬렉션 (furniture_products 와 대칭 설계):
  insights.sports_products : all_brands.csv — 공식몰 원본 상품 단위 (~61k)
      _id = <source>_<style_code> (style_code 없으면 <source>_row<N>)
  insights.sports_catalogs : catalogs.csv — 모델 롤업 + title_geo (~36k)
      _id = <source>_<model_key>

멱등 upsert(ReplaceOne). catalog_decomposed(사이즈 전개)는 두 컬렉션에서 파생 가능해 생략.
"""
import csv
import os
import re
import sys
from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient, ReplaceOne

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
URI = os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")
BATCH = 5000


def clean_id(s):
    return re.sub(r"[^A-Za-z0-9가-힣_:-]", "_", s)[:120]


def bulk(col, ops):
    total_up = total_mod = 0
    for i in range(0, len(ops), BATCH):
        res = col.bulk_write(ops[i:i + BATCH], ordered=False)
        total_up += res.upserted_count
        total_mod += res.modified_count
    return total_up, total_mod


def load_products(db, now):
    path = os.path.join(OUT, "all_brands.csv")
    ops, seen = [], set()
    for i, r in enumerate(csv.DictReader(open(path, encoding="utf-8-sig"))):
        source = (r.get("source") or "").strip()
        sc = (r.get("style_code") or "").strip()
        uid = clean_id(f"{source}_{sc}") if sc else f"{source}_row{i}"
        if uid in seen:
            uid = f"{uid}__{i}"  # 동일 스타일코드 중복행(색상 분리 몰) 보존
        seen.add(uid)
        sizes = [s for s in (r.get("sizes") or "").split("|") if s]
        doc = {
            "_id": uid,
            "type": "sports_product",
            "mall": source,
            "brand": (r.get("brand") or "").strip(),
            "style_code": sc or None,
            "name": (r.get("name") or "").strip(),
            "color": (r.get("color") or "").strip() or None,
            "price": int(r["price"]) if str(r.get("price") or "").isdigit() else None,
            "currency": (r.get("currency") or "KRW"),
            "category": (r.get("category") or "").strip() or None,
            "gender": (r.get("gender") or "").strip() or None,
            "sizes": sizes,
            "origin": (r.get("origin") or "").strip() or None,
            "material": (r.get("material") or "").strip() or None,
            "mfg_date": (r.get("mfg_date") or "").strip() or None,
            "url": (r.get("url") or "").strip(),
            "loaded_at": now,
        }
        doc = {k: v for k, v in doc.items() if v not in (None, "", [])}
        ops.append(ReplaceOne({"_id": uid}, doc, upsert=True))
    col = db.sports_products
    up, mod = bulk(col, ops)
    print(f"[sports_products] upsert {up} · modified {mod} · 총 {col.count_documents({})}건")
    col.create_index([("mall", ASCENDING)])
    col.create_index([("brand", ASCENDING)])
    col.create_index([("style_code", ASCENDING)])
    col.create_index([("category", ASCENDING), ("gender", ASCENDING)])


def load_catalogs(db, now):
    path = os.path.join(OUT, "catalogs.csv")
    ops, seen = [], set()
    for i, r in enumerate(csv.DictReader(open(path, encoding="utf-8-sig"))):
        source = (r.get("source") or "").strip()
        mk = (r.get("model_key") or "").strip() or f"row{i}"
        uid = clean_id(f"{source}_{mk}")
        if uid in seen:
            uid = f"{uid}__{i}"
        seen.add(uid)
        doc = {
            "_id": uid,
            "type": "sports_catalog",
            "mall": source,
            "brand": (r.get("brand_norm") or "").strip(),
            "title_geo": (r.get("title_geo") or "").strip(),
            "title_commerce": (r.get("title_commerce") or "").strip(),
            "product_name": (r.get("product_name") or "").strip(),
            "gender": (r.get("gender") or "").strip() or None,
            "product_type": (r.get("product_type") or "").strip() or None,
            "colors": [c for c in (r.get("colors") or "").split("|") if c],
            "n_colors": int(r["n_colors"]) if str(r.get("n_colors") or "").isdigit() else None,
            "size_range": (r.get("size_range") or "").strip() or None,
            "materials": (r.get("materials") or "").strip() or None,
            "origins": (r.get("origins") or "").strip() or None,
            "style_codes": [s for s in (r.get("style_codes") or "").split("|") if s],
            "price_min": int(r["price_min"]) if str(r.get("price_min") or "").isdigit() else None,
            "price_max": int(r["price_max"]) if str(r.get("price_max") or "").isdigit() else None,
            "n_variants": int(r["n_variants"]) if str(r.get("n_variants") or "").isdigit() else None,
            "sample_url": (r.get("sample_url") or "").strip() or None,
            "loaded_at": now,
        }
        doc = {k: v for k, v in doc.items() if v not in (None, "", [])}
        ops.append(ReplaceOne({"_id": uid}, doc, upsert=True))
    col = db.sports_catalogs
    up, mod = bulk(col, ops)
    print(f"[sports_catalogs] upsert {up} · modified {mod} · 총 {col.count_documents({})}건")
    col.create_index([("brand", ASCENDING)])
    col.create_index([("product_type", ASCENDING)])
    col.create_index([("title_geo", ASCENDING)])
    col.create_index([("style_codes", ASCENDING)])


def load_variants(db, now):
    """catalog_decomposed.csv — 사이즈 단위 전개 (SKU/변형 층, 스포츠 최종 산출물).
    가구 furniture_variants 와 대칭."""
    path = os.path.join(OUT, "catalog_decomposed.csv")
    if not os.path.exists(path):
        print("[sports_variants] catalog_decomposed.csv 없음 — 스킵")
        return
    ops, seen = [], set()
    for i, r in enumerate(csv.DictReader(open(path, encoding="utf-8-sig"))):
        source = (r.get("source") or "").strip()
        sc = (r.get("style_code") or "").strip()
        size = (r.get("size") or "").strip()
        uid = clean_id(f"{source}_{sc or 'nosc'}_{size or 'nosize'}")
        if uid in seen:
            uid = f"{uid}__{i}"
        seen.add(uid)
        doc = {
            "_id": uid,
            "type": "sports_variant",
            "mall": source,
            "brand": (r.get("brand_norm") or "").strip(),
            "style_code": sc or None,
            "title_geo": (r.get("title_geo") or "").strip() or None,
            "title_commerce": (r.get("title_commerce") or "").strip() or None,
            "product_name": (r.get("product_name") or "").strip() or None,
            "gender": (r.get("gender") or "").strip() or None,
            "product_type": (r.get("product_type") or "").strip() or None,
            "color": (r.get("color") or "").strip() or None,
            "size": size or None,
            "material": (r.get("material") or "").strip() or None,
            "origin": (r.get("origin") or "").strip() or None,
            "price": int(r["price"]) if str(r.get("price") or "").isdigit() else None,
            "url": (r.get("url") or "").strip() or None,
            "loaded_at": now,
        }
        doc = {k: v for k, v in doc.items() if v not in (None, "", [])}
        ops.append(ReplaceOne({"_id": uid}, doc, upsert=True))
    col = db.sports_variants
    up, mod = bulk(col, ops)
    print(f"[sports_variants] upsert {up} · modified {mod} · 총 {col.count_documents({})}건")
    col.create_index([("style_code", ASCENDING)])
    col.create_index([("brand", ASCENDING), ("product_type", ASCENDING)])
    col.create_index([("title_geo", ASCENDING)])


def main():
    now = datetime.now(timezone.utc)
    db = MongoClient(URI, serverSelectionTimeoutMS=5000).insights
    load_products(db, now)
    load_catalogs(db, now)
    load_variants(db, now)
    print("[mongo] 스포츠 적재 완료")


if __name__ == "__main__":
    main()
