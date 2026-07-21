# 비정형 인사이트 추출 엔진 제품화 — 설계 (Design Spec)

- 날짜: 2026-07-21
- 범위: 비정형(unstructured) 인사이트 추출 로직의 **엔진 API/라이브러리화(#3)** + **안정 운영 서비스화(#2)**
- 상태: 승인됨 (브레인스토밍 완료, 구현 계획 대기)

## 배경 / 문제

현재 비정형 추출은 `naver_review_geo.py`(3,489줄) 모놀리스에 **수집·정형(identity)·비정형 추출**이 한 파일에 섞여 있고, 그 위에 두 종류의 호출자가 있다:

- 파일 배치: `run_batch.py` (work_units.jsonl → insights_1002.jsonl, 재개·멱등)
- Mongo 운영 루프: `db/catalog_insight_backfill.py` (적재된 SKU → 인사이트 backfill, 재개·멱등)
- HTTP 트리거: `db/pipeline_trigger.py`(:8766, `/step/<stage>`·`/progress`·`/status`)

문제: 추출 코어가 재사용 가능한 인터페이스로 노출돼 있지 않고(다른 시스템이 호출하려면 모놀리스에 결합), 운영 관측·재현이 로그 grep + HTML 대시보드 수준에 머문다.

## 목표

1. 비정형 추출 코어를 **호출 가능한 패키지**로 뽑아낸다 (내부는 잡 큐, 얇은 동기 래퍼 제공).
2. 노출 표면: **코어=Python 패키지, HTTP=얇은 어댑터**.
3. 운영 안정성: **①모니터링·알림 + ②재현성·버전 고정** 우선. (자동복구·품질게이트는 다음 마일스톤)

명시적 비목표(이번 범위 제외): 자동 부분실패 복구/오염 정정(#3-ops), 자동 품질 게이트(#4). run_meta가 깔리면 후속에서 그 위에 얹는다.

## 아키텍처

```
insight_engine/                     # 새 패키지 (코어=순수: Mongo·파일·HTTP 의존 없음)
├── types.py       ExtractTarget(keyword/name/sku/brand) · EngineConfig · InsightResult
├── engine.py      extract_insight(target, cfg) -> InsightResult
│                   = 수집(blog[+danawa]) -> sourced 추출 -> 인용검증/재접지
│                     (기존 naver_review_geo 함수들을 호출로 재사용, 내부는 유지)
├── jobs.py        submit(targets, cfg) -> job_id · get(job_id) -> JobStatus
│                   워커가 쿼터 존중하며 드레인, 재개·멱등(기존 skip 로직 승계)
├── sync.py        extract_one(target, cfg) = submit+wait (단건 저빈도용 얇은 래퍼)
└── versioning.py  run_meta 생성/고정
http_adapter        pipeline_trigger 패턴 확장 — /extract · /jobs · /jobs/{id} · /metrics
```

**경계 원칙**
- 코어(`engine.py`)는 I/O·전역 상태 없음 → 입력(target, cfg) → 출력(InsightResult)의 순수 함수. 단독 테스트 가능.
- Mongo·파일·쿼터 상태·재개 저널은 `jobs.py` 층에만 존재.
- HTTP 어댑터는 `jobs`/`sync`를 감싸는 얇은 층. 비즈니스 로직 없음.

**점진 이관 원칙 (리스크 최소화)**
- `naver_review_geo.py` 모놀리스를 통째로 재작성하지 않는다.
- `insight_engine`은 초기엔 기존 함수(`search_blog`, `collect_danawa`, sourced 추출/`_reground_quote` 등)를 **호출로 재사용하는 파사드**.
- `run_batch.py`·`catalog_insight_backfill.py`는 `naver_review_geo` 직접 import → `insight_engine` 클라이언트로 점진 전환. 전환 중에도 둘 다 계속 동작.

## 데이터 계약

### ExtractTarget
추출 단위. `keyword`(필수) + 선택 `name`/`sku`/`brand`/`context`.

### EngineConfig
`model`, `prompt_version`, `lexicon_version`, `source_config`(blog/danawa/youtube on-off), 동시성·재시도 파라미터.

### InsightResult
`insights`(sourced: dim별 best point + faq), `evidence`(출처+인용), 그리고 **`run_meta`**(재현성 핵심):

```
run_meta = {
  engine_version,     # insight_engine 시맨틱 버전
  prompt_version,     # sourced 추출 프롬프트 해시/버전 (현재 코드 산재 → 상수화)
  model,              # gpt-4o-mini 등
  lexicon_version,    # 도메인 사전 버전
  source_config,      # 사용한 소스 on-off
  extracted_at
}
```

## 운영 층

### ② 재현성·버전 고정 (versioning.py) — 선행
- 모든 `InsightResult`에 `run_meta`를 박고, `jobs` 층이 인사이트 저장 시 함께 기록.
- 흩어진 프롬프트를 상수/해시로 승격해 `prompt_version` 부여.
- 효과: "이 인사이트가 어떤 설정으로 나왔나" 추적, 프롬프트/모델 변경이 데이터에 자동 반영, 동일 버전 재실행 = 재현. mini 환각/출처 오기재 이슈를 버전으로 원인 격리.

### ① 모니터링·알림
- `/metrics` 엔드포인트: `{ 진행률, 처리율, 실패율, 누적비용($), 쿼터상태, 버전분포 }`.
- 기존 `db/pipeline_progress.py`(Mongo 집계)를 `/metrics`로 승격 + `run_meta` 버전분포 추가.
- 알림 훅: 임계치(실패율↑ / 쿼터소진 / 루프정지) → 알림. 초기엔 로그 + 간단 채널, 확장 여지 확보.

## 테스트 전략
- `engine.py`: 순수 함수 → 고정 입력(mock 수집 결과)으로 sourced 추출·인용 재접지 단위 테스트.
- `jobs.py`: 재개·멱등(중복 uid skip), 쿼터 중단/재개 시나리오.
- `versioning.py`: 동일 cfg → 동일 run_meta, cfg 변경 → 버전 변화 검증.
- 회귀: 기존 `validate_insights.py`·`test_validate_insights.py` 승계.

## 열린 항목 (구현 계획에서 확정)
- 잡 저장소: Mongo `jobs` 컬렉션 vs 기존 jsonl 저널 — 실제 운영 부하 보고 결정.
- HTTP: `pipeline_trigger.py` 확장 vs 별도 얇은 FastAPI.
- 알림 채널 구체 대상.
