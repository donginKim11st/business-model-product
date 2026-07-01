# 비정형 인사이트 검증·자동수정 파이프라인 (`validate_insights.py`)

- 날짜: 2026-07-01
- 상태: 설계 승인됨 (구현 대기)
- 대상 코드베이스: `insight/db/`

## 배경

비정형 추출(`catalog_insight_backfill.py`)은 네이버 블로그 → LLM(`gpt-4o-mini`) 추출 결과를
`products.catalogs[].insight` 서브필드에 저장한다. 실데이터 점검 중 두 종류의 오류를 발견했다:

1. **플래그 드리프트** — `catalogs[].has_insight`가 `insight` 필드의 실제 유무와 어긋남
   (예: `has_insight: false`인데 `insight`는 채워져 있음). UI가 `has_insight`로 노출을 판단하면
   실제로 있는 인사이트가 안 보이는 버그.
2. **소스 불일치** — `disp`(카탈로그명)의 용량/개수와 수집된 근거(evidence) 원문의 용량/개수가 다름
   (예: `disp`="…92g 12개"인데 evidence 원문은 전부 "96g"). 풀네임 검색 시 유사 상품이 섞여 들어온 것.

이 오류들을 규칙으로 검증하고, 발견 즉시 자동 수정하는 확장 가능한 파이프라인을 만든다.

## 목표 / 비목표

**목표**
- 규칙 기반의 확장 가능한 검증 프레임워크 (규칙 추가 = 레지스트리 항목 1개 추가)
- 감지 + 무조건 autofix (발견하면 바로 고침)
- 기존 `step_*.sh` / `exports/` 관례 준수, 단독·멱등 실행
- v1 규칙 3개: `flag_drift`, `source_mismatch`, `stale_schema`

**비목표 (YAGNI)**
- 상시 백그라운드 루프(`run_validate_loop.sh`) — v1 미포함, 필요 시 후속
- `--apply` 게이트/규칙별 autofix on-off 정책 — 방식은 "무조건 autofix"로 확정
- insight 격리(quarantine) 필드 — B는 재수집 큐잉 방식으로 결정됨

## 아키텍처

신규 단독 스크립트 `insight/db/validate_insights.py` + `insight/db/step_validate.sh`.

```
products.find({"type":"package"}) 순회
  └─ 각 catalog(ctlg_no) → 등록된 RULES 전부 검사
       └─ rule.detect(ctx) 가 violation 반환 → rule.fix(...) 로 mongo update + 리포트 누적
  └─ 종료: 콘솔 요약 + insight/db/exports/validation_report_<ts>.json
```

### 규칙 인터페이스 (선언형)

각 규칙은 최소 두 함수만 가진다. 순회·리포트·`--dry-run`·`--rules` 필터는 프레임워크가 공통 처리.

```python
Rule(
  id="flag_drift",
  severity="low",              # low | high
  detect=lambda ctx: violation | None,      # ctx = {pkg, catalog, insight, db, opts}
  fix=lambda ctx, violation: mongo_update,  # dry-run이면 프레임워크가 적용을 건너뜀
)
RULES = [ ... ]   # 이 리스트에 추가하면 새 규칙
```

`ctx`는 `pkg_uid`, `ctlg_no`, `disp`, `catalog` dict, `insight` dict, `db` 핸들, `opts`(CLI 옵션)를 담는다.
`fix`는 부작용을 직접 실행하지 않고 `update_one` 인자(filter/update/array_filters)를 반환해 프레임워크가
`--dry-run` 여부에 따라 적용/스킵한다.

## v1 규칙 세트

### R1. `flag_drift` (severity: low)
- **감지**: `bool(catalog.get("insight"))` != `catalog.get("has_insight")` — 양방향
  (insight 있는데 flag false / flag true인데 insight 없음)
- **autofix**: `has_insight`를 실제 값으로 `$set`
  ```
  update_one({"_id": pkg_uid}, {"$set": {"catalogs.$[c].has_insight": <actual>}},
             array_filters=[{"c.ctlg_no": ctlg}])
  ```

### R2. `source_mismatch` (severity: high) — 오류 B
- **감지 (휴리스틱 1차 + LLM 폴백)**:
  1. `disp`에서 용량/개수 파싱: 정규식으로 `92g`, `12개`, `1.5kg`, `500ml` 등 → 정규화(단위 환산: kg→g, l→ml)
  2. 비어있지 않은 insight의 evidence(각 point/faq의 `evidence`/`answer_evidence`) `title`+`quote`에서 동일 패턴 추출
  3. disp 용량과 evidence 다수(과반) 용량이 **명확히 다르면** → mismatch 확정
  4. disp에 용량 파싱 실패 / evidence 숫자 없음 / 애매하면 → **LLM 게이트**(`gpt-4o-mini`)에게
     "이 카탈로그명과 근거 묶음이 같은 상품인가?" 판정. `--no-llm-gate` 시 이 단계 스킵(보수적으로 통과 처리)
