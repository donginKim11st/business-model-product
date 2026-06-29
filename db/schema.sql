-- ============================================================================
-- 1002 카탈로그 인사이트 DB — PostgreSQL 16 + pgvector 스키마
-- 설계 원칙
--   * 정규화(serving/분석) + raw_block JSONB(재처리 fidelity) 하이브리드
--   * taxonomy는 카테고리마다 leaf가 달라지므로 셀별 컬럼이 아니라 dim_path 문자열
--   * tree.sizes 변형은 별도 테이블이 아니라 parent_uid로 묶인 자식 product
--   * source의 S-id는 상품마다 S1부터 재시작 → PK = (product_uid, local_id)
--   * RAG는 정규화 테이블을 진실로 두고, 검색은 파생 테이블 rag_chunk에서
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;   -- 임베딩(의미 검색)
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- 한국어 부분일치 검색(LIKE 가속). 운영은 pgroonga 권장(주석 하단)

-- ── 0) 방법론 note 디덕(상품마다 동일한 ~1.5KB 보일러플레이트) ─────────────────
CREATE TABLE note (
  hash  text PRIMARY KEY,   -- md5(text)
  text  text NOT NULL
);

-- ── 1) product : 상품 단위 + 변형(자식) ──────────────────────────────────────
CREATE TABLE product (
  uid              text PRIMARY KEY,           -- "P7863" / 변형은 "P7863::92g"
  parent_uid       text REFERENCES product(uid) ON DELETE CASCADE,  -- 변형이면 base를 가리킴, base면 NULL
  bndl_grp         text,                       -- 번들 그룹(work_units.bndl_grp)
  type             text NOT NULL,              -- 'package' | 'standalone' | 'variant'
  variant_value    text,                       -- 변형의 용량/개수 라벨(예: '92g'), base면 NULL
  keyword          text NOT NULL,              -- 상품명
  category         text,                       -- 'food' | 'beauty' | ...  (배치 출처)

  analyzed_count   int,                        -- 분석에 쓰인 출처 수
  ad_flagged       int,                        -- 광고로 표시된 출처 수
  sources          jsonb,                      -- {"naver":20,"youtube":184,"danawa":20}
  verification     jsonb,                      -- {"verified":23,"partial":2,...} 검증 카운터
  overall_recommendation text,                 -- verdict.overall_recommendation (스칼라)
  flags            jsonb,                       -- {"is_gift_set":true,...} 불리언 플래그
  note_hash        text REFERENCES note(hash),  -- 방법론 note 참조(본문은 note 테이블)

  raw_block        jsonb NOT NULL,             -- 원본 block 전체(재처리/감사용 fidelity). source_index 제외 적재 권장(load.py)
  n_items          int,
  elapsed          double precision,
  ingested_at      timestamptz NOT NULL DEFAULT now(),
  -- 변형 라벨 안정성: 같은 base 안에서 (parent_uid, variant_value) 유일. base는 둘 다 NULL→충돌 없음.
  CONSTRAINT uq_variant UNIQUE (parent_uid, variant_value)
);
CREATE INDEX ix_product_parent   ON product(parent_uid);
CREATE INDEX ix_product_type     ON product(type);
CREATE INDEX ix_product_bndl     ON product(bndl_grp);
CREATE INDEX ix_product_category ON product(category);
CREATE INDEX ix_product_flags    ON product USING gin(flags jsonb_path_ops);   -- flags @> '{"is_gift_set":true}' (가벼움, 인라인 OK)
-- ※ 무거운 trgm/HNSW 인덱스는 db/indexes.sql 에서 '적재 후' 생성(인라인 시 적재 throughput 붕괴)

-- ── 2) source : 상품별 출처(원문 전체). evidence가 인용으로 참조 ───────────────
CREATE TABLE source (
  product_uid  text NOT NULL REFERENCES product(uid) ON DELETE CASCADE,
  local_id     text NOT NULL,                 -- "S1".."Sn" (상품 내부에서만 유의미)
  source       text,                          -- 'naver' | 'youtube' | 'danawa'
  kind         text,                          -- 'blog' | 'yt_comment' | 'danawa_review' ...
  is_ad        boolean,
  ad_signals   jsonb,
  author       text,
  date         text,                          -- 원본이 'YYYYMMDD' 문자열 → 텍스트 보존(+date_norm 파생)
  date_norm    date,                          -- 파싱 가능하면 정규화(분석/정렬용)
  url          text,
  title        text,
  rating       double precision,
  body         text,                          -- 원문 전체(source_index[Sx].text)
  PRIMARY KEY (product_uid, local_id)
);
CREATE INDEX ix_source_kind    ON source(source, kind);
CREATE INDEX ix_source_url     ON source(url);                          -- 상품 간 동일 URL 추적용(글로벌 디덕 대비)
-- ※ source.body 트그램 GIN(4M행에 ~4GB)은 db/indexes.sql 에서 '선택적'으로만. 1차 검색면은 rag_chunk 권장.

