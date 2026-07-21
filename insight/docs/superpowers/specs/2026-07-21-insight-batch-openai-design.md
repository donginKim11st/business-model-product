# 비정형 인사이트 OpenAI Batch API 백엔드 — 설계 (Design Spec)

- 날짜: 2026-07-21
- 범위: 비정형 인사이트 추출을 OpenAI **Batch API**(비동기·최대 24h·50% 할인)로 실행하는 신규 백엔드
- 상태: 승인됨(브레인스토밍 완료) — 구현 플랜 대기
- 선행: `insight_engine` 제품화 패키지(2026-07-21) 완료. 본 설계는 그 위에 batch 실행 경로를 추가.

## 배경 / 문제

현재 비정형 추출(`db/catalog_insight_backfill.py`)은 SKU마다 `naver_review_geo._parse_sourced`를 통해 `chat.completions.parse`를 **동기로 3콜**(SourcedInsights / SourcedContext / SourcedAspectVerdict) 부르고, 직후 인용검증·재접지(`build_sourced_block`)까지 이어서 한다. ThreadPoolExecutor 16워커 병렬. 정가 mini ≈$0.0038/건.

OpenAI Batch API는 요청을 `.jsonl`로 제출해 비동기(최대 24h)로 처리하고 **50% 할인**(mini ≈$0.0019/건)한다. 동기 스트리밍 루프와 구조가 달라, 수집·LLM·후처리를 분리해야 한다.

## 목표

1. 전체 미처리 큐를 OpenAI Batch API로 추출해 비용 절반.
2. 비동기(제출↔회수가 세션·재시작·최대 24h를 넘김) → **durable 상태**로 재개 가능.
3. 추출 품질(인용검증·재접지·run_meta)은 동기 경로와 **동일**. 기존 `naver_review_geo`/`run_batch` 무수정, 코드 재사용.

명시적 비목표: 동기 경로 대체(동기 backfill은 그대로 유지, batch는 별도 경로). 콜 통합(3→1)은 하지 않음(품질·중첩한계 회피 로직 유지, 비용은 토큰 기준이라 콜 수 무관).

## 아키텍처

```
insight_engine/batch_openai.py      # Batch API 백엔드 (순수 로직: 요청빌드·되매핑·스키마변환)
db/run_insight_batch_openai.py      # 오케스트레이터 CLI (--submit / --status / --fetch)
```

기존 동기 backfill(`catalog_insight_backfill.py`)은 손대지 않는다. Batch는 독립 3단계:

### ① SUBMIT (`--submit`)
1. 큐 구성 — 운영 `insights.products`의 미처리 SKU. 로직은 `catalog_insight_backfill`과 동일: `type=package`의 `catalogs[]` 중 `ctlg_no` 있고 `insight` 없는(또는 빈-재시도 대상) 것. 카탈로그 많은 패키지 우선.
2. 수집(크롤) — SKU별 네이버 블로그를 `run_batch.collect(kw, nid, nsec, ...)`로 모아 `items` 획득. 동기·스레드, 네이버 쿼터 그대로 존중(dongsuh 등 몰별 가드 무관 — 여긴 네이버). items 없으면 빈 insight로 바로 적재 대상(LLM 불필요).
3. staging 저장 — `items`를 **로컬 staging jsonl**(`insight_engine_batch/<batch_run_id>/staging.jsonl`)에 `{pkg_uid, ctlg_no, kw, items, prev_attempts}`로 append. 후처리(id_map 재구성·n_sources)에 필요.
4. 요청 `.jsonl` 빌드 — SKU × 3스키마 = **3N 요청**. 각 줄:
   - `custom_id = "{ctlg_no}|{schema_key}"` (schema_key ∈ sourced|context|aspect)
   - `method="POST"`, `url="/v1/chat/completions"`
   - `body = {model, temperature:0, messages:[{role:"user", content: PROMPT.format(keyword=kw, snippets=snippets)}], response_format: <pydantic→json_schema>}`
   - `snippets` = `naver_review_geo._build_sourced_snippets(items)[0]` (SUBMIT·FETCH가 같은 결정적 함수를 써 동일 id_map 보장)
   - `response_format` 변환은 SDK와 동일 결과를 쓴다: `openai.lib._parsing._completions.type_to_response_format_param(Model)` (동기 `.parse()`가 내부적으로 쓰는 변환 — 스키마 동일 보장). openai 버전 고정. (대안: `{"type":"json_schema","json_schema":{"name":..,"strict":True,"schema":Model.model_json_schema()}}` — strict 규칙 수동 보정 필요하므로 비권장.)
