<!-- /autoplan restore point: /Users/a1101417/.gstack/projects/business-model/main-autoplan-restore-20260630-105415.md -->
# Canonical Product 노드 합류 계획 (v2 — autoplan 리뷰 반영)

> 목표: `uid` 기준으로 **insight 택소노미 ⊕ identity 정형 팩트**가 한 노드에 합류하고,
> 신규 상품이 들어오면 자동으로 합류되는 지속 파이프라인.

## 확정 설계 원칙 (사용자)

1. **insight가 상품 우주를 정의한다** — 지속 수집, 각 상품에 `uid`+`keyword` 부여.
2. **identity는 insight의 상품 목록을 입력 씨앗으로 받아 실행한다** — synthetic `listings.json` /
   독립 브랜드 카탈로그가 아니라. (씨앗 전달 방식은 CSV 핸드오프 — 아래 결정 D2 참조.)
3. **identity 산출물에 insight `uid`를 찍어 조인 키로 쓴다.**
4. **활성 저장소 = Mongo(`insights_demo`)의 `product` 모델.** 정형 스핀을 얹는다. (Postgres offer 스키마는 참고용.)
5. **지속 수집이므로 n8n step으로 신규 insight 상품에 identity를 증분 실행한다.**

## 카테고리 불가지 설계 (사용자 확정 — premise 게이트 + 설계 변경)

특정 카테고리만이 아니라 **모든 카테고리를 수용**한다 — insight·identity 둘 다.
→ insight 우주는 카테고리 불문(category_l1 문자열, taxonomy=dim_path 문자열로 이미 불가지).
→ identity는 카테고리별 정형 스핀을 산출(의류=style_code/소재·제조국, 식품=원재료명/유통기한,
   가전=모델/소비전력 …). 산출 컬럼 집합을 **하드코딩하지 않는다.**
→ 합류 계층(T1/T2)은 정형 컬럼을 **passthrough**로 수용 → 카테고리 분기 없음.
→ 아직 identity가 추출 못하는 카테고리는 `status:empty`(과도기)로 안전 마킹, 추출기 확장 시 done.
→ plan A(씨앗 주입 + uid 스탬프 + 증분 step) 타당. CEO 도메인 반박 해소됨.

## 현재 상태 (실측)

### insight (활성)
- Mongo `insights_demo.products`. `_id = uid`: package `P{bndl_grp}`, variant `P{bndl_grp}::{value}`, single `S{ctlg_no}`.
- `keyword` = 외부 수집기 조인 키. `catalogs[]` 임베드(실 SKU: `ctlg_no, disp, size, count, has_insight, insight, price_summary`).
- 이미 합류 중(전부 resumable backfill, reload 보존): 가격 `food_price_backfill.py`(→`catalogs[].price_summary`),
  youtube `youtube_backfill.py`(→`products.youtube` + `status` enum), buzz, per-catalog insight.
- 지속 루프: `run_structured_loop.sh`(Oracle→trees→Mongo, **background loop**), `run_unstructured_loop.sh`.
- n8n 트리거 `pipeline_trigger.py`(:8766), `STAGES`→`step_*.sh` 동기 배치(STEP_TIMEOUT≈900s)→`pipeline_progress.py`.

### identity (정형 엔진)
- 추출기 `extract_*.py`(30 브랜드) + `extract_all.py` → `outputs/all_brands.csv`
  (14컬럼: source/brand/**style_code**/name/**color**/**price**/currency/category/gender/sizes/**origin**/**material**/**mfg_date**/url).
- 추출기는 브랜드몰 크롤러: run.sh 키 자동로드, `style_code` dedup, CSV 출력. **uid·씨앗 개념 없음.**

## 아키텍처 (Eng 리뷰 반영)

