#!/usr/bin/env python3
"""정규화 테이블(point/faq) → rag_chunk 머티리얼라이즈 (멱등). 임베딩은 별도 단계.

핵심: content를 상품·차원 컨텍스트와 합성해 임베딩 변별력 확보.
  point: "<keyword> <variant> — <dim 라벨>: <point.text>"
  faq  : "<keyword> — Q: <question> A: <short_answer>"
  → 동일 문장도 상품/차원 다르면 임베딩이 자연 분리(별도 디덕 불필요), 상품 앵커링 확보.
임베딩 단위 = point + faq 만(짧고 광고 섞인 source 4M은 임베딩 제외 — pg_trgm/evidence join으로 회수).

사용:
  export PGHOST=localhost PGPORT=55432 PGUSER=postgres PGPASSWORD=insight PGDATABASE=insights
  python3 db/build_chunks.py [--only-product P7863]
이후 임베딩: chunk_embedding 을 content_hash 불일치 행만 골라 배치 임베딩(embed 스텝은 모델 선정 후).
"""
import os, sys, json, hashlib, argparse
import psycopg2
from psycopg2.extras import execute_values, Json

# dim_path → 한국어 라벨(검색 컨텍스트용). 미등록 path는 마지막 세그먼트로 폴백.
DIM_LABELS = {
    "verdict.strengths": "강점", "verdict.weaknesses": "약점",
    "verdict.overall_recommendation": "총평",
    "aspect.taste": "맛", "aspect.texture": "식감", "aspect.spec": "사양",
    "aspect.size": "크기/용량", "aspect.care": "관리/보관", "aspect.price_range": "가격대",
    "aspect.routine": "사용 루틴", "aspect.sensory": "향/감각",
    "context.who.age": "사용 연령", "context.who.gender": "성별", "context.who.household": "가구",
    "context.when.scene": "사용 상황", "context.when.season": "계절", "context.when.event": "이벤트",
    "context.where.place": "사용 장소",
    "context.why.positive_goal": "구매 목적", "context.why.negative_concern": "우려/불만",
    "context.gift.recipient": "선물 대상",
}


def md5(s): return hashlib.md5(s.encode("utf-8")).hexdigest()


def dim_label(dim_path):
    return DIM_LABELS.get(dim_path) or dim_path.split(".")[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-product", default=None)
    args = ap.parse_args()
    conn = psycopg2.connect()
    conn.autocommit = False
    cur = conn.cursor()

    # 처리 대상 상품
    if args.only_product:
        cur.execute("""SELECT uid, keyword, variant_value, type, category, flags, sources
                       FROM product WHERE uid = %s OR parent_uid = %s""",
                    (args.only_product, args.only_product))
    else:
        cur.execute("""SELECT uid, keyword, variant_value, type, category, flags, sources FROM product""")
    products = cur.fetchall()
    n_chunk = 0

    for uid, keyword, variant_value, type_, category, flags, sources in products:
        head = keyword if not variant_value else f"{keyword} {variant_value}"
        meta_base = {"keyword": keyword, "type": type_, "category": category,
                     "variant_value": variant_value, "flags": flags or {},
                     "source_counts": sources or {}}
        rows = []
        # points
        cur.execute("SELECT id, dim_path, text, cited_examples FROM point WHERE product_uid=%s", (uid,))
        for pid, dim_path, text, cited in cur.fetchall():
            content = f"{head} — {dim_label(dim_path)}: {text}"
            meta = dict(meta_base, dim=dim_path, cited=cited)
            rows.append((uid, "point", pid, dim_path, content, md5(content), Json(meta)))
        # faqs
        cur.execute("SELECT id, question, short_answer FROM faq WHERE product_uid=%s", (uid,))
        for fid, q, a in cur.fetchall():
            content = f"{head} — Q: {q} A: {a or ''}".strip()
            meta = dict(meta_base, qa=True)
            rows.append((uid, "faq", fid, None, content, md5(content), Json(meta)))

        # 멱등 재빌드: 같은 (product_uid, kind, ref_id)면 content/hash 갱신.
        # content_hash가 바뀌면 chunk_embedding이 stale → 재임베딩 단계가 골라냄.
        if rows:
            execute_values(cur, """
                INSERT INTO rag_chunk (product_uid, kind, ref_id, dim_path, content, content_hash, metadata)
                VALUES %s
                ON CONFLICT (product_uid, kind, ref_id) DO UPDATE
                  SET content=EXCLUDED.content, content_hash=EXCLUDED.content_hash,
                      dim_path=EXCLUDED.dim_path, metadata=EXCLUDED.metadata""", rows)
            n_chunk += len(rows)
        # 사라진 청크 정리(이번에 안 만든 ref는 삭제)
        cur.execute("""DELETE FROM rag_chunk c WHERE c.product_uid=%s AND NOT EXISTS (
            SELECT 1 FROM point  p WHERE p.product_uid=c.product_uid AND c.kind='point' AND c.ref_id=p.id
            UNION ALL
            SELECT 1 FROM faq    f WHERE f.product_uid=c.product_uid AND c.kind='faq'   AND c.ref_id=f.id)""",
            (uid,))
        conn.commit()
    conn.close()
    print(f"rag_chunk 빌드 완료: {n_chunk} chunks / {len(products)} products")


if __name__ == "__main__":
    main()
