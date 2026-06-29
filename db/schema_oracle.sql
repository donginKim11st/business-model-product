-- ============================================================================
-- 1002 카탈로그 인사이트 DB — Oracle Database 23ai 스키마
-- (PostgreSQL 판 db/schema.sql 의 논리 모델을 Oracle로 1:1 이식)
--
-- ★ 버전 전제: Oracle 23ai (23c). RAG/벡터 검색에 네이티브 VECTOR 타입이 필요하기 때문.
--   - 23ai 이상: 아래 그대로 동작(네이티브 VECTOR/JSON/BOOLEAN, 벡터 인덱스).
--   - 19c/21c  : VECTOR 타입 없음 → 임베딩은 (a) Oracle 밖 벡터스토어로 분리하거나
--                (b) BLOB로 보관 후 외부 검색. JSON/BOOLEAN 폴백은 파일 하단 주석 참고.
--
-- ★ 선행 설정 권장(한 번):
--   ALTER SYSTEM SET MAX_STRING_SIZE=EXTENDED SCOPE=SPFILE;  -- VARCHAR2 4000→32767 (한글 멀티바이트 여유)
--   (재시작 필요. 안 하면 긴 한글 컬럼은 CLOB로 두면 됨 — 아래는 EXTENDED 가정)
--   모든 VARCHAR2는 CHAR 의미(바이트 아님)로 선언.
-- ============================================================================

-- ── 0) 방법론 note 디덕 ──────────────────────────────────────────────────────
CREATE TABLE note (
  hash  VARCHAR2(64 CHAR) PRIMARY KEY,   -- md5(text)
  text  CLOB NOT NULL
);

-- ── 1) product : 상품 단위 + 변형(자식) ──────────────────────────────────────
-- ※ Oracle에서 'uid'는 예약어 → PK 컬럼명을 product_uid로(자식 FK 컬럼명과 일치, PG판의 product.uid에 해당).
CREATE TABLE product (
  product_uid            VARCHAR2(200 CHAR) PRIMARY KEY,                 -- "P7863" / 변형 "P7863::92g"
  parent_uid             VARCHAR2(200 CHAR),                             -- 변형이면 base, base면 NULL
  bndl_grp               VARCHAR2(100 CHAR),
  type                   VARCHAR2(40 CHAR)  NOT NULL,                    -- package | standalone | variant
  variant_value          VARCHAR2(200 CHAR),
  keyword                VARCHAR2(1000 CHAR) NOT NULL,
  category               VARCHAR2(40 CHAR),

  analyzed_count         NUMBER,
  ad_flagged             NUMBER,
  sources                JSON,                                          -- {"naver":20,...}
  verification           JSON,                                          -- {"verified":23,...}
  overall_recommendation VARCHAR2(2000 CHAR),
  flags                  JSON,                                          -- {"is_gift_set":true,...}
  note_hash              VARCHAR2(64 CHAR),
  raw_block              JSON NOT NULL,                                 -- 원본 block 전체(OSON 바이너리 저장)
  n_items                NUMBER,
  elapsed                BINARY_DOUBLE,
  ingested_at            TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,

  CONSTRAINT fk_product_parent FOREIGN KEY (parent_uid) REFERENCES product(product_uid) ON DELETE CASCADE,
  CONSTRAINT fk_product_note   FOREIGN KEY (note_hash)  REFERENCES note(hash),
  -- 변형 라벨 안정성: 같은 base 안에서 (parent_uid, variant_value) 유일. base는 둘 다 NULL.
  CONSTRAINT uq_variant UNIQUE (parent_uid, variant_value)
);
CREATE INDEX ix_product_parent   ON product(parent_uid);
CREATE INDEX ix_product_type     ON product(type);
CREATE INDEX ix_product_bndl     ON product(bndl_grp);
CREATE INDEX ix_product_category ON product(category);
-- flags 임의 경로 질의용 JSON 검색 인덱스 (PG의 GIN(flags) 대응)
CREATE SEARCH INDEX ix_product_flags ON product (flags) FOR JSON;
-- keyword 한국어 전문검색(PG의 gin_trgm 대응 — Oracle Text 형태소)
CREATE INDEX ix_product_kw_txt ON product(keyword)
  INDEXTYPE IS CTXSYS.CONTEXT PARAMETERS('LEXER kor_lexer SYNC (ON COMMIT)');

