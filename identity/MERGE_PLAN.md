# 병합 설계: business-model × product-identity-graph

> 작성 2026-06-29. **현재 두 파이프라인 모두 실행 중일 수 있음 → 이 문서는 설계만 기록하며 파일 이동/삭제는 포함하지 않는다.**
> 실제 병합 실행은 양쪽 배치가 멈춘 것을 확인한 뒤 별도로 진행한다.

## 0. 한 줄 요약

두 프로젝트는 같은 대상("상품")을 다른 축에서 본다. **`product-identity-graph` = 상품의 정체(정형 팩트) + 매칭 엔진**, **`business-model` = 그 상품에 대한 여론/수요(비정형 인사이트)**. 병합은 둘을 하나의 **canonical product 노드** 중심으로 합치는 것이고, 합류점은 이미 존재하는 `db/` 스키마다.

| | **business-model** (`~/Work/business-model`) | **product-identity-graph** (`~/Laboratory/product-identity-graph`) |
|---|---|---|
| 데이터 성격 | **비정형** — 블로그 후기·유튜브 댓글·다나와 후기 | **정형** — 공식몰 상품 팩트 |
| 뽑는 것 | 강점/약점·FAQ·사용맥락 인사이트 (+근거검증) | 스타일코드·이름·컬러·가격·사이즈·고시(소재/제조국/제조년월) |
| 핵심 엔진 | `naver_review_geo.py` + `run_batch.py` (LLM 추출, 재개 가능 배치) | `extract_*.py` 30개 브랜드 어댑터 + `pig/` (엔티티 레졸루션) |
| 산출물 | `insights_1002.jsonl` (상품당 taxonomy 1줄) | `outputs/all_brands.csv` + 매칭된 canonical 노드 |
| 부가 레이어 | `db/` (PG/Mongo/Oracle 적재 설계), `site/`·`data/` 리포트 | `pig/` 매칭(블로킹→유사도→LLM판정→union-find) |

## 1. 각 프로젝트의 정체

### product-identity-graph = "상품의 정체(identity)"
- 흩어진 리스팅(11번가/쿠팡/네이버/아마존)을 **진짜 상품 하나(canonical 노드)**로 묶는다 → `pig/`.
  - 파이프라인: 블로킹(후보 생성) → 필드 유사도+속성 충돌 가드 → 라우팅(자동병합 / 경계밴드 LLM판정 / 자동기각) → union-find 클러스터.
- 30개 스포츠/아웃도어 브랜드 공식몰에서 정형 스펙을 채운다 → `extract_<brand>.py` + `official_extract.py`.
  - 봇차단 3계층: ① 서버측 HTTP+JSON-LD(cafe24/Shopify/Demandware) ② 서버측 HTTP+DOM/내부JSON(자체몰) ③ 봇차단 우회(나이키 거주IP, 아디다스 언블로커/patchright).
  - 고시(소재/제조국/제조년월): 텍스트몰은 즉시, 이미지박힘몰(미즈노·몽벨 등)은 `ocr_openai.py` 비전 OCR(gpt-4o-mini).
- 산출물: `outputs/all_brands.csv` + 브랜드별 CSV + 대시보드.

### business-model = "상품에 대한 여론/수요(voice & demand)"
- 상품 키워드(`work_units.jsonl`, ~24k) → 흩어진 후기/댓글 수집 → 광고/협찬 필터 → LLM 구조화 인사이트.
  - `run_batch.py`: 재개 가능 배치(처리한 uid 건너뜀). 패키지 풀스테어 = 1차(base) → 2차(용량 델타) → 3차(개수 델타).
  - `naver_review_geo.py`: 수집 + `extract_sourced_insights` + 근거검증(verified/partial/hallucination drop).
- 산출물: `insights_1002.jsonl` — 상품당 1줄, `taxonomy`(context/verdict/aspect/faqs) + `verification`.
- `db/`: 인사이트를 **PostgreSQL 16 + pgvector**(기본)·Mongo·Oracle에 적재하는 검증된 스키마.
- `site/`·`data/`: consumer_guide / exec_report / package_explorer / seller_dashboard HTML 리포트.

## 2. 현재 구조의 중복 (병합으로 자연 해소)

`business-model/identity_graph/` 안의 파일들은 **`product-identity-graph`의 2026-06-22~24 스냅샷(옛 복사본)**이다:

```
identity_graph/{pig/, naver_demand.py, naver_dossier*.py, buzz.py, danawa_demand.py,
                nike_crossmarket.py, holdout_eval.py, mcp_server.py, llm_split.py,
                product_mockup.py, review_velocity.py, run.py, seller_view.py, demo_brand.py}
```

Laboratory 쪽은 이후 30개 브랜드 추출기·`outputs/`·`EXTRACT_README.md`까지 진화했다.
→ **병합 시 이 복사본은 버리고 `product-identity-graph`를 정본(source of truth)으로 삼는다.**

