#!/usr/bin/env python3
"""[DEMO] trees_food.jsonl(실제 식품 taxonomy) → insights_demo DB 적재 + 데모 카테고리 부여.

카테고리 grouping 의 운영 소스는 Oracle DISP_CTGR1_NM(export_bndl_category.py)다. 이건 그게
아직 없을 때 '여러 카테고리에서 대표 속성이 어떻게 달라지나'를 실제 코드(build_for_product +
category_rank)로 보여주기 위한 데모일 뿐 — 카테고리는 base 키워드 규칙으로 임시 분류한다.
운영에선 --bndl-category(Oracle) 가 이 분류를 그대로 대체한다.

  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/demo_load_trees.py --limit 800
  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/category_rank.py
"""
import os
import re
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import load_mongo
from pymongo import MongoClient

# 데모 카테고리 규칙(첫 매칭 우선). 운영 DISP_CTGR1_NM 대체 자리.
# 즉석밥/면류를 국·탕보다 먼저 평가(밥/면 제품이 '국물'류로 새지 않게). 단일 글자 토큰('국','탕','밥')은
# '국내산'·'탕수육'·'밥솥' 등 오탐이 많아 배제하고, 변별력 있는 2글자+ 토큰만 사용(데모 분류기 한정).
RULES = [
    ("즉석밥·밥류", ["햇반", "즉석밥", "비빔밥", "볶음밥", "컵밥", "누룽지", "현미밥", "잡곡밥", "발아현미", "오곡밥", "백미밥"]),
    ("면류·라면", ["컵라면", "라면", "짬뽕", "짜장", "우동", "미고랭", "쌀국수", "국수", "파스타", "스파게티", "당면", "냉면"]),
    ("국·탕·찌개", ["미역국", "육개장", "삼계탕", "곰탕", "설렁탕", "찌개", "국밥", "수프", "된장국", "북엇국", "해장국", "갈비탕", "어묵탕"]),
    ("생수·음료", ["생수", "샘물", "워터", "탄산수", "음료", "주스", "사이다", "콜라", "커피", "녹차", "보리차", "이온음료"]),
    ("과자·스낵", ["과자", "스낵", "쿠키", "비스킷", "초코", "사탕", "젤리", "팝콘", "크래커", "웨하스", "감자칩"]),
    ("간편식·요리", ["만두", "돈까스", "돈가스", "너겟", "카레", "떡볶이", "어묵", "소시지", "스팸", "갈비", "닭가슴살", "동그랑땡"]),
    ("조미·소스", ["소스", "케찹", "케첩", "마요", "간장", "된장", "고추장", "쌈장", "식용유", "참기름", "올리고당", "물엿", "굴소스"]),
    ("유제품·아침", ["우유", "요거트", "요구르트", "치즈", "시리얼", "그래놀라", "두유", "버터"]),
]


def categorize(base):
    b = base or ""
    for cat, kws in RULES:
        if any(k in b for k in kws):
            return cat
    return "기타식품"


def iter_catalogs(rec):
    """패키지 하위 카탈로그(ctlg_no/disp=풀네임). trees_food sizes[].counts[] = 실제 SKU."""
    base = rec.get("base") or ""
    out = []
    for s in rec.get("sizes") or []:
        sval = (s.get("value") or "").strip()
        counts = s.get("counts") or []
        if not counts:
            out.append({"ctlg_no": None, "disp": (f"{base} {sval}").strip() or base,
                        "size": sval or None, "count": None,
                        "has_insight": bool((s.get("block") or {}).get("taxonomy"))})
        for c in counts:
            out.append({"ctlg_no": c.get("ctlg_no"),
                        "disp": c.get("disp") or f"{base} {sval} {c.get('count','')}".strip(),
                        "size": sval or None, "count": c.get("count"),
                        "has_insight": bool((c.get("block") or {}).get("taxonomy"))})
    if not out:
        out.append({"ctlg_no": None, "disp": base, "size": None, "count": None,
                    "has_insight": bool((rec.get("block") or {}).get("taxonomy"))})
    return out


