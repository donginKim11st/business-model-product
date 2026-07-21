# 하이브리드 실행 라우팅 (sync ↔ batch 혼용) — 설계 + 태스크

- 날짜: 2026-07-21
- 범위: 실시간(동기)과 OpenAI Batch(비동기)를 **하나의 인터페이스에서 모드로 선택**해 혼용
- 상태: 승인됨 — 인라인 TDD 구현

## 문제
두 백엔드가 다 있으나 따로 논다: 동기는 `http_adapter` REST(/extract·/jobs), 배치는 CLI 오케스트레이터. 호출자가 한 인터페이스에서 모드를 고를 수 없다.

## 설계
**실행 모드를 설정 한 줄로 고른다.** 라우터가 백엔드로 분기한다.

- `EngineConfig.execution: "sync" | "batch"` (기본 `"sync"`).
- `insight_engine/router.py` — `submit(targets, cfg, *, sync_store=None, client=None, creds=None, extract=None, max_per_batch=40000) -> dict`
  - `execution="sync"` → `jobs.submit(...)` 위임 → `{"mode":"sync","job_id":..,"state":..}`
  - `execution="batch"` → 타깃별 크롤 → `batch_openai.build_request_lines` → 청킹 → OpenAI 파일 업로드(메모리 BytesIO, 디스크 없음)·배치 생성 → `{"mode":"batch","batch_ids":[..],"staging":[recs],"request_count":N}`
  - 배치 staging(후처리용 items)은 **반환값으로 돌려준다** — 파일·Mongo 영속화는 호출자(오케스트레이터) 몫(라우터는 디스크·Mongo 무의존).
- `http_adapter` — `POST /jobs` body의 `execution`으로 라우팅. sync는 job_id, batch는 batch_ids+staging 반환. `POST /batch/status` body `{batch_ids}` → 배치 상태 조회.
- `POST /extract`(실시간 단건)는 항상 sync 유지.

**혼용 정책**: 호출자가 건별 선택(급한 단건·소량 → sync, 대량·저비용 → batch). 자동 규칙은 비목표(후속).

## 태스크 (인라인 TDD)
- **T1** `types.EngineConfig.execution` 필드 + `router.submit` 라우팅. 테스트: sync→jobs 위임, batch→build+chunk+client.files/batches.create 호출(모킹), staging/ batch_ids 반환, QuotaStop/빈items skip.
- **T2** `http_adapter`: `/jobs` execution 라우팅 + `/batch/status`. 테스트: sync 경로 job_id, batch 경로 batch_ids, /batch/status 집계.
- **T3** 전체 스위트 통과 + 미러 + 문서 반영.

## 비목표
- 자동 모드 선택(임계 기반). 배치 REST 전체 lifecycle(staging→fetch)의 서버측 영속화 — 그건 오케스트레이터가 router를 써서 수행. run_meta.execution은 이미 배치 경로에 기록됨.

## 재사용/무수정
`naver_review_geo`·`run_batch`·`catalog_insight_backfill` 무수정. `batch_openai`·`jobs`·`engine` 재사용.
