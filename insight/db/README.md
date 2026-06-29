# 인사이트 DB 설계

LLM이 추출한 상품 인사이트(`insights_*.jsonl`)를 **앱/API 서빙 + RAG/LLM 콘텐츠 생성 + 원본 보관·재처리**에 쓰기 위한 데이터베이스 설계. 실제 20개 레코드를 PostgreSQL·Oracle 양쪽에 적재해 검증함.

## 1. DB 추천: PostgreSQL 16 + pgvector

| 후보 | 판정 | 이유 |
|---|---|---|
| **PostgreSQL + pgvector** | ✅ **기본 채택** | 서빙+RAG+분석+무결성을 한 무료 엔진에. pgvector 벡터검색 turnkey, FK로 인용·디덕 무결성 강제, SQL 집계·하이브리드 1급. 자가호스팅. |
| MongoDB (Atlas) | ◯ **유력 대안** | 데이터가 이미 문서형 → serving=`findOne` 1회(조인 0), 재처리=문서 교체, taxonomy 가변성 무모델 수용. **단 벡터검색은 Atlas 사실상 전제**(managed $/락인), 크로스상품 집계는 덜 매끄럽고 인용/디덕 무결성은 앱 책임. → `*_mongo.py` |
| Oracle 23ai | ◯ 대안 | 동등 가능(네이티브 VECTOR/JSON/BOOLEAN). 한국어 FTS(형태소 lexer)·파티셔닝 강함. 단 라이선스, RAG는 23ai 필수. → `schema_oracle.sql` |
| DuckDB | ✗ | 로컬 분석 전용. 멀티유저 서빙/RAG 부적합(용도 불일치). |

**규모는 결정요인이 아님**(어느 DB에도 24k/4M은 작음). 진짜 갈림:
- **자가호스팅·무료·무결성 강제·SQL 분석 중시** → **PostgreSQL**.
- **managed 선호(Atlas) + serving/재처리가 지배적 + 문서형 개발 속도** → **MongoDB**.
- 셋 다 실측 결과 동일(아래 §3). MongoDB도 RAG·source 디덕 때문에 결국 3컬렉션(products/sources/chunks)으로 수렴 → "단일 문서" 환상보다는 관계형 설계와 유사하게 가되 serving 임베드가 이득, 무결성이 비용.

## 2. 데이터 모델

추출 JSON의 본질 = **고정된 계층형 taxonomy의 각 셀이 point(주장) 리스트이고, 각 point가 evidence(출처 인용)로 뒷받침됨**. 이를 다음 엔티티로 정규화:

```
product ──┐ (parent_uid 자기참조: tree.sizes 변형 = 자식 product)
          ├── source        (product당 S1..Sn 원문, PK=(product_uid,local_id))
          ├── point          (taxonomy 셀 관찰 1건, dim_path 문자열)
          │     └── evidence (point 근거 인용 → source로 (product_uid,local_source_id) 조인)
          ├── faq → faq_evidence
          └── rag_chunk       (검색면: point/faq를 컨텍스트 합성해 머티리얼라이즈)
                └── chunk_embedding (임베딩 캐시: 모델/차원 분리, content_hash로 증분)
note  (보일러플레이트 방법론 텍스트 디덕)
```

**핵심 설계 결정 (검증으로 '옳음' 확인):**
- **변형 = parent_uid 자식 product** — 별도 테이블 아님. base의 `block`은 1차, `tree.sizes[].block`은 변형별 인사이트.
- **taxonomy는 `point.dim_path` 문자열** (예 `aspect.taste`) — 셀별 컬럼 ✗. 카테고리(식품/뷰티)마다 leaf가 달라도 스키마 변경 없이 수용. 로더가 트리를 재귀 워킹.
- **sparse**: 빈 셀은 행 없음. 한 상품 ~30 point만 저장(전체 차원의 일부).
- **source는 product 스코프** — S-id가 상품마다 S1부터 재시작하므로 복합 PK. evidence도 `(product_uid, local_source_id)`로 조인.
- **하이브리드 보관**: 정규화 테이블(서빙/분석) + `raw_block` JSONB(재처리 fidelity, source_index 제외해 중복 제거).
- **RAG는 정규화 테이블이 진실, `rag_chunk`는 파생 검색면.** 임베딩은 `chunk_embedding`으로 분리 → 모델 A/B·차원 공존, 상품 재적재가 임베딩을 안 날림, `content_hash` 불일치 청크만 재임베딩.

