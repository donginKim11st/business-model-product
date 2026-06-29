#!/usr/bin/env python3
"""카테고리 실제화 — 데모 휴리스틱 → 실제 네이버 쇼핑 카테고리(offers.category_naver).

food_price_backfill 이 수집한 offers.category_naver(= 네이버 cat2 > cat3, 100% 실측)의 cat2 최빈값을
각 패키지의 category_l1 으로 박는다. offers 없는 패키지는 (미분류). 그리고 그 카테고리를 변형(variant)에
전파한다(변형이 옛 데모 카테고리로 남으면 category_rank 에서 유령 카테고리·분모 오염 유발).

  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/realize_category.py
  # 이후 반드시 랭킹 재실행: python3 db/category_rank.py
"""
import os
from collections import defaultdict, Counter
from pymongo import MongoClient, UpdateMany, UpdateOne


def main():
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]

    # 1) 패키지별 네이버 cat2 최빈 (offers.package_uid + category_naver)
    pkgcat = defaultdict(Counter)
    for o in db.offers.find({}, {"package_uid": 1, "category_naver": 1}):
        cn = o.get("category_naver"); pu = o.get("package_uid")
        if pu and cn:
            pkgcat[pu][cn.split(" > ")[0]] += 1

    ops = []
    n_real = 0
    for p in db.products.find({"type": "package"}, {"_id": 1}):
        cnt = pkgcat.get(p["_id"])
        if cnt:
            cat = cnt.most_common(1)[0][0]
            ops.append(UpdateOne({"_id": p["_id"]},
                                 {"$set": {"category_l1": cat, "category_source": "naver_shop"}}))
            n_real += 1
        else:
            ops.append(UpdateOne({"_id": p["_id"]},
                                 {"$set": {"category_l1": "(미분류)", "category_source": "demo"}}))
    for i in range(0, len(ops), 500):
        db.products.bulk_write(ops[i:i + 500])

    # 2) 패키지 category_l1 → 변형(variant) 전파
    prop = []
    for p in db.products.find({"type": "package"}, {"_id": 1, "category_l1": 1, "category_source": 1}):
        prop.append(UpdateMany({"parent_uid": p["_id"], "type": "variant"},
                               {"$set": {"category_l1": p.get("category_l1"),
                                         "category_source": p.get("category_source")}}))
    for i in range(0, len(prop), 500):
        db.products.bulk_write(prop[i:i + 500])

    src = Counter(p.get("category_source") for p in db.products.find({"type": "package"}, {"category_source": 1}))
    ncat = len({p["category_l1"] for p in db.products.find({"type": "package"}, {"category_l1": 1})})
    print(f"카테고리 실제화 완료 · 실측(naver_shop) {n_real} · 데모폴백((미분류)) {src.get('demo', 0)} · "
          f"카테고리 {ncat}종 · 변형 전파 {len(prop)} 패키지")
    print("→ 다음: python3 db/category_rank.py 로 랭킹 재계산 필수")


if __name__ == "__main__":
    main()