- **autofix (재수집 큐잉)**: insight를 재수집 가능한 빈 상태로 무효화한다. 이는
  `catalog_insight_backfill.py --retry-empty`의 재큐 조건(`not dims and n_sources==0 and attempts<max`)을
  충족시켜 다음 비정형 루프가 재수집하게 만든다.
  ```python
  # prev_attempts = (기존 insight.get("attempts") or 0)
  {"$set": {
      "catalogs.$[c].insight": {
          "dims": [], "faqs": [], "n_sources": 0,
          "attempts": prev_attempts + 1,
          "invalidated": "source_mismatch",     # 추적용 마커
          "fetched_at": now_iso(), "source": "naver_review"},
      "catalogs.$[c].has_insight": False}}
  # array_filters=[{"c.ctlg_no": ctlg}]
  ```
  재수집이 실제 인사이트를 만들면 `catalog_insight_backfill`이 insight를 통째 교체하므로 `invalidated`
  마커는 자연히 사라진다.
- **정직한 한계 (문서화됨)**: 재수집은 같은 `disp` 키워드로 다시 `collect()`한다. mismatch 원인이
  키워드 모호성(해당 용량의 블로그가 원래 없음)이면 재수집도 같은 결과 → `attempts` 한도까지 시도 후
  "진짜 리뷰 없음"으로 확정된다. **틀린 insight를 남기느니 빈 값으로 수렴**시키는 것이며, 이는 기존
  empty-confirmation 철학과 일치한다.

### R3. `stale_schema` (severity: low)
- **감지**: 비어있지 않은 insight(`dims` 존재)인데 `fetched_at` 또는 `source` 필드 누락
- **autofix**: 없음 — 리포트만. 누락된 타임스탬프/출처를 지어낼 수 없으므로 사람이 판단.

## CLI / 통합

`validate_insights.py` 플래그:
- `--limit N` — 처리 카탈로그 수 제한(0=전체)
- `--dry-run` — 감지만, 수정 안 함 (리포트만 생성)
- `--rules a,b` — 특정 규칙만 실행 (기본: 전체)
- `--llm-gate` / `--no-llm-gate` — R2의 LLM 폴백 on/off (기본 on, 비용 제어용)
- `--priced-only` — 가격 보유 카탈로그만 (기존 관례와 일치)

환경변수: `MONGO_URI`(기본 `mongodb://localhost:47017/?directConnection=true`),
`INSIGHTS_DB`(기본 `insights`), `OPENAI_API_KEY`(LLM 게이트 사용 시), `INSIGHT_MODEL`(기본 `gpt-4o-mini`).

`step_validate.sh`:
- `_pipeline_common.sh` 소싱, 파일 락으로 중복 실행 방지 (다른 `step_*.sh`와 동일 골격)
- 단독 실행 가능, 멱등

리포트: `insight/db/exports/validation_report_<ts>.json` — 규칙별 위반 목록
(`{rule_id, severity, pkg_uid, ctlg_no, disp, detail, fixed: bool}`) + 요약 카운트.

## 데이터 흐름 / 멱등성

- 검증기는 읽기(전체 catalog 스캔) → 규칙 검사 → 쓰기(autofix update)로 단방향.
- `flag_drift`/`stale_schema`는 결정론적이라 반복 실행해도 수렴(두 번째엔 위반 0).
- `source_mismatch`는 무효화 후 재실행 시 insight가 비어(`dims` 없음) 다시 감지 대상이 아니므로 재무효화 안 됨.
- LLM 게이트는 비용 발생 지점 — `--no-llm-gate`로 완전 차단 가능, 휴리스틱만으로도 명확한 케이스는 잡힘.

## 에러 처리

- Mongo 연결 실패 → 즉시 종료(exit non-zero), 기존 스크립트 관례.
- LLM 게이트 호출 실패(429 등) → 해당 레코드는 **보수적으로 통과**(mismatch 아님으로 처리)하고 리포트에
  `llm_error` 플래그 기록. 멀쩡한 insight를 오판으로 날리지 않기 위함.
- `--dry-run`에서는 어떤 쓰기도 하지 않고 "would fix" 리포트만.

## 테스트 전략

- 용량/개수 파서 단위 테스트: `92g`, `1.5kg`, `500ml`, `12개`, `x24`, `24입` 등 표기 변형.
- R1: 인위적으로 flag를 어긋나게 한 fixture → 감지·수정 확인, 재실행 시 위반 0(멱등).
- R2 휴리스틱: disp 92g / evidence 96g fixture → mismatch 감지 + 무효화 update 형태 검증.
- R2 무효화 결과가 `--retry-empty` 재큐 조건을 만족하는지 확인.
- `--dry-run`이 쓰기를 하지 않는지 확인.