## 3. 실증 검증 (종이 설계 아님)

Docker로 **PostgreSQL 16.14 + pgvector 0.8.3** / **Oracle 26ai(23.26) Free** / **MongoDB 8.3.4 (Atlas Local)**를 띄워 실제 20레코드 적재 — 셋 다 **동일 카운트**:

| 검증 항목 | PostgreSQL | Oracle | MongoDB |
|---|---|---|---|
| 적재(base 20 + 변형 25) | ✅ product 45, point 251, evidence 415, source 5,853, faq 73 | ✅ 동일 | ✅ products 45, sources 5,853, chunks 324 |
| 멱등 재실행 | ✅ | ✅ | ✅ |
| serving | ✅ 뷰 조인 | ✅ | ✅ **findOne 1회(조인 0, taxonomy 임베드)** |
| 크로스-상품 집계(`verdict.strengths` 30상품 40point) | ✅ | ✅ 동일 | ✅ 동일(chunks 평탄화면) |
| 플래그 필터 | ✅ GIN | ✅ JSON_EXISTS | ✅ `flags.is_premium` 인덱스 |
| 변형 부모-자식 | ✅ | ✅ | ✅ |
| 벡터 검색 | ✅ HNSW | ✅ IVF | ✅ **Atlas `$vectorSearch`** |
| 하이브리드(메타필터+벡터) | ✅ | ✅ | ✅ |

MongoDB 약점도 실측: 임베드된 taxonomy를 직접 크로스상품 집계하려면 경로 하드코딩 필요(가변 차원에 취약) → `chunks` 컬렉션이 평탄화로 우회. 벡터검색은 Atlas/Atlas Local 전용(self-host Community엔 없음).

PG/Oracle 비평 수정(MF3/4/5)도 **수정 스키마 재검증**: Postgres reset→재적재→`indexes.sql`까지, Oracle 재적용 후 `uq_variant` all-NULL·`uq_chunk`·`chunk_embedding(1024)` 확인.

비평 수정(MF3/4/5)을 가한 **수정 스키마도 양쪽 재검증**: Postgres는 reset→전체 재적재→`indexes.sql`까지, Oracle은 수정 스키마 재적용 후 스모크 적재로 `uq_variant` all-NULL(base 20개 공존)·`uq_chunk` 중복거부·`chunk_embedding(1024)` 벡터검색 확인.

운영상 발견: Oracle HNSW는 `VECTOR_MEMORY_SIZE` 풀 필요(없으면 IVF 사용), Oracle Text(한국어 형태소 lexer)는 별도 설치 필요. `load_oracle.py`는 **레퍼런스급** — 레코드별 commit은 적용했으나 point/faq는 아직 행별 RETURNING(24k 적재 시 `load.py`처럼 배치化 권장).

## 4. 파일 & 적용 순서

```
db/schema.sql        # PostgreSQL DDL (테이블 + 가벼운 인덱스)
db/load.py           #   JSONL → PG 적재 (배치 INSERT, 레코드별 commit=재개안전, 변형키 가드)
db/build_chunks.py   #   point/faq → rag_chunk (컨텍스트 합성 + content_hash + 멱등)
db/indexes.sql       #   무거운 인덱스(trgm GIN, HNSW)를 '적재 후' 일괄 생성
db/schema_oracle.sql # Oracle 23ai 대안 DDL
db/load_oracle.py    #   JSONL → Oracle 적재
db/load_mongo.py     # MongoDB 대안: products/sources/chunks 3컬렉션 적재(serving=findOne)
db/mongo_setup.py    #   chunks 벡터검색 인덱스(Atlas Vector Search) + 한국어 텍스트 인덱스
```
```bash
# PostgreSQL
psql -f db/schema.sql
python db/load.py insights_1002.jsonl --category food --work-units work_units.jsonl
python db/build_chunks.py
# (임베딩 모델 선정·임베딩 → chunk_embedding 채우기)
psql -f db/indexes.sql
```