-- ── 2) source : 상품별 출처(원문 전체) ───────────────────────────────────────
CREATE TABLE source (
  product_uid  VARCHAR2(200 CHAR) NOT NULL,
  local_id     VARCHAR2(40 CHAR)  NOT NULL,         -- "S1".."Sn" (상품 내부에서만 유의미)
  source       VARCHAR2(40 CHAR),                   -- naver | youtube | danawa
  kind         VARCHAR2(60 CHAR),
  is_ad        BOOLEAN,                             -- 23ai 네이티브 BOOLEAN (폴백: NUMBER(1))
  ad_signals   JSON,
  author       VARCHAR2(1000 CHAR),
  date_raw     VARCHAR2(8 CHAR),                    -- 'YYYYMMDD' 원형 보존 ("date"는 Oracle 예약어라 date_raw)
  date_norm    DATE,
  url          VARCHAR2(2000 CHAR),
  title        VARCHAR2(1000 CHAR),
  rating       BINARY_DOUBLE,
  body         CLOB,                                -- 원문 전체
  CONSTRAINT pk_source PRIMARY KEY (product_uid, local_id),
  CONSTRAINT fk_source_product FOREIGN KEY (product_uid) REFERENCES product(product_uid) ON DELETE CASCADE
);
CREATE INDEX ix_source_kind ON source(source, kind);
CREATE INDEX ix_source_url  ON source(url);
CREATE INDEX ix_source_body_txt ON source(body)
  INDEXTYPE IS CTXSYS.CONTEXT PARAMETERS('LEXER kor_lexer SYNC (ON COMMIT)');

-- ── 3) point : taxonomy 셀 관찰 1건(빈 셀은 행 없음) ─────────────────────────
CREATE TABLE point (
  id             NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_uid    VARCHAR2(200 CHAR) NOT NULL,
  dim_path       VARCHAR2(200 CHAR) NOT NULL,        -- 'aspect.taste' 등
  text           VARCHAR2(2000 CHAR) NOT NULL,
  cited_examples NUMBER,
  ord            NUMBER,
  CONSTRAINT fk_point_product FOREIGN KEY (product_uid) REFERENCES product(product_uid) ON DELETE CASCADE
);
CREATE INDEX ix_point_product  ON point(product_uid);
CREATE INDEX ix_point_dim      ON point(dim_path);
CREATE INDEX ix_point_dim_prod ON point(dim_path, product_uid);
CREATE INDEX ix_point_text_txt ON point(text)
  INDEXTYPE IS CTXSYS.CONTEXT PARAMETERS('LEXER kor_lexer SYNC (ON COMMIT)');

-- ── 4) evidence : point 근거 인용 ────────────────────────────────────────────
CREATE TABLE evidence (
  id              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  point_id        NUMBER NOT NULL,
  product_uid     VARCHAR2(200 CHAR) NOT NULL,
  local_source_id VARCHAR2(40 CHAR),
  quote           VARCHAR2(4000 CHAR) NOT NULL,
  match           VARCHAR2(20 CHAR),                  -- verified | partial
  ord             NUMBER,
  CONSTRAINT fk_evid_point  FOREIGN KEY (point_id) REFERENCES point(id) ON DELETE CASCADE,
  CONSTRAINT fk_evid_source FOREIGN KEY (product_uid, local_source_id)
      REFERENCES source(product_uid, local_id) ON DELETE SET NULL
);
CREATE INDEX ix_evidence_point  ON evidence(point_id);
CREATE INDEX ix_evidence_source ON evidence(product_uid, local_source_id);

