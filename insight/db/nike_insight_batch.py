#!/usr/bin/env python3
"""다중몰 나이키 SKU 일괄 인사이트 (product-identity-graph 편입).

Mongo products 에서 flags.multi_mall=true 인 변형(SKU)을 골라, 각각
네이버 블로그 → 광고필터 → gpt-4o-mini → taxonomy/faqs 를 추출하고 우리 카탈로그 문서에 MERGE($set).
nike_insight_one.py 와 동일 정합(가격 데이터 보존), 한 클라이언트로 총비용 누적.

  set -a; eval "$(grep '^export ' run.sh)"; set +a
  NO_YT=1 DANAWA_OFF=1 MONGO_URI="mongodb://localhost:47017/?directConnection=true" \
    python3 db/nike_insight_batch.py
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

os.environ.setdefault("INSIGHT_MODEL", "gpt-4o-mini")

import run_batch
import naver_review_geo as nrg  # noqa: F401
import load_mongo
from pymongo import MongoClient

CODE_PAREN = re.compile(r"\s*\([A-Z0-9-]+\)\s*$")


def clean_kw(keyword):
    """'나이키 에어포스 1 07 트리플 블랙 (DD8959-001)' → 괄호 스타일코드 제거(블로그 검색용)."""
    return CODE_PAREN.sub("", keyword).replace("'07", "07").strip()


def insight_one(uid, keyword, nid, nsec, ytk, use_yt, llm, db):
    parent = uid.split("::")[0]
    code = uid.split("::")[1] if "::" in uid else None
    items = run_batch.collect(keyword, nid, nsec, ytk, use_yt=use_yt)
    if not items:
        return {"uid": uid, "ok": False, "n_src": 0}
    t0 = time.time()
    block = run_batch.extract_full(keyword, items, llm)
    if not block:
        return {"uid": uid, "ok": False, "n_src": len(items)}
    pdoc, sdocs, cdocs = load_mongo.build_for_product(uid, parent, "variant", code, keyword, "footwear", None, block)
    # youtube(backfill)·representative(주간배치)는 비동기 산출 → MERGE에서 제외(덮어쓰기 방지).
    keep_out = {"_id", "parent_uid", "type", "variant_value", "keyword", "category", "flags",
                "youtube", "representative"}
    set_fields = {k: v for k, v in pdoc.items() if k not in keep_out}
    set_fields["insight_keyword"] = keyword
    set_fields["insight_elapsed"] = round(time.time() - t0, 1)
    db.products.update_one({"_id": uid}, {"$set": set_fields})
    db.sources.delete_many({"product_uid": uid, "kind": {"$ne": "youtube"}})  # backfill youtube 원문 보존
    if sdocs:
        db.sources.insert_many(sdocs, ordered=False)
    db.chunks.delete_many({"product_uid": uid})
    if cdocs:
        db.chunks.insert_many(cdocs, ordered=False)
    tax = block.get("taxonomy") or {}
    return {"uid": uid, "ok": True, "n_src": len(items),
            "n_point": sum(len(v) for _, v in load_mongo.walk_points(tax)),
            "n_faq": len(block.get("faqs") or [])}


def main():
    nid = os.environ.get("NAVER_CLIENT_ID")
    nsec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (nid and nsec and os.environ.get("OPENAI_API_KEY")):
        sys.exit("✗ 키 없음 (run.sh export 로드 필요)")
    ytk = os.environ.get("YOUTUBE_API_KEY")
    use_yt = not os.environ.get("NO_YT")
    yt_eff = bool(use_yt and ytk and os.environ.get("INSIGHT_INLINE_YT"))  # 디커플링: 인라인은 옵트인만

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")).insights
    skus = list(db.products.find(
        {"type": "variant", "flags.multi_mall": True},
        {"_id": 1, "keyword": 1, "price_summary.n_malls": 1}))
    skus.sort(key=lambda d: -(d.get("price_summary", {}).get("n_malls", 0)))
    print(f"다중몰 SKU {len(skus)}개 일괄 인사이트 (YouTube={'inline' if yt_eff else 'off(backfill)'}, "
          f"danawa={'off' if os.environ.get('DANAWA_OFF') else 'on'}, {os.environ['INSIGHT_MODEL']})")
    print("=" * 64)

    llm = run_batch.make_client()
    t0 = time.time()
    done = []
    for i, d in enumerate(skus, 1):
        kw = clean_kw(d["keyword"])
        print(f"[{i}/{len(skus)}] {d['_id'].split('::')[-1]:12} '{kw}' ...", flush=True)
        try:
            r = insight_one(d["_id"], kw, nid, nsec, ytk, use_yt, llm, db)
        except Exception as e:
            print(f"     ✗ 오류: {str(e)[:80]}", flush=True)
            r = {"uid": d["_id"], "ok": False, "n_src": -1}
        done.append(r)
        if r["ok"]:
            print(f"     → 출처 {r['n_src']} · point {r['n_point']} · faq {r['n_faq']}", flush=True)
        else:
            print(f"     → 스킵 (출처 {r['n_src']})", flush=True)

    cost = run_batch.usd()
    ok = [r for r in done if r["ok"]]
    print("=" * 64)
    print(f"완료: {len(ok)}/{len(skus)} SKU · point 합계 {sum(r.get('n_point',0) for r in ok)} · "
          f"{round(time.time()-t0)}s · 총비용 ≈ ${cost:.4f} (≈ ₩{cost*1380:.0f})")


if __name__ == "__main__":
    main()