## 5. 적대적 비평 반영 (5렌즈 × 라이브 EXPLAIN)

**적용 완료 (must-fix):**
1. 로더 단일 트랜잭션 → **레코드별 commit**(중간 크래시 시 단순 재실행으로 재개) + point/faq **배치 INSERT**(라운드트립 ~320k→block당 2회).
2. 무거운 GIN/HNSW를 테이블 DDL에서 분리 → **`indexes.sql`(적재 후)** + `maintenance_work_mem` 상향 + `halfvec`로 빌드 메모리 절반.
3. `v_point_evidence` 뷰에 `e.product_uid=p.product_uid` 추가 → **evidence 전체 스캔 제거**(검증됨).
4. 변형 합성키 가드(None/빈문자/`::` 차단) + **`UNIQUE(parent_uid, variant_value)`**.
5. 임베딩 **`chunk_embedding` 분리 + `content_hash`** → 한국어 모델 전환·증분 재임베딩 가능.
6. (RAG 주용도) **`build_chunks.py` 신설** — 컨텍스트 합성 content + 풍부 metadata + 멱등.

**로드맵 (대량 적재 전 권장 / 스키마 주석에 경로 명시):**
- **글로벌 source 디덕**: 같은 URL이 상품마다 중복(실측 76%) → `source_global`(content_hash PK) + `product_source` 매핑 분리. 4M 적재 전이 가장 싼 시점.
- **dim_path 마스터 테이블**: 카테고리 간 차원 표준화(오타/동의어 방지), sparse 패싯 UI의 '분모'.
- **임베딩 모델 확정**: 전량 한국어 데이터 → bge-m3/multilingual-e5(1024) vs OpenAI 3-small을 한국어 평가셋 recall@10으로 비교 후 픽스.
- **한국어 FTS 업그레이드**: pg_trgm(부분일치) → 필요 시 PGroonga(형태소+랭킹).
- source 4M 전량 임베딩 금지 — point+faq만 임베딩, 원문 근거는 evidence→source 조인으로 회수.

> 총평(비평 종합): **관계형 코어는 건강하고 재설계 불필요. "집중된 사전-스케일 수정셋 후 진행"** — 위 must-fix가 그 수정셋이며 모두 반영됨.

---

## 6. 서빙 큐레이션 — YouTube 디커플 + 카테고리 대표 인사이트

원천 추출(taxonomy 전체)은 **보존**하고, 고객 노출만 큐레이션하는 서빙 레이어. 카테고리 계층:

```
카테고리(DISP_CTGR1_NM) → 번들(bndl_grp, package) → 카탈로그(ctlg_no, 변형/SKU) → 속성(dim_path) → point
```

### 6-1. YouTube 디커플링 (메인 배치 무정지)
YouTube Data API 일일 쿼터는 작다(search.list=100 units/키워드 → 1만 쿼터면 ~95키워드/일). 24k 번들엔
수개월이 걸려 메인 인사이트 배치를 막으면 안 된다. 그래서:
- `run_batch.collect()` 는 **네이버(+다나와)만** 수집한다(YouTube 분기 제거, `QuotaStop` 무력화 → 무정지).
  예전 인라인 동작은 `INSIGHT_INLINE_YT=1` escape hatch 로만.
