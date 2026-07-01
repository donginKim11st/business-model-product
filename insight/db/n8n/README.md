# n8n 워크플로우 — identity 합류

기존 4개 워크플로우(structured/unstructured/youtube/rebuild)와 동일 패턴:
**Schedule → 트리거서버(:8766) `/step/<stage>` → `progress.remaining` 분기 → 드레인 루프.**

## identity_join.json — canonical product 합류 자동화

- **10분마다** → `POST host.docker.internal:8766/step/identity?batch=200` (X-Token 헤더)
  - 서버가 `step_identity.sh` 동기 실행: `export_identity_seed → identity_seed_match(게이트+색상가드+색/사이즈 tie-break) → identity_backfill` 1배치. **크롤 없음**(네트워크 0 → STEP_TIMEOUT 안전).
- **`remaining>0?`** (`{{$json.progress.identity.remaining}}`) → 참이면 3초 대기 후 재호출(드레인), 거짓이면 완료.

### import
n8n UI → Workflows → Import from File → `identity_join.json`. 활성화(Active) 후 `$env.TRIGGER_TOKEN`이 트리거서버 `TRIGGER_TOKEN`과 일치하는지 확인.

### 전제
- 트리거서버 기동: `TRIGGER_PORT=8766 python3 db/pipeline_trigger.py` (launchd 통로 데몬).
- identity 산출(`identity/outputs/all_brands.csv`)은 별도 프로세스(extract_all)가 채움 — 이 워크플로우는 **합류(join)만**.

### 진행률
- `GET /progress/identity` → `{identity:{total,done,remaining,joined,empty}}`. (모든 카테고리 적격, status∈{done,empty}=처리완료.)

## catalog.json — 스포츠 정형 → 카탈로그명 추출 자동화

- **매일(24h)** → `POST host.docker.internal:8766/step/catalog` (X-Token 헤더)
  - 서버가 `identity/step_catalog.sh` 동기 실행: `run_catalog.py` 전체 재변환(`identity/outputs/all_brands.csv` → `catalog_decomposed.csv` + `catalogs.csv`). **규칙 기반·무료·멱등**(수초). 크롤 없음.
  - 카탈로그명 = 브랜드 + 상품명 + 속성(성별→유형→색상→사이즈, 최대 5). 속성값은 개별 컬럼으로도.
- **1회성**이라 드레인 루프 없음(`progress.catalog.remaining`=0) → POST 후 바로 완료.
- LLM 보정(한/영 중복명 정리)은 기본 OFF. 켜려면 트리거서버 프로세스에 `CATALOG_LLM_GATE=1 CATALOG_LLM_LIMIT=N` env.

### import
n8n UI → Import from File → `catalog.json`. 활성화 후 `$env.TRIGGER_TOKEN` 확인.

### 전제
- 트리거서버에 `catalog` 스테이지 등록됨(`pipeline_trigger.py` STAGES). `identity/outputs/all_brands.csv`는 extract_all 이 채움 — 이 워크플로우는 **명명 추출만**.

## (참고) 보정은 n8n 아님
가이드라인 보정(라벨링)은 **사람 인더루프** — n8n 자동화 아님. 브라우저 `GET :8766/calib/ui`에서 사람이 라벨→추천. 자동화 대상은 합류(join)뿐.