-- ── 3) point : taxonomy 셀의 관찰 1건(빈 셀은 행 없음 = sparse) ────────────────
CREATE TABLE point (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_uid    text NOT NULL REFERENCES product(uid) ON DELETE CASCADE,
  dim_path       text NOT NULL,               -- 'aspect.taste' / 'context.gift.recipient' / 'verdict.weaknesses'
  text           text NOT NULL,               -- point 문장
  cited_examples int,                          -- 빈도 proxy(검증된 근거 예시 수)
  ord            int                           -- 셀 내 순서
);
CREATE INDEX ix_point_product  ON point(product_uid);
CREATE INDEX ix_point_dim      ON point(dim_path);                          -- 카테고리 전체에서 한 차원 집계
CREATE INDEX ix_point_dim_prod ON point(dim_path, product_uid);
-- point.text 트그램 GIN은 db/indexes.sql(선택). 의미검색/패싯은 rag_chunk로 단일화 권장.

-- ── 4) evidence : point를 뒷받침하는 인용. source로 (product_uid, local_id) 조인 ─
CREATE TABLE evidence (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  point_id        bigint NOT NULL REFERENCES point(id) ON DELETE CASCADE,
  product_uid     text NOT NULL,              -- source 조인 스코프(상품 내부 local_id 때문에 필수)
  local_source_id text,                       -- "S208" → source(product_uid, local_id)
  quote           text NOT NULL,
  match           text,                        -- 'verified' | 'partial'
  ord             int,
  FOREIGN KEY (product_uid, local_source_id)
      REFERENCES source(product_uid, local_id) ON DELETE SET NULL
);
CREATE INDEX ix_evidence_point  ON evidence(point_id);
CREATE INDEX ix_evidence_source ON evidence(product_uid, local_source_id);

-- ── 5) faq : 상품별 Q&A ──────────────────────────────────────────────────────
CREATE TABLE faq (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_uid    text NOT NULL REFERENCES product(uid) ON DELETE CASCADE,
  question       text NOT NULL,
  short_answer   text,
  cited_examples int,
  ord            int
);
CREATE INDEX ix_faq_product ON faq(product_uid);
CREATE INDEX ix_faq_q_tg    ON faq USING gin(question gin_trgm_ops);

-- ── 6) faq_evidence : FAQ 질문/답변 근거 인용 ────────────────────────────────
CREATE TABLE faq_evidence (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  faq_id          bigint NOT NULL REFERENCES faq(id) ON DELETE CASCADE,
  product_uid     text NOT NULL,
  role            text NOT NULL,              -- 'answer' | 'question'
  local_source_id text,
  quote           text NOT NULL,
  match           text,
  ord             int,
  FOREIGN KEY (product_uid, local_source_id)
      REFERENCES source(product_uid, local_id) ON DELETE SET NULL
);
CREATE INDEX ix_faqev_faq    ON faq_evidence(faq_id);
CREATE INDEX ix_faqev_source ON faq_evidence(product_uid, local_source_id);

