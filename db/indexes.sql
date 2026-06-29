-- ============================================================================
-- 무거운 인덱스 — '적재 후' 일괄 생성용 (PostgreSQL)
-- 이유: trgm GIN·HNSW를 CREATE TABLE에 인라인하면 적재 INSERT마다 증분 유지 →
--       24k 상품 / 4M source / 350k chunk 적재가 시간~일 단위로 붕괴.
-- 순서: schema.sql 적용 → load.py 적재 → build_chunks.py(+임베딩) → 이 파일 실행.
-- ============================================================================

-- 빌드 세션 자원 상향(이 세션 한정). HNSW 그래프가 메모리에 들어가야 빠름.
SET maintenance_work_mem = '2GB';            -- 가능하면 4GB. halfvec(1024)×350k ≈ 0.7GB
SET max_parallel_maintenance_workers = 6;    -- 병렬 빌드

-- ── 한국어 부분일치(trgm) — 선택적. 1차 검색면을 rag_chunk로 단일화하면 최소만 ──
CREATE INDEX IF NOT EXISTS ix_chunk_content_tg ON rag_chunk USING gin(content gin_trgm_ops);
-- 아래는 정말 필요할 때만(저장/쓰기 비용 큼):
-- CREATE INDEX ix_point_text_tg  ON point  USING gin(text gin_trgm_ops);
-- CREATE INDEX ix_source_body_tg ON source USING gin(body gin_trgm_ops);   -- 4M행 ~4GB. 권장 안 함.

-- ── 벡터 HNSW(코사인). chunk_embedding.embedding(halfvec) 기준 ────────────────
-- 모델 픽스 후 차원이 확정되면 생성. m/ef_construction은 한국어 평가셋 recall@10으로 튜닝.
CREATE INDEX IF NOT EXISTS ix_emb_hnsw
  ON chunk_embedding USING hnsw (embedding halfvec_cosine_ops)
  WITH (m = 32, ef_construction = 128);
-- 질의 측 정확도: SET hnsw.ef_search = 80~120;

-- 대량 적재/인덱스 생성 후 통계 갱신
ANALYZE product; ANALYZE source; ANALYZE point; ANALYZE evidence;
ANALYZE faq; ANALYZE faq_evidence; ANALYZE rag_chunk; ANALYZE chunk_embedding;

-- 운영 메모:
--  · 한국어 고품질 FTS가 필요하면 PGroonga(형태소+랭킹, 인덱스 작음)를 trgm 대신 도입.
--  · 증분 재적재(소수 상품)는 인덱스를 유지한 채 진행. 전량 재적재(--bulk)만 drop→적재→재생성.
