#!/usr/bin/env python3
"""insights_*.jsonl → MongoDB 적재기 (멱등). 문서 모델 3컬렉션 하이브리드.

설계:
  products : 한 상품(또는 변형) = 한 문서. taxonomy(point+evidence)·faqs를 '임베드'(serving=findOne 1회).
             원문 전체(source_index)는 제외 → 문서 비대/16MB 한도 회피. 변형은 parent_uid로 자식.
  sources  : 원문 전체. _id=(product_uid,local_id). evidence가 source_id로 참조.
  chunks   : point/faq를 컨텍스트 합성한 검색 단위(RAG 임베딩 + 크로스상품 집계 평탄화면).
             dim_path가 평탄화돼 있어 분석 집계도 여기서.

사용:
  pip install pymongo
  MONGO_URI="mongodb://localhost:47017/?directConnection=true" \
  python3 db/load_mongo.py insights_1002.jsonl --category food --work-units work_units.jsonl
"""
import os, sys, json, hashlib, argparse
from pymongo import MongoClient, ReplaceOne, DeleteMany, InsertOne

SKIP_LEAF = {"flags", "overall_recommendation"}
DIM_LABELS = {
    "verdict.strengths": "강점", "verdict.weaknesses": "약점", "verdict.overall_recommendation": "총평",
    "aspect.taste": "맛", "aspect.texture": "식감", "aspect.spec": "사양", "aspect.size": "크기/용량",
    "aspect.care": "관리/보관", "aspect.price_range": "가격대", "aspect.routine": "사용 루틴", "aspect.sensory": "향/감각",
    "context.who.age": "사용 연령", "context.who.gender": "성별", "context.who.household": "가구",
    "context.when.scene": "사용 상황", "context.when.season": "계절", "context.where.place": "사용 장소",
    "context.why.positive_goal": "구매 목적", "context.why.negative_concern": "우려/불만", "context.gift.recipient": "선물 대상",
}


def md5(s): return hashlib.md5(s.encode("utf-8")).hexdigest()
def dim_label(p): return DIM_LABELS.get(p) or p.split(".")[-1]