-- ── 5) faq ───────────────────────────────────────────────────────────────────
CREATE TABLE faq (
  id             NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_uid    VARCHAR2(200 CHAR) NOT NULL,
  question       VARCHAR2(2000 CHAR) NOT NULL,
  short_answer   VARCHAR2(2000 CHAR),
  cited_examples NUMBER,
  ord            NUMBER,
  CONSTRAINT fk_faq_product FOREIGN KEY (product_uid) REFERENCES product(product_uid) ON DELETE CASCADE
);
CREATE INDEX ix_faq_product ON faq(product_uid);

-- ── 6) faq_evidence ──────────────────────────────────────────────────────────
CREATE TABLE faq_evidence (
  id              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  faq_id          NUMBER NOT NULL,
  product_uid     VARCHAR2(200 CHAR) NOT NULL,
  role            VARCHAR2(20 CHAR) NOT NULL,         -- answer | question
  local_source_id VARCHAR2(40 CHAR),
  quote           VARCHAR2(4000 CHAR) NOT NULL,
  match           VARCHAR2(20 CHAR),
  ord             NUMBER,
  CONSTRAINT fk_faqev_faq    FOREIGN KEY (faq_id) REFERENCES faq(id) ON DELETE CASCADE,
  CONSTRAINT fk_faqev_source FOREIGN KEY (product_uid, local_source_id)
      REFERENCES source(product_uid, local_id) ON DELETE SET NULL
);
CREATE INDEX ix_faqev_faq    ON faq_evidence(faq_id);
CREATE INDEX ix_faqev_source ON faq_evidence(product_uid, local_source_id);

-- ── 7) rag_chunk : 검색면(point/faq 1차 단위. content는 상품·차원 컨텍스트 합성) ─────
CREATE TABLE rag_chunk (
  id              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_uid     VARCHAR2(200 CHAR) NOT NULL,
  kind            VARCHAR2(20 CHAR) NOT NULL,         -- point | faq
  ref_id          NUMBER,
  dim_path        VARCHAR2(200 CHAR),
  content         CLOB NOT NULL,
  content_hash    VARCHAR2(64 CHAR) NOT NULL,         -- md5(content): 바뀐 청크만 증분 재임베딩
  metadata        JSON,
  CONSTRAINT fk_chunk_product FOREIGN KEY (product_uid) REFERENCES product(product_uid) ON DELETE CASCADE,
  CONSTRAINT uq_chunk UNIQUE (product_uid, kind, ref_id)   -- 멱등 재빌드
);
CREATE INDEX ix_chunk_product ON rag_chunk(product_uid);
CREATE INDEX ix_chunk_kind    ON rag_chunk(kind);
CREATE SEARCH INDEX ix_chunk_meta ON rag_chunk (metadata) FOR JSON;     -- 적재 후 생성 권장
CREATE INDEX ix_chunk_content_txt ON rag_chunk(content)                 -- 적재 후 생성 권장
  INDEXTYPE IS CTXSYS.CONTEXT PARAMETERS('LEXER kor_lexer SYNC (ON COMMIT)');

-- ── 8) chunk_embedding : 임베딩 캐시(rag_chunk와 분리 — 다모델·재적재 보존·증분 재임베딩) ──
CREATE TABLE chunk_embedding (
  chunk_id     NUMBER NOT NULL,
  model        VARCHAR2(100 CHAR) NOT NULL,           -- 'bge-m3' | 'text-embedding-3-small' ...
  content_hash VARCHAR2(64 CHAR) NOT NULL,            -- 임베딩 당시 hash(불일치=stale)
  embedding    VECTOR(1024, FLOAT32),                 -- 차원=픽스한 모델(한국어 강모델 1024 권장)
  embedded_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_chunk_emb PRIMARY KEY (chunk_id, model),
  CONSTRAINT fk_chunk_emb FOREIGN KEY (chunk_id) REFERENCES rag_chunk(id) ON DELETE CASCADE
);
-- 벡터 인덱스: 적재·임베딩 후 생성.
--   HNSW(인메모리, 빠름)는 VECTOR_MEMORY_SIZE 풀 필요:
--     ALTER SYSTEM SET VECTOR_MEMORY_SIZE=1G SCOPE=SPFILE;  (재시작)
--     CREATE VECTOR INDEX ix_emb ON chunk_embedding(embedding)
--       ORGANIZATION INMEMORY NEIGHBOR GRAPH DISTANCE COSINE WITH TARGET ACCURACY 95;
--   IVF(온디스크, 메모리풀 불필요)는 대량/메모리 제약에 적합:
--     CREATE VECTOR INDEX ix_emb ON chunk_embedding(embedding)
--       ORGANIZATION NEIGHBOR PARTITIONS DISTANCE COSINE WITH TARGET ACCURACY 90;