def iter_blocks(rec):
    """한 번들의 카탈로그(taxonomy 보유 노드) 산출: base + 용량(2차) + 개수(3차)."""
    base = rec.get("base") or ""
    if (rec.get("block") or {}).get("taxonomy"):
        yield ("base", base, rec["block"])
    for s in rec.get("sizes") or []:
        sval = (s.get("value") or "").strip()
        if (s.get("block") or {}).get("taxonomy") and sval and sval != "용량 단일":
            yield ("variant", sval, s["block"])
        for c in s.get("counts") or []:
            cval = (c.get("count") or "").strip()
            if (c.get("block") or {}).get("taxonomy") and cval and cval != "단품":
                yield ("variant", f"{sval} {cval}".strip(), c["block"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=800,
                    help="이번 실행에서 새로 적재할 번들 최대 수(증분 모드 기준 '신규' 개수)")
    ap.add_argument("--src", default="trees_food.jsonl")
    ap.add_argument("--max-variants", type=int, default=6)
    ap.add_argument("--reset", action="store_true",
                    help="옛 동작: 적재 전 DB를 통째로 비움(완료된 6/7단계 인사이트도 삭제됨). 기본은 증분(비파괴).")
    args = ap.parse_args()

    dbname = os.environ.get("INSIGHTS_DB", "insights_demo")
    if dbname == "insights":
        sys.exit("✗ 안전장치: 데모는 INSIGHTS_DB=insights_demo 등 별도 DB로. (운영 insights 보호)")
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[dbname]
    if args.reset:
        for c in ("products", "sources", "chunks", "category_attribute_rank"):
            db[c].delete_many({})
        existing = set()
    else:
        # 증분: 이미 적재된 패키지(uid)는 건너뛴다 → 완료된 catalog insight / youtube 보존.
        existing = set(db.products.distinct("_id", {"type": "package"}))
    print(f"[적재모드] {'RESET(전체삭제)' if args.reset else f'증분(기존 {len(existing):,}개 패키지 보존)'}")

    n_bndl = n_cat_cnt = 0
    from collections import Counter
    cat_count = Counter()
    prods, srcs, chunks = [], [], []
    with open(args.src) as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            base = rec.get("base") or ""
            bg = rec.get("bndl_grp")
            uid = f"P{bg}"
            if uid in existing:        # 증분: 이미 적재된 패키지는 스킵(완료 인사이트 보존)
                continue
            cat = categorize(base)
            cat_info = {"ctgr1": cat, "ctgr_path": cat}
            blocks = list(iter_blocks(rec))
            if not blocks:
                continue
            cat_count[cat] += 1
            catalogs = iter_catalogs(rec)        # 패키지 하위 카탈로그(ctlg_no/disp) 전체
            nv = 0
            for kind, val, block in blocks:
                if kind == "base":
                    p, s, c = load_mongo.build_for_product(uid, None, "package", None, base, "food", bg, block, cat_info=cat_info)
                    p["catalogs"] = catalogs     # 번들 하위 카탈로그 목록 부착
                    p["n_catalogs"] = len(catalogs)
                else:
                    if nv >= args.max_variants:
                        continue
                    nv += 1
                    vuid = f"{uid}::{val}".replace(" ", "_")
                    p, s, c = load_mongo.build_for_product(vuid, uid, "variant", val, f"{base} {val}", "food", bg, block, cat_info=cat_info)
                prods.append(p); srcs.extend(s); chunks.extend(c)
            n_bndl += 1
            if n_bndl >= args.limit:
                break

    if prods:
        db.products.insert_many(prods, ordered=False)
    if srcs:
        db.sources.insert_many(srcs, ordered=False)
    if chunks:
        db.chunks.insert_many(chunks, ordered=False)
    db.products.create_index("category_l1"); db.products.create_index("type")
    db.products.create_index("parent_uid")
    print(f"[DEMO] DB={dbname} · 번들 {n_bndl} · product {len(prods)} 적재 · 카테고리 {len(cat_count)}종")
    for cat, n in cat_count.most_common():
        print(f"    {n:4d} 번들  {cat}")


if __name__ == "__main__":
    main()