def walk_points(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in SKIP_LEAF:
                continue
            yield from walk_points(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list) and node and isinstance(node[0], dict) and "point" in node[0]:
        yield path, node


def build_for_product(uid, parent_uid, type_, variant_value, keyword, category, bndl_grp, block,
                      cat_info=None):
    """cat_info: export_bndl_category.py 산출(번들 카테고리) {ctgr1, ctgr2, ctgr_path}.
    없으면 coarse `category`(food/footwear)로 폴백 — category_l1 은 항상 채워진다(랭킹 grouping 키)."""
    tax = block.get("taxonomy") or {}
    head = keyword if not variant_value else f"{keyword} {variant_value}"
    cat_info = cat_info or {}
    category_l1 = cat_info.get("ctgr1") or category   # canonical 우선, 없으면 coarse 폴백
    # products: block에서 source_index만 제외(원문은 sources 컬렉션)
    pdoc = {k: v for k, v in block.items() if k != "source_index"}
    pdoc.update(_id=uid, parent_uid=parent_uid, type=type_, variant_value=variant_value,
                keyword=keyword, category=category, bndl_grp=bndl_grp,
                category_l1=category_l1, category_path=cat_info.get("ctgr_path"),
                category_source=("oracle" if cat_info.get("ctgr1") else "fallback"),
                # YouTube 디커플링: 적재 시점엔 placeholder. youtube_backfill.py 가 쿼터 내에서 천천히 채움.
                # (메인 적재는 youtube 를 건드리지 않으므로 backfill 결과가 재적재로 날아가지 않게
                #  main() 에서 기존 youtube/representative 를 보존 머지한다.)
                youtube={"status": "pending", "attempts": 0},
                overall_recommendation=(tax.get("verdict") or {}).get("overall_recommendation"),
                flags=tax.get("flags") or {})
    # sources
    sdocs = []
    for sid, s in (block.get("source_index") or {}).items():
        sdocs.append(dict(s, _id=f"{uid}:{sid}", product_uid=uid, local_id=sid))
    # chunks (point + faq), 컨텍스트 합성
    cdocs = []
    meta_base = {"keyword": keyword, "type": type_, "category": category,
                 "category_l1": category_l1, "bndl_grp": bndl_grp,
                 "variant_value": variant_value, "flags": tax.get("flags") or {},
                 "source_counts": block.get("sources") or {}}
    for dim_path, items in walk_points(tax):
        for i, it in enumerate(items):
            content = f"{head} — {dim_label(dim_path)}: {it.get('point','')}"
            cdocs.append({"_id": f"{uid}|point|{dim_path}#{i}", "product_uid": uid, "kind": "point",
                          "dim_path": dim_path, "content": content, "content_hash": md5(content),
                          "metadata": dict(meta_base, dim=dim_path, cited=it.get("cited_examples"))})
    for j, f in enumerate(block.get("faqs") or []):
        content = f"{head} — Q: {f.get('question','')} A: {f.get('short_answer') or ''}".strip()
        cdocs.append({"_id": f"{uid}|faq|{j}", "product_uid": uid, "kind": "faq",
                      "content": content, "content_hash": md5(content), "metadata": dict(meta_base, qa=True)})
    return pdoc, sdocs, cdocs


def _preserve_async_fields(p, ex):
    """비동기로 채워지는 필드를 재적재(ReplaceOne) 시 보존 머지.
    인사이트 재적재가 backfill·주간배치 산출(youtube/representative/identity)을 덮어쓰지 않게 한다.
    p: 새로 빌드한 product 문서(in-place 수정), ex: 기존 문서(projection 결과, 없으면 {}).
    순수 함수(Mongo 비의존) — reload 보존 테스트의 단위 검증 지점."""
    yt = ex.get("youtube")
    if yt:
        if yt.get("status") not in (None, "pending"):
            p["youtube"] = yt                   # done/empty/error → 통째 보존
        elif yt.get("attempts"):                # pending이지만 시도이력 있음 → 이월(--max-attempts 보호)
            p["youtube"] = dict(p["youtube"], attempts=yt.get("attempts", 0),
                                last_error=yt.get("last_error"))
    if ex.get("representative") is not None:
        p["representative"] = ex["representative"]   # 주간 랭킹 산출 → 보존
    if ex.get("identity") is not None:
        p["identity"] = ex["identity"]               # identity_backfill 정형 스핀(brand/status) → 보존
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl"); ap.add_argument("--category", default=None)
    ap.add_argument("--work-units", default=None); ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--bndl-category", default=None,
                    help="export_bndl_category.py 산출(bndl_grp→canonical 카테고리) jsonl")
    args = ap.parse_args()

    bndl = {}
    if args.work_units and os.path.exists(args.work_units):
        for line in open(args.work_units):
            if line.strip():
                w = json.loads(line); bndl[w["uid"]] = w.get("bndl_grp")

    # 번들 카테고리 매핑(canonical) — bndl_grp -> {ctgr1, ctgr2, ctgr_path}
    catmap = {}
    if args.bndl_category and os.path.exists(args.bndl_category):
        for line in open(args.bndl_category):
            if line.strip():
                c = json.loads(line); catmap[c["bndl_grp"]] = c
    print(f"카테고리 매핑 로드: {len(catmap):,}개 번들" if catmap
          else "카테고리 매핑 없음 — coarse --category 로 폴백")

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]
    n_base = n_var = 0
    for n, line in enumerate(open(args.jsonl)):
        if not line.strip():
            continue
        if args.limit and n >= args.limit:
            break
        rec = json.loads(line); uid = rec["uid"]
        prods, srcs, chunks = [], [], []
        bg = bndl.get(uid)
        cat_info = catmap.get(bg)
        def add(u, par, ty, vv, kw, blk):
            p, s, c = build_for_product(u, par, ty, vv, kw, args.category, bg, blk, cat_info=cat_info)
            prods.append(p); srcs.extend(s); chunks.extend(c)
        add(uid, None, rec.get("type", "package"), None, rec.get("keyword", ""), rec["block"]); n_base += 1
        for sz in (rec.get("tree") or {}).get("sizes") or []:
            vb = sz.get("block"); val = sz.get("value")
            if not vb:
                continue
            if not val or "|" in val:        # 변형키 가드(구분자 '|' 충돌 차단)
                raise ValueError(f"{uid}: 불안정한 변형 value={val!r}")
            add(f"{uid}::{val}", uid, "variant", val, f"{rec.get('keyword','')} {val}".strip(), vb); n_var += 1

        # 멱등: 이 base와 변형 uid 집합을 통째로 교체.
        # 단 youtube(backfill 산출)·representative(주간 배치 산출)는 비동기로 채워지므로
        # 인사이트 재적재가 이를 덮어쓰지 않도록 기존 값을 보존 머지한다.
        uids = [p["_id"] for p in prods]
        keep = {d["_id"]: d for d in db.products.find(
            {"_id": {"$in": uids}}, {"youtube": 1, "representative": 1, "identity": 1})}
        for p in prods:
            _preserve_async_fields(p, keep.get(p["_id"]) or {})
        db.products.bulk_write([ReplaceOne({"_id": p["_id"]}, p, upsert=True) for p in prods])
        # backfill이 넣은 kind=youtube 원문은 건드리지 않는다(보존된 youtube.taxonomy evidence의 조인 대상).
        db.sources.delete_many({"product_uid": {"$in": uids}, "kind": {"$ne": "youtube"}})
        if srcs: db.sources.insert_many(srcs, ordered=False)
        db.chunks.delete_many({"product_uid": {"$in": uids}})
        if chunks: db.chunks.insert_many(chunks, ordered=False)

    # 인덱스(가벼움). 벡터 검색 인덱스는 db/mongo_setup.py에서 별도 생성(임베딩 후).
    db.products.create_index("parent_uid"); db.products.create_index("type")
    db.products.create_index("category"); db.products.create_index("flags.is_gift_set")
    db.products.create_index("category_l1"); db.products.create_index("bndl_grp")
    db.products.create_index("youtube.status")   # backfill 큐 스캔용
    db.products.create_index("identity.status")  # identity 씨앗 export 스캔용(status 부재/pending 큐)
    db.sources.create_index("url"); db.chunks.create_index("product_uid")
    db.chunks.create_index("dim_path"); db.chunks.create_index("metadata.dim")
    db.chunks.create_index("metadata.category_l1")
    print(f"적재 완료: base={n_base} variants={n_var}  "
          f"(products={db.products.count_documents({})} sources={db.sources.count_documents({})} "
          f"chunks={db.chunks.count_documents({})})")


if __name__ == "__main__":
    main()