-- ── 편의 뷰: serving용 point+evidence+source ─────────────────────────────────
-- ★ e.product_uid = p.product_uid 술어 필수: 없으면 evidence 전체 스캔(목표 400k+행에서 폭발).
CREATE OR REPLACE VIEW v_point_evidence AS
SELECT p.product_uid, p.dim_path, p.id AS point_id, p.text AS point_text, p.cited_examples,
       e.quote, e.match, s.source, s.kind, s.author, s.date_raw, s.url, s.title, s.rating
FROM point p
LEFT JOIN evidence e ON e.point_id = p.id AND e.product_uid = p.product_uid
LEFT JOIN source   s ON s.product_uid = e.product_uid AND s.local_id = e.local_source_id;

-- ============================================================================
-- 부록 A) 한국어 Oracle Text lexer (위 CONTEXT 인덱스들의 'kor_lexer'). 인덱스 생성 전 1회.
--   BEGIN
--     CTX_DDL.CREATE_PREFERENCE('kor_lexer', 'KOREAN_MORPH_LEXER');
--   END;
--   /
--   ※ Oracle Text의 KOREAN_MORPH_LEXER는 형태소 분석 기반 — PG의 pg_trgm보다 한국어 검색 품질이 좋다(Oracle 강점).
--
-- 부록 B) 대표 쿼리(PG 판과 동일 워크로드)
--   -- flag 필터 (PG: flags @> '{"is_gift_set":true}')
--   SELECT uid, keyword FROM product
--   WHERE JSON_EXISTS(flags, '$?(@.is_gift_set == true)');
--   -- 벡터 의미검색 (PG: ORDER BY embedding <=> :q LIMIT k)
--   SELECT id, content FROM rag_chunk
--   ORDER BY VECTOR_DISTANCE(embedding, :qvec, COSINE)
--   FETCH APPROX FIRST 10 ROWS ONLY;
--   -- 하이브리드: 메타필터 + 한국어 전문검색
--   SELECT id, content FROM rag_chunk
--   WHERE JSON_EXISTS(metadata, '$?(@.dim == "verdict.weaknesses")')
--     AND CONTAINS(content, '국물') > 0;
--
-- 부록 C) 24k 스케일 — Oracle 강점: 참조 파티셔닝으로 자식 행을 부모 파티션에 동치(co-locate)
--   product 를 category LIST 파티션 → source/point/evidence/faq 를 REFERENCE 파티션:
--     CREATE TABLE product (...) PARTITION BY LIST (category) (PARTITION p_food VALUES ('food'), ...);
--     CREATE TABLE source  (...) PARTITION BY REFERENCE (fk_source_product);
--   → 카테고리 단위 적재/삭제/조회가 파티션 프루닝으로 빨라지고, 대량 재처리 시 파티션 TRUNCATE 활용.
--
-- 부록 D) 19c/21c 폴백 (네이티브 VECTOR 없음)
--   - embedding 컬럼 제거. rag_chunk 는 본문/메타데이터/Oracle Text 검색만.
--   - 의미검색은 외부 벡터스토어(예: pgvector/OpenSearch/전용 벡터DB)로 분리하고 ref_id로 조인.
--   - JSON 타입 미지원(<21c) → CLOB + CONSTRAINT chk CHECK (col IS JSON).
--   - BOOLEAN 미지원(<23ai) → is_ad NUMBER(1) CHECK (is_ad IN (0,1)).
-- ============================================================================