5. 청킹·제출 — 요청을 ≤`MAX_REQ_PER_BATCH`(기본 40,000, OpenAI 한도 50,000/200MB 이하 여유)로 분할, 각 청크를 `files.create(purpose="batch")` → `batches.create(completion_window="24h")`.
6. manifest 기록 — `insight_engine_batch/<batch_run_id>/manifest.json`: `{batch_run_id, created_at, model, batch_ids:[...], staging_path, request_count, status}`. (파일 단일출처. 운영 Mongo엔 결과만 씀 — manifest는 로컬.)

### ② STATUS (`--status [batch_run_id]`)
- manifest의 `batch_ids`로 `batches.retrieve(id)` → 각 배치 `status`(validating/in_progress/finalizing/completed/failed/expired)·완료 카운트 집계 출력. 무료. LLM·크롤 없음.

### ③ FETCH (`--fetch [batch_run_id]`)
1. manifest의 배치 중 `status=completed`인 것의 `output_file_id`를 `files.content`로 다운로드(.jsonl).
2. 줄마다 `custom_id`→(ctlg_no, schema_key), `response.body.choices[0].message.content`(JSON 문자열)를 `Model.model_validate_json`으로 파싱.
3. SKU(ctlg_no)별로 3결과(sourced/context/aspect) 재조립. 3개 다 모인 SKU만 처리(부분은 대기).
4. staging jsonl에서 그 SKU의 `items` 로드 → `_build_sourced_snippets(items)`로 `id_map` 재구성 → `build_sourced_block(si, ctx, av, id_map, items)` → block.
5. `catalog_insight_backfill.to_insight(block, len(items))`로 `ins` 생성 → `ins["run_meta"] = build_run_meta(EngineConfig(model))` + `run_meta["execution"]="openai_batch"` → `products.update_one({_id:pkg_uid}, {"$set":{"catalogs.$[c].insight": ins}}, array_filters=[{"c.ctlg_no":ctlg}])`.
6. 처리 완료 SKU를 manifest/로컬 진행파일에 표시(멱등: 이미 insight 있으면 skip). 완료 배치의 나머지·미완 배치는 다음 `--fetch`가 이어받음.

## 재개·멱등
- SUBMIT는 이미 insight 있는 SKU를 큐에서 제외(동기 backfill과 동일).
- FETCH는 SKU insight 존재 시 skip → 중복 적재 없음.
- manifest·staging이 로컬에 남아 세션/재시작을 넘어 STATUS·FETCH 재개 가능. `--fetch`를 배치 완료까지 반복 호출.
- 부분 완료 허용(완료 배치만 처리).

## 데이터 계약
- **custom_id**: `"{ctlg_no}|{schema_key}"`. ctlg_no에 `|` 미포함 가정(실측 확인 태스크 포함).
- **manifest.json**: `{batch_run_id, created_at(ISO), model, batch_ids:[str], staging_path, request_count, chunks:[{batch_id, file_id, n}], status}`.
- **staging.jsonl** 줄: `{pkg_uid, ctlg_no, kw, items:[...], prev_attempts:int}`.
- **run_meta**(FETCH 적재): 기존 필드 + `execution:"openai_batch"`.

## 테스트 전략
- `batch_openai.py` 순수 함수 단위테스트(Mongo·OpenAI 없이):
  - `build_request_line(sku_ctx, schema_key)` → custom_id·body·response_format 형태 검증(고정 items로).
  - `parse_output_line(line)` → (ctlg_no, schema_key, parsed_model) 되매핑, 잘못된 custom_id 처리.
  - `regroup_by_sku(parsed_lines)` → SKU별 3결과 묶기, 부분(2/3)은 제외.
  - `assemble_insight(sku_ctx, si, ctx, av)` → id_map 재구성→build_sourced_block→to_insight, run_meta.execution 포함(build_sourced_block/to_insight는 실제 함수 사용, items 고정).
- 청킹 로직: 요청 수 > MAX 시 다중 배치 분할 검증.
- custom_id 충돌: ctlg_no에 `|` 있으면 감지/이스케이프.
- OpenAI 클라이언트·Mongo는 모킹(단위테스트에서 실호출 없음).

## 열린 항목(플랜에서 확정)
- staging items 용량: SKU당 blog 50건 × N. 로컬 디스크 여유 확인. 필요 시 gzip.
- `type_to_response_format_param` 내부 API 안정성 — openai 버전 핀. import 실패 시 fallback 경로.
- 비용 사전보고: SUBMIT 전 N·3N·예상 토큰·예상 비용(원화) 산출·보고·승인(CLAUDE.md).
- 대상 DB=운영 `insights`(사용자 지정). DB 쓰기는 FETCH 단계 — 실행 전 재확인.