- 적재 product 는 `youtube={status:"pending"}` placeholder 로 시작.
- **`db/youtube_backfill.py`** 가 매일 쿼터 예산(`--daily-units`, 기본 9000) 안에서 pending 큐를 **우선순위**
  (기존 리뷰량·다중몰 가중)대로 처리 → `collect_youtube` → 유튜브 근거로만 **전용 인사이트(taxonomy/faqs)**
  추출 → `product.youtube` 에 누적 + 원문은 `sources(kind="youtube")`. 쿼터 소진/오류 시 우아하게 중단,
  다음날 재개(`status` 기반 멱등, `attempts`/`last_error` 추적, `--max-attempts` 초과 시 `error` 고정).
  기본은 **번들 base 만**(변형 제외 — 유튜브 댓글은 base 일반 내용; `--include-variants` 로 포함).

### 6-2. 카테고리 속성 랭킹 + 번들 '대표' (트리 전부노출 → 큐레이션)
문제: 트리(1·2·3차)로 속성을 전부 뽑으니 희소 속성(`verdict.trust.*`, `context.when.scene` 등 극소수
카탈로그에만 있는 것)까지 고객에 노출. 해법(**추출 불변, 노출만 큐레이션**):
- **`db/category_rank.py`** 가 카테고리별로 그 안 카탈로그를 훑어 속성(dim)별 **coverage**(언급 비율)와
  `cited_examples` 합을 집계 → `category_attribute_rank` 컬렉션(카테고리별 랭킹).
- 선정 = **하이브리드**: 상위 N(`--top-n`, 기본 5) ∩ 최소 커버리지(`--min-coverage`, 기본 0.20).
- **coverage 분모는 기본 `bundle`**(`--coverage-by`): 변형 델타가 일반 속성(맛/식감)을 희석하는 문제를 피함.
  (실측: 맛 catalog-coverage 19% → bundle-coverage 80%.)
- 각 번들에 `representative` 필드 materialize — 그 번들 카탈로그들(base+변형) 합집합에서 대표 dim 별
  `cited_examples` 상위 `--per-dim`(기본 3) point + evidence. **taxonomy 원천은 불변 → 완전 가역.**
- 식품(인사이트=base)·신발(인사이트=변형) 양쪽 모델 모두 동작(번들=base+변형 rollup).

### 6-3. 번들 카테고리 매핑(Oracle, canonical)
`DISP_CTGR1_NM` 은 Oracle `pd_ctlg` 에만 있어 repo 밖. **`db/export_bndl_category.py`** 가
`pd_ctlg c LEFT JOIN DP_DISP_CTGR_LIST l ON c.DISP_CTGR_NO=l.DISP_CTGR_NO` (카탈로그 최빈 투표)로
`bndl_category.jsonl`(bndl_grp→ctgr1/path) export → `load_mongo.py --bndl-category` 가 소비해
`product.category_l1` 채움(없으면 coarse `--category` 폴백). 접속은 `archive/db_extract.py` 와 동일
(thick `oracledb`, `ORA_USER/PW/HOST/PORT/SID`).

### 6-4. 신규 스키마(추가 필드/컬렉션)
- `products.category_l1` / `category_path` / `category_source(oracle|fallback)` — 랭킹 grouping 키.
- `products.youtube{status,taxonomy,faqs,n_sources,n_videos,fetched_at,attempts,last_error}` — backfill 산출.
- `products.representative{category,params,dims:[{dim,label,rank,coverage,points:[{point,cited_examples,evidence}]}]}`.
- `sources` 에 `kind:"youtube"` 원문(_id=`uid:yt:<sid>`).
- 신규 컬렉션 `category_attribute_rank`(_id=카테고리, `top_dims`/`ranked_dims`/`params`/`generated_at`).
- **멱등 보존**: 인사이트 재적재(`load_mongo`)는 ReplaceOne 이지만 `youtube`(backfill)·`representative`
  (주간 배치) 는 비동기 산출이라 기존 값을 머지 보존(덮어쓰기 방지).