## 3. 합류점: canonical product node

`db/README.md`의 데이터 모델이 이미 다리를 놓고 있다. `product` 테이블 중심:

```
                    ┌─────────────────────────────┐
                    │   canonical product node    │   ← pig/ 가 리스팅들을 여기로 해소
                    │   (uid, brand, style_code)  │
                    └──────────────┬──────────────┘
         ┌─────────────────────────┼─────────────────────────┐
   [정형 spine]                                          [비정형 voice]
   identity-graph                                        business-model
   · style_code/color/price/size                         · point (taxonomy 셀, dim_path)
   · 고시(소재/제조국/제조년월)                            · evidence (출처 인용 → source 조인)
   · offer (몰별 가격·재고)                                · faq, rag_chunk → chunk_embedding
```

- identity-graph가 **상품의 뼈대(누구인가)**를 만들고,
- business-model이 그 뼈대에 **여론 살(어떻게 평가받는가)**을 붙이고,
- `db/`가 둘을 같은 `product.uid`로 한 스키마(PG+pgvector)에 적재해 서빙/RAG.

> 참고: `db/` 데이터 모델의 핵심 결정 — 변형 = `parent_uid` 자식 product, taxonomy는 `point.dim_path` 문자열(셀별 컬럼 ✗, 카테고리 달라도 스키마 불변), sparse(빈 셀 행 없음), 하이브리드 보관(정규화 테이블 + `raw_block` JSONB). PG/Oracle/Mongo 3종 실측 검증 완료(동일 카운트).

## 4. 제안 병합 구조 (목표 레이아웃)

한 리포지토리 안에서 **"수집 → 정체해소 → 인사이트 → 적재 → 서빙"** 으로 정렬:

```
product-graph/                      ← 통합 루트 (이름 택일)
├── collect/                        공통 수집 (네이버/다나와/유튜브/공식몰)
│   ├── naver_review_geo.py           (← business-model 메인)
│   ├── extract_<brand>.py …          (← identity-graph 30개 어댑터)
│   ├── official_extract.py · ocr_openai.py · unblocker.py
│   └── naver_demand.py · danawa_demand.py · buzz.py
├── identity/                       정체 해소 엔진
│   └── pig/  (blocking · normalize · similarity · adjudicate · resolve)
├── insight/                        비정형 → LLM 인사이트
│   └── run_batch.py · trees_food.jsonl · work_units.jsonl
├── db/                             적재·스키마 (PG/Mongo/Oracle) — 둘의 합류점
├── serve/                          리포트·대시보드·사이트 (site/, data/*.html)
├── data/                           입력/산출 (listings, insights_*.jsonl, all_brands.csv)
└── archive/                        실험 잔재
```

## 5. 병합 실행 체크리스트 (배치 정지 후 진행)

1. **선행 확인**: 양쪽 배치 정지 확인 — `run_batch.py`(business-model), `extract_all.py`/`harvest_all.py`(identity-graph) 프로세스 없음. 출력 파일(`insights_1002.jsonl`, `outputs/`) 쓰기 멈춤 확인.
2. **백업/커밋**: 두 디렉토리 각각 현재 상태를 git 커밋(또는 스냅샷). `site/`만 git repo이므로 나머지는 init 필요.
3. **중복 제거**: `business-model/identity_graph/` 폐기, `product-identity-graph`를 정본으로 흡수.
4. **수집기 통합**: 양쪽이 공유하는 네이버/다나와 호출을 `collect/`로 단일화 (현재 코드 분기됨).
5. **db/ 스키마 점검**: 정형 spine(style_code/offer/고시)을 현 스키마가 수용하는지 정밀 점검 — 병합 실제 난이도는 여기서 갈림. `product`에 정형 컬럼/offer 테이블 추가 여부 결정.
6. **키/환경 통합**: `run.sh`의 export(NAVER/OPENAI/YOUTUBE/UNBLOCKER 키)를 공통 진입점으로. (identity-graph는 이미 `~/Work/business-model/run.sh`에서 키를 읽음.)
7. **import 경로 수정** → 통합 후 스모크 테스트(각 파이프라인 1건씩 end-to-end).

## 6. 미해결 결정사항

- [ ] 통합 루트 이름 (`product-graph` / 기타) 및 위치(`~/Work` vs `~/Laboratory`).
- [ ] `db/`가 정형 spine을 수용하도록 스키마 확장할지, 별도 정형 테이블로 둘지.
- [ ] business-model의 식품 카탈로그(1002)와 identity-graph의 스포츠/아웃도어 브랜드 — 카테고리 도메인이 다름. 같은 `product` 스키마로 묶을지, 카테고리 네임스페이스로 분리할지.