### 데이터 흐름 (CSV 핸드오프 — 인프로세스 재배관 회피)
```
insight products (uid, keyword, category_l1, catalogs[])
   │  ① export_identity_seed.py  (identity.status 부재인 상품만)
   ▼
seed.csv  {insight_uid, keyword, category_l1, ctlg_no[], style_code?, barcode?}
   │  ② identity 실행 (카테고리별 추출기 — 산출 컬럼은 카테고리마다 다름)
   │     · 의류/신발 → extract_* 브랜드몰 크롤(현재 활성)
   │     · 식품/가전/기타 → 해당 카테고리 추출기(확장 로드맵) — 없으면 status:empty
   ▼
outputs/*.csv  (insight_uid + 카테고리별 정형 컬럼 — 컬럼 집합 고정 안 함)
   │  ③ identity_backfill.py  (food_price_backfill 패턴 복제, resumable, column-agnostic)
   ▼
Mongo insights_demo.products
   · per-SKU 정형 → catalogs[].identity = {<카테고리 컬럼 passthrough>, gosi{...}, source, fetched_at}
   · 상품 레벨     → products.identity = {brand, status, n_facts, fetched_at}   # status: pending|done|empty|error
```

### 합류 모양 (Eng #1, #2 + category-agnostic)
- **per-SKU 정형은 `catalogs[].identity`** — `price_summary`와 동일 위치/패턴. ctlg_no 매치.
  package/variant는 catalogs 여럿 → 상품 레벨 단일 서브독으론 손실.
- **정형 컬럼은 카테고리 불문 passthrough** — `META_COLS`(insight_uid/ctlg_no/brand) 제외 모든 컬럼 수용.
  `GOSI_HINT`에 든 법정 고시 컬럼만 `gosi{}`로 묶고 나머지는 top-level. 카테고리 분기/하드코딩 없음.
- **상품 레벨(brand) + 진행상태는 `products.identity = {brand, status, n_facts, fetched_at}`.**
- single(`S{ctlg_no}`)은 1:1이라 둘 다 가능하나 일관성 위해 같은 규칙 적용.
- 구현: `identity_backfill.build_identity_update` (순수 함수, 단위 테스트 7/7) + `_preserve_async_fields`(T1).

### reload 보존 (Eng #2 — 정정: 활성 경로는 이미 안전)
- **실측 정정:** 활성 `insights_demo`의 `demo_load_trees.py`는 ReplaceOne이 아니라 **증분 insert + 기존
  bndl_grp skip**(136행). `food_price_backfill`은 `update_one($set:{catalogs})`(505행). → 기존 상품은
  reload가 안 건드림. catalogs[].identity 보존은 backfill의 `$set`만으로 자동(별도 로더 수정 불필요).
- 잔여 위험은 legacy `load_mongo.py` ReplaceOne 경로뿐 → **완료(T1):** keep-projection에 `identity:1` 추가 +
  순수 함수 `_preserve_async_fields`로 youtube/representative/identity 보존(단위 테스트 9/9).
- 파괴 경로는 `demo_load_trees --reset`(명시적 전체삭제)뿐 — 설계상 의도된 동작.

### 증분/idempotency (Eng #3)
- 씨앗 export 쿼리 = `products.identity.status` 부재(또는 `pending`).
- backfill miss(식품·산출 없음) → `products.identity.status = "empty"` 스탬프(빈 서브독 ✗) → 재씨앗 안 됨.
- 성공 → `status="done"`. 재실행 시 done/empty skip = no-op.

### n8n 계약 (Eng #5)
- identity는 **background loop**(`run_identity_loop.sh`, structured 패턴)로 크롤 수행.
- `step_identity.sh` + `STAGES['identity']`: 동기 `/step`은 **작은 backfill 배치만**(크롤 ✗, 타임아웃 회피).
- `pipeline_progress.py` identity: **모든 카테고리가 적격** — `total` = 전체 product,
  `done` = `identity.status ∈ {done,empty}`. 추출기 없는 카테고리는 `empty`로 마킹돼 remaining이 정상 드레인
  (영구잔류 없음 — 카테고리 부분집합 스코핑 불필요. category-agnostic 설계로 단순화).