-- ── 7) rag_chunk : 검색면(정규화 테이블이 진실, 여기는 머티리얼라이즈) ───────────
--    point/faq를 1차 임베딩 단위로(짧은 source 4M 전량 임베딩 금지).
--    content는 상품·차원 컨텍스트를 합성해 변별력 확보(build_chunks.py): "<keyword> <variant> — <dim 라벨>: <text>"
CREATE TABLE rag_chunk (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_uid  text NOT NULL REFERENCES product(uid) ON DELETE CASCADE,
  kind         text NOT NULL,                 -- 'point' | 'faq'
  ref_id       bigint,                        -- point.id / faq.id
  dim_path     text,                          -- point일 때 차원(필터/패싯)
  content      text NOT NULL,                 -- 임베딩·반환할 합성 텍스트
  content_hash text NOT NULL,                 -- md5(content). 재추출로 바뀐 청크만 증분 재임베딩
  metadata     jsonb,                         -- {keyword,type,category,flags,dim,cited,source_counts,...}
  CONSTRAINT uq_chunk UNIQUE (product_uid, kind, ref_id)   -- 멱등 재빌드(중복 청크 방지)
);
CREATE INDEX ix_chunk_product ON rag_chunk(product_uid);
CREATE INDEX ix_chunk_kind    ON rag_chunk(kind);
CREATE INDEX ix_chunk_meta    ON rag_chunk USING gin(metadata jsonb_path_ops);   -- 메타 필터(가벼움)
-- content 트그램 GIN은 db/indexes.sql(적재 후)

-- ── 8) chunk_embedding : 임베딩 캐시(rag_chunk와 분리) ────────────────────────
--    분리 이유: (1) 모델/차원 공존·A/B (2) product 재적재 CASCADE가 임베딩을 안 날림
--               (3) content_hash 동일하면 재임베딩 0($ 절약).
--    차원은 픽스한 모델에 맞춤. 한국어 강모델(bge-m3=1024, multilingual-e5-large=1024) 권장.
--    OpenAI text-embedding-3-small을 쓰면 1024로 차원축소(dimensions=1024) 가능.
CREATE TABLE chunk_embedding (
  chunk_id     bigint NOT NULL REFERENCES rag_chunk(id) ON DELETE CASCADE,
  model        text   NOT NULL,               -- 'bge-m3' | 'text-embedding-3-small' ...
  content_hash text   NOT NULL,               -- 임베딩 당시 content_hash(불일치 = stale)
  embedding    halfvec(1024),                 -- halfvec: float16 → 메모리/인덱스 절반(0.8.3). 차원=모델
  embedded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, model)
);
-- HNSW 벡터 인덱스는 db/indexes.sql 에서 '적재 후' 생성(maintenance_work_mem 상향 + 병렬).

-- ── 편의 뷰: 상품 한 건의 point+evidence+source(serving용) ─────────────────────
--    ★ e.product_uid = p.product_uid 술어 필수: 없으면 evidence 전체 Seq Scan(목표 400k+행에서 폭발).
CREATE VIEW v_point_evidence AS
SELECT p.product_uid, p.dim_path, p.id AS point_id, p.text AS point_text, p.cited_examples,
       e.quote, e.match, s.source, s.kind, s.author, s.date, s.url, s.title, s.rating
FROM point p
LEFT JOIN evidence e ON e.point_id = p.id AND e.product_uid = p.product_uid
LEFT JOIN source   s ON s.product_uid = e.product_uid AND s.local_id = e.local_source_id;

-- ── 적용 순서 ────────────────────────────────────────────────────────────────
--   1) psql -f db/schema.sql           (테이블 + 가벼운 인덱스)
--   2) python db/load.py ...           (정규화 적재; 무거운 인덱스 없는 상태라 빠름)
--   3) python db/build_chunks.py ...   (rag_chunk 머티리얼라이즈 + 임베딩)
--   4) psql -f db/indexes.sql          (trgm GIN + HNSW를 '적재 후' 일괄 생성)
--
-- ── 운영 메모 ────────────────────────────────────────────────────────────────
-- 한국어 전문검색: pg_trgm은 부분일치/오타에 강하나 형태소 없음 → 고품질 필요시 PGroonga.
-- 글로벌 source 디덕(같은 URL이 여러 상품에 ~76% 중복): 대량 적재 전에 처리 권장.
--   source_global(content_hash PK, body 1회 저장) + product_source(product_uid, local_id, global_id) 분리,
--   evidence는 product_source 경유 조인. url NULL(yt댓글)은 content_hash로 키잉.
-- dim_path 표준화: 카테고리 간 차원 흔들림 방지용 dim 마스터(dim_path PK, category, label_ko, is_servable)
--   를 두고 point.dim_path를 검증(FK 또는 적재 후 diff 리포트). sparse 패싯 UI의 '분모' 역할도 겸함.
-- evidence FK: 본 로더는 상품 단위 DELETE-CASCADE 후 source+evidence를 함께 재적재 →
--   고아 인용이 생기지 않음. 부분 업데이트 도입 시 ON DELETE SET NULL 대신 RESTRICT 검토.
