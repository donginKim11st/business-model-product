#!/usr/bin/env python3
"""나이키 SKU 1건 인사이트 검증 — 신발 핏 + 비용 확인용 (product-identity-graph 편입).

run_batch.py 의 빌딩블록(collect → extract_full=build_sourced_block)을 재사용해 한 SKU에 대해
네이버 블로그(+선택 다나와) → 광고필터 → OpenAI(gpt-4o-mini) 인사이트(taxonomy/faqs)를 추출하고,
**우리 카탈로그 uid(NK_AF1_BLACK::코드)의 기존 product 문서에 MERGE**($set)한다 — 가격 사다리(price_summary)·offer는 보존.
sources/chunks 컬렉션도 load_mongo.build_for_product 로직으로 채운다.

키는 run.sh 의 export 라인에서 로드(값 출력 안 함):
  set -a; eval "$(grep '^export ' run.sh)"; set +a
  NO_YT=1 DANAWA_OFF=1 MONGO_URI="mongodb://localhost:47017/?directConnection=true" \
    python3 db/nike_insight_one.py "나이키 에어포스 1 트리플 블랙" NK_AF1_BLACK::DD8959-001
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

os.environ.setdefault("INSIGHT_MODEL", "gpt-4o-mini")

import run_batch
import naver_review_geo as nrg  # noqa: F401  (run_batch가 의존)
import load_mongo
from pymongo import MongoClient

KW = sys.argv[1] if len(sys.argv) > 1 else "나이키 에어포스 1 트리플 블랙"
UID = sys.argv[2] if len(sys.argv) > 2 else "NK_AF1_BLACK::DD8959-001"
PARENT = UID.split("::")[0]
CODE = UID.split("::")[1] if "::" in UID else None


def main():
    nid = os.environ.get("NAVER_CLIENT_ID")
    nsec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (nid and nsec and os.environ.get("OPENAI_API_KEY")):
        sys.exit("✗ 키 없음: NAVER_CLIENT_ID/SECRET, OPENAI_API_KEY 필요 (run.sh export 로드)")
    ytk = os.environ.get("YOUTUBE_API_KEY")
    use_yt = not os.environ.get("NO_YT")
    # YouTube는 디커플링됨 — INSIGHT_INLINE_YT 가 켜져 있고 키가 있을 때만 인라인 수집(그 외엔 backfill 전담).
    yt_eff = bool(use_yt and ytk and os.environ.get("INSIGHT_INLINE_YT"))

    llm = run_batch.make_client()
    t0 = time.time()
    print(f"[수집] '{KW}'  (YouTube={'inline' if yt_eff else 'off(backfill 전담)'}, danawa={'off' if os.environ.get('DANAWA_OFF') else 'on'})")
    items = run_batch.collect(KW, nid, nsec, ytk, use_yt=use_yt)
    print(f"  → 출처 {len(items)}건 수집")
    if not items:
        sys.exit("✗ 수집된 출처 0 — 키워드를 바꿔보세요")

    print(f"[추출] OpenAI {os.environ['INSIGHT_MODEL']} 로 인사이트 추출 중...")
    block = run_batch.extract_full(KW, items, llm)
    if not block:
        sys.exit("✗ block 생성 실패")
    elapsed = round(time.time() - t0, 1)

    # sources/chunks 문서 생성 (load_mongo 로직 재사용)
    pdoc, sdocs, cdocs = load_mongo.build_for_product(
        UID, PARENT, "variant", CODE, KW, "footwear", None, block)

    # 기존 변형 문서에 인사이트 필드만 MERGE($set) — price_summary/flags/style_code 보존.
    # youtube(backfill)·representative(주간배치)는 비동기 산출이라 $set에서 제외(덮어쓰기 방지).
    keep_out = {"_id", "parent_uid", "type", "variant_value", "keyword", "category", "flags",
                "youtube", "representative"}
    set_fields = {k: v for k, v in pdoc.items() if k not in keep_out}
    set_fields["insight_keyword"] = KW
    set_fields["insight_elapsed"] = elapsed

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")).insights
    r = db.products.update_one({"_id": UID}, {"$set": set_fields})
    if r.matched_count == 0:
        print(f"  ⚠ {UID} 문서 없음 → upsert로 새로 생성")
        db.products.update_one({"_id": UID}, {"$set": dict(set_fields, parent_uid=PARENT, type="variant",
                               variant_value=CODE, keyword=KW, category="footwear")}, upsert=True)
    db.sources.delete_many({"product_uid": UID, "kind": {"$ne": "youtube"}})  # backfill youtube 원문 보존
    if sdocs:
        db.sources.insert_many(sdocs, ordered=False)
    db.chunks.delete_many({"product_uid": UID})
    if cdocs:
        db.chunks.insert_many(cdocs, ordered=False)

    # 요약
    tax = block.get("taxonomy") or {}
    n_points = sum(len(v) for _, v in load_mongo.walk_points(tax))
    n_faqs = len(block.get("faqs") or [])
    cost = run_batch.usd()
    print("\n" + "=" * 60)
    print(f" 인사이트 1건 완료 — {UID}")
    print("=" * 60)
    print(f" 출처 {len(items)} · point {n_points} · faq {n_faqs} · {elapsed}s")
    print(f" 비용 ≈ ${cost:.4f} (≈ ₩{cost*1380:.0f})  [{os.environ['INSIGHT_MODEL']}]")
    print(f" Mongo: products.{UID} $set(taxonomy/faqs) · sources {len(sdocs)} · chunks {len(cdocs)}")
    # 샘플 포인트
    print("\n 추출 샘플(차원별 1건):")
    shown = set()
    for dim, pts in load_mongo.walk_points(tax):
        if dim in shown or not pts:
            continue
        shown.add(dim)
        print(f"   [{dim}] {pts[0].get('point','')[:60]}")
        if len(shown) >= 8:
            break


if __name__ == "__main__":
    main()