## 구현 단계
1. ✅ **(T1 완료)** product 스키마 계약: `catalogs[].identity`(per-SKU) + `products.identity{brand,status,n_facts}`. 인덱스 `identity.status`.
2. ✅ **(T1 완료)** reload 보존: `load_mongo.py` projection+`_preserve_async_fields`. (활성 demo 경로는 증분 skip+$set로 이미 안전 — 별도 로더 수정 불필요.)
3. ✅ **(T2 완료)** `identity_backfill.py`: CSV→uid 매치, **column-agnostic** per-SKU/상품 합류, status enum, resumable. 순수함수 단위테스트 7/7.
4. ✅ **(T3 완료)** `export_identity_seed.py` (insight→seed.csv, `identity.status` 부재 필터, category_l1 passthrough). 단위 6/6.
5. ✅ **(T4 완료)** `identity_seed_match.py`: 강키 우선+이름 폴백 매칭 + `insight_uid` 스탬프(추출기 본체 불변). 단위 7/7.
6. ✅ **(T5 완료)** `run_identity_loop.sh` + `step_identity.sh` + `STAGES['identity']` + `pipeline_progress`(total=전체). 조인만(크롤 별도).
7. ✅ **(T6 완료)** 라이브 통합 테스트(격리 DB): 합류·공존보존·empty·progress·reload 보존 end-to-end PASS.

**상태: T1~T6 구현 완료.** 5개 테스트 스위트 36개 통과(라이브 통합 포함). 미커밋 연계: `pipeline_progress.py`/`pipeline_trigger.py`(선행 untracked + identity 추가분).

## Error & Rescue Registry
```
 METHOD/CODEPATH            | WHAT CAN GO WRONG              | EXCEPTION       | RESCUED? | USER/SYSTEM SEES
 ---------------------------|--------------------------------|-----------------|----------|------------------------
 export_identity_seed       | products 쿼리 빈 결과          | (정상)          | Y        | seed.csv 0행 → 단계 skip
 identity 크롤(의류)        | 브랜드몰 4xx/5xx/봇차단        | HTTPError       | Y        | 해당 row drop+log, status 미스탬프
 identity 도메인 라우팅     | 식품인데 추출기 없음           | (분기)          | Y        | status:empty, no crash
 identity_backfill CSV 읽기 | outputs CSV 누락/깨짐          | FileNotFound/csv| Y(GAP→) | 단계 busy/err, 다음 배치 재시도
 identity_backfill uid 매치 | style_code 충돌/`::`구분자     | KeyError        | Y        | 미매치 row log, skip
 reload 동시 실행           | backfill 중 demo_load reload   | race            | Y        | status 보존 → 손실 없음(테스트로 보증)
 step_identity              | 락 보유 중                     | (lock)          | Y        | 'busy' 즉시 반환
```

## Failure Modes Registry
```
 CODEPATH               | FAILURE MODE         | RESCUED? | TEST? | SYSTEM SEES        | LOGGED?
 -----------------------|----------------------|----------|-------|--------------------|--------
 reload 보존            | identity 소멸        | Y(설계)  | Y(필수)| 손실 없음          | Y
 빈 결과 재씨앗         | 식품 영구 재실행     | Y(status)| Y     | empty skip         | Y
 동기 step 크롤         | n8n 타임아웃         | Y(loop)  | Y     | 배치만 동기        | Y
 total 미스코프         | remaining 영구잔류   | Y(부분집합)| Y    | 적격분만 카운트     | Y
```
모든 행 RESCUED=Y, 설계 단계 CRITICAL GAP 없음(테스트로 보증 전제).

## 범위 밖 (NOT in scope, 의도적 제외)
- **카테고리별 신규 추출기 개발**(식품/가전/화장품 고시 등) — identity 추출 *능력*의 확장 로드맵.
  합류/저장 계층(T1/T2)은 이미 category-agnostic이라 추출기만 추가하면 자동 수용. 추출기 없는 카테고리는
  `status:empty`로 안전. (이 분리가 "특정 카테고리만"이 아니라 "모든 카테고리 수용"을 가능케 함.)