### 6-5. 배치 자동화 (cron / launchd)
무정지 메인 배치(`resume_1002.sh`)와 별개로 두 배치 래퍼(키 로드·락·로그는 `resume_1002.sh` 관례 동일):
```
db/run_food_price_backfill.sh  # 매일 — 식품 카탈로그 크로스몰 가격 + 추이 스냅샷 → db/food_price_backfill.log
db/run_youtube_backfill.sh     # 매일 — 쿼터 예산만큼 youtube 채움 → db/youtube_backfill.log
db/run_category_rank.sh        # 주 1회 — 카테고리 랭킹 + 대표 갱신 → db/category_rank.log
```
crontab 예(매일 02:00 가격/추이, 03:00 youtube, 매주 일요일 04:00 랭킹):
```cron
0 2 * * *  /Users/a1101417/Work/business-model/db/run_food_price_backfill.sh
0 3 * * *  /Users/a1101417/Work/business-model/db/run_youtube_backfill.sh
0 4 * * 0  /Users/a1101417/Work/business-model/db/run_category_rank.sh
```
`run_food_price_backfill.sh` 환경변수: `FOOD_PRICE_ALL=1`(대표 패키지만→전체), `FOOD_PRICE_LIMIT=N`(일일
네이버 호출 상한 보호), `FOOD_PRICE_DISPLAY`/`FOOD_PRICE_CAP`. 매일 `--refresh` 라 그날 현재가가
`price_history` 에 1행 쌓여 추이가 누적된다(첫날 1점, 2주면 2주치).

### 6-6. 적용 순서
```bash
# (1) Oracle 카테고리 export  → bndl_category.jsonl
ORA_USER=.. ORA_PW=.. python3 db/export_bndl_category.py --reg-typ 1002,801
# (2) 인사이트 적재(카테고리 주입)
MONGO_URI=.. python3 db/load_mongo.py insights_1002.jsonl --category food \
  --work-units work_units.jsonl --bndl-category bndl_category.jsonl
# (3) 주간 랭킹/대표(첫 수동 1회 + cron)
MONGO_URI=.. python3 db/category_rank.py            # --dry-run 으로 먼저 확인 가능
# (4) 매일 youtube backfill(별도, 천천히)
MONGO_URI=.. python3 db/youtube_backfill.py --dry-run   # 큐 확인 → 실제는 키 로드 후
```

### 6-7. 랭킹 관점(rank-by) · 소수 카테고리 가드 · 셀러뷰/고객뷰
**랭킹 관점** `category_rank.py --rank-by`:
- `hybrid`(coverage×lift, **기본값**) — 흔하면서도 그 카테고리에 특징적. 카테고리가 서로 구분되게 보임.
  실측: 생수=음용빈도·원산지·라이프스타일, 소스=가구·용량·보관, 즉석밥=식감·건강.
- `coverage`('가장 많이 언급'만) — 강점/맛/사양처럼 전역에서 흔한 속성이 상위 → 모든 카테고리 top-5가 비슷.
- `lift` — 전역 baseline 대비 과대표(순수 차별화). 저커버리지 noise가 끼어 raw로는 비권장.

**강점 고정**(`--pin-dims`, 기본 `verdict.strengths`): hybrid 가 보편 속성(강점)을 top-N 밖으로 밀어내도,
pin 된 dim 은 top-N 컷을 면제하고 항상 대표에 포함한다(품질 가드 min_coverage/min_support 는 유지) →
고객뷰 '👍이런 점이 좋아요' 헤드라인이 카테고리 무관하게 채워진다. `--pin-dims ""` 로 끄거나 콤마로 여러 prefix 지정.

**소수 카테고리 가드**(리뷰 반영): 번들 단위 coverage는 카테고리 번들이 1~2개면 전부 100%로 붕괴해 변별력이 없다.
`--min-support`(대표 dim 최소 절대 번들수, 기본 2)·`--min-bundles`(기본 3) 미만이면 `low_confidence=true`로 표시(가드는 1로 완화). 단일 카탈로그(nike) 같은 degenerate 케이스를 '근거 있는 랭킹'으로 오인하지 않게.

