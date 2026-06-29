-- ============================================================================
-- offer : 동일 SKU의 몰별 판매 리스팅 = '가격 사다리'
-- ----------------------------------------------------------------------------
-- product-identity-graph 편입 산출물. 네이버 쇼핑검색 정식 API로 모은 리스팅을
-- 나이키 스타일코드(DD8959-001 등)로 cross-mall 해소한 결과를 1급 데이터로 보존.
--   · product(변형 = 스타일코드 SKU)에 FK → 상품 단위 재적재 시 CASCADE로 함께 정리
--   · 기존 schema.sql 은 리뷰/인사이트(point/evidence/faq) 중심이라 가격 차원이 없음 → 이 파일이 보완
--   · 적재 순서: product(스키마/인사이트) 적재 후 → offer 적재 (product CASCADE가 offer를 지우므로)
-- 적용: psql -f db/schema.sql && psql -f db/schema_offer.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS offer (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_uid  text NOT NULL REFERENCES product(uid) ON DELETE CASCADE,
  mall         text NOT NULL,          -- 판매 몰/상호 (네이버 mallName)
  seller_kind  text,                   -- '셀러' | '마켓' | '가격비교' (link 도메인으로 판별)
  platform     text,                   -- '스마트스토어' | '머스트잇' | '11번가' ...
  price        int NOT NULL,           -- 표시가(원)
  used         boolean,                -- 중고/리셀 여부 (productType=2)
  style_code   text,                   -- 나이키 스타일코드 = 상품키
  shipping_fee int,                    -- 배송비. 네이버 쇼핑검색 API 미제공 → 현재 NULL(상세페이지 수집 시 채움)
  url          text,                   -- 리스팅 URL
  title        text,                   -- 원본 제목(증거 보존: 허위표기/구성 확인용)
  pulled_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_offer_product ON offer(product_uid);
CREATE INDEX IF NOT EXISTS ix_offer_style   ON offer(style_code);
CREATE INDEX IF NOT EXISTS ix_offer_mall     ON offer(mall);
CREATE INDEX IF NOT EXISTS ix_offer_used     ON offer(used);

-- ── 편의 뷰: SKU(변형 product)별 가격 사다리 요약 ────────────────────────────
--    같은 스타일코드의 몰 간 최저/최고/중앙값/가격폭/중고비율을 한 행으로.
CREATE OR REPLACE VIEW v_price_ladder AS
SELECT o.product_uid,
       p.keyword,
       o.style_code,
       count(*)                                   AS n_offers,
       count(DISTINCT o.mall)                     AS n_malls,
       min(o.price)                               AS min_price,
       max(o.price)                               AS max_price,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY o.price))::int AS median_price,
       round((max(o.price) - min(o.price))::numeric / NULLIF(min(o.price),0) * 100)::int AS spread_pct,
       sum((o.used)::int)                         AS n_used,
       (array_agg(o.mall ORDER BY o.price))[1]    AS lowest_mall
FROM offer o
JOIN product p ON p.uid = o.product_uid
GROUP BY o.product_uid, p.keyword, o.style_code;