- Postgres/pgvector 정형 적재 (참고용 유지, 활성 아님).
- identity `pig/` 엔티티 레졸루션 재설계 — 씨앗이 uid를 들고 오므로 cross-mall 병합은 별도.
- 30개 추출기 본체 재작성 — 씨앗/매칭은 래퍼로, 추출기는 불변.

## What already exists (재사용)
- `food_price_backfill.py` — identity_backfill의 복제 원형(resumable, ctlg_no/keyword 매치, catalogs 서브독 쓰기).
- `youtube_backfill.py` + `load_mongo.py:140-151` — status enum + reload 보존 패턴.
- `run_structured_loop.sh` — background loop + 락 패턴(run_identity_loop 원형).
- `pipeline_trigger.py STAGES` + `step_*.sh` + `pipeline_progress.py` — n8n 단계 등록 계약.

## 결정 확정 (게이트 통과)
- **D1 (premise/user challenge):** 조인 토대 = plan A. 사용자 "둘 다 뽑는" 로드맵으로 확정.
- **D2 (taste):** identity 씨앗 전달 = **CSV 핸드오프** 확정(in-process 씨앗 모드 기각).
- 상태: **APPROVED** (2026-06-30 autoplan). 미해결 결정 없음. 다음: 구현(T1~T6) 또는 /ship.

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|----------------|-----------|-----------|----------|
| 1 | CEO | 조인 토대 = plan A(씨앗 주입+uid 스탬프+증분 step) | User Challenge | 사용자 컨텍스트 우선 | 게이트에서 사용자가 A 선택, "둘 다 뽑는" 로드맵으로 도메인 반박 해소 | B 디커플, C 하이브리드 |
| 2 | Eng | per-SKU 정형 → `catalogs[].identity`, 상품 레벨 → `products.identity` | Mechanical | P4 DRY | `price_summary` 전례 그대로; package/variant 다중 catalogs 손실 방지 | 단일 products.identity |
| 3 | Eng | reload 보존: load_mongo projection + catalogs 로더 양쪽 | Mechanical | P1 완전성 | ReplaceOne이 미보존 서브독 파괴(youtube 전례) — correctness 버그 | 미보존(소멸) |
| 4 | Eng | `identity.status` enum(pending/done/empty/error) | Mechanical | P4 DRY | youtube status 전례; 식품 빈 결과 영구 재씨앗 방지 | 서브독 부재로 skip |
| 5 | Eng | 씨앗 전달 = CSV 핸드오프 | Taste | P5 명시>영리, P3 실용 | 30개 추출기에 uid 안 들림, 디커플, 타임아웃 회피 / 원칙 #2(insight 씨앗) 유지 | in-process 씨앗 모드 |
| 6 | Eng | identity 크롤 = background loop, /step은 backfill 배치만 | Mechanical | P3 실용 | 동기 /step(900s)에 5000행 크롤 → 타임아웃; structured 전례 | 동기 /step 크롤 |
| 7 | Eng | progress total = identity-적격 부분집합 | Mechanical | P5 명시 | 식품 포함 시 remaining 영구잔류 또는 즉시 no-op | 전체 우주 total |
| 8 | CEO | 카테고리별 신규 추출기 = NOT in scope(분리) | Mechanical | P2 blast radius 밖 | 별도 추출 엔진 = 1일+ 신규 인프라; status:empty로 안전 처리 | 지금 빌드 |
| 9 | User | **category-agnostic 설계** — 특정 카테고리 분기 ✗, 모든 카테고리 수용(insight·identity) | User Directive | 사용자 지시 | 정형 컬럼 passthrough(META 제외) + GOSI_HINT 묶음; progress total=전체(부분집합 ✗) | 의류/식품 도메인 분기 |