**셀러뷰 / 고객뷰** `db/representative_view.py` — 같은 `representative` 데이터를 청중별로 투영(추출/랭킹 불변, 표현만):
- `seller_view(rep)` : dim·coverage·lift·rank·언급수까지 — 셀러 포지셔닝/보완점 분석 화면.
- `customer_view(rep)`: 고객 친화 섹션(👍좋아요/😋맛·식감/📋특징/📦용량·보관/🎯활용/💡참고)으로 재구성, 내부 수치 숨김,
  근거수→'실제 후기 N건' 소셜프루프 환산, 약점 소프트 프레이밍, 셀러 전용(타사 비교) 비노출, 문장 dedup.
- `--html` 로 좌(셀러)·우(고객) 비교 화면 생성(앱/API는 두 함수만 import).

### 6-8. 데모/스테이징 (Oracle·24k 미적재 상태에서 검증)
- 모든 적재/랭킹/뷰 스크립트는 `INSIGHTS_DB` 환경변수로 DB를 분리할 수 있다(기본 `insights`, 운영 보호).
- `db/demo_load_trees.py` : `trees_food.jsonl`(실제 식품 taxonomy 2.7k 번들)을 키워드 규칙 데모 카테고리와 함께
  `INSIGHTS_DB=insights_demo` 로 적재 → 여러 카테고리에서 랭킹/대표/뷰를 실코드로 검증(운영은 Oracle 카테고리가 대체).

### 6-9. 식품 가격(offers) · 가격추이(price_history) — product-identity-graph 식품판
신발(nike)의 SKU 크로스몰 가격사다리를 식품 카탈로그(ctlg_no)에도. **`db/food_price_backfill.py`**:
- 네이버 쇼핑 API(`search_shop`)로 카탈로그 풀네임(disp) 검색 → 가격비교 대표행 제외한 실제 몰 리스팅만.
- `offers` 컬렉션(`product_uid=str(ctlg_no)`: mall·platform·price·중고/새·url) + 패키지 `catalogs[].price_summary`
  (min·max·median·n_malls·low_mall·spread_pct) 저장. 재개 안전(가격 있으면 skip, `--refresh` 로 갱신).
- **가격추이**: 매 실행 시 `price_history`(`_id=ctlg_no@YYYY-MM-DD`, 일자별 1행) upsert. 네이버는 과거가를
  안 주므로 **매일 cron(`run_food_price_backfill.sh`, --refresh)** 이 그날 현재가를 1점씩 쌓아 시계열을 만든다.
- 결합 화면 `db/bundle_view.py`: 좌(고객 대표 인사이트) + 우(하위 카탈로그 ctlg_no/풀네임 + 가격사다리 +
  📈 추이 스파크라인/변동%). nike 는 가격모드(변형 SKU+offers), 식품은 카탈로그모드(catalogs+price_history).
- **이상치 필터**(`clean_offers`): 식품은 단품·다른 팩사이즈 오매칭·네이버 productType 오라벨로 min~max 가
  심하게 왜곡된다(예: 130g 단품 ₩810 vs 36개 ₩43,350). 기본 = **중고제외(productType=2)+중앙값 비율 필터
  (`--ratio` 4.0, [median/4, median×4] 밖 제거)**, 어떤 단계도 결과를 비우지 않음(전부 중고면 폴백).
  옵션: `--keep-used`(몰 손실 우려 시), `--iqr-k`, `--pct-trim`. 실측: 전체 카탈로그 spread 중앙값이 큰 폭으로
  하락(예: 쿡시 미역국 681%→9%). **`--reclean`**: API 재호출 없이 기존 offers를 필터로 재정제(가격/추이 재계산).
  ※ 트레이드오프 — 중고제외는 네이버 오라벨 listing 도 같이 떨궈 n_malls 가 줄 수 있음(`--keep-used` 로 보존).
- `db/seed_price_history_demo.py` : **데모 전용** — 스냅샷 1점뿐일 때 추이 UI를 보여주려 현재가 기준 N일치 합성
  이력 생성(오늘만 실제, `source=synthetic_demo`). 운영에선 불필요(매일 cron 이 진짜를 쌓음).

신규 컬렉션: `offers`(식품·신발 공용 판매처), `price_history`(일자별 가격 스냅샷=추이).
```
