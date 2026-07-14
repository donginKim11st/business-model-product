# CLAUDE.md

상품을 두 축으로 보는 통합 파이프라인 워크스페이스(product-graph): **identity**(정형 — 공식몰 크롤→카탈로그) + **insight**(비정형 — 여론/수요 GEO 인사이트).

## 세션 시작 시
- `identity/HANDOFF.md`가 **다음 세션 진입점** — 카탈로그 파이프라인 전체 상태·치트시트·n8n 워크플로 ID가 여기 있다. 먼저 읽을 것.
- 상세 설계: `MERGE_PLAN.md`(리포 통합 근거) · `identity/FURNITURE_CATALOG_DESIGN.md` · `identity/CATALOG_README.md`

## 구조 지도
```
business-model/
├── run.sh → insight/run.sh 심링크    # API 키(OPENAI/NAVER/YOUTUBE 등). gitignore — 절대 커밋 금지
├── identity/                         # 정형: 스포츠 30몰 + 가구 9몰
│   ├── brands_furniture.json         # 가구 브랜드 레지스트리(jakomo·bflamp·wooree·vittz·flora·mothershome·prielle·dongsuh·dotoro)
│   ├── brand_profile.py               # 브랜드 프로필 스토어: A층 crawl_profile 읽기 + B층 brand_profiles 계산/조회
│   ├── run_furniture_pipeline.py     # 가구 원샷: 추출→병합→GEO매핑→QA→리포트
│   ├── extract_furniture_<slug>.py / extract_furniture_engine.py   # 몰별/범용 추출기
│   ├── map_geo_furniture.py → qa_geo_mapping.py                    # GEO 매핑 + 회귀 QA 게이트
│   ├── furniture_catalog.py · run_catalog.py(스포츠) · catalog_geo.py(canonical LLM 배치, gpt-4o-mini)
│   ├── catalog_lexicon*.py           # 도메인 사전 단일 출처(몰 고유 패턴은 MALL_PROFILES에만 추가)
│   ├── furniture_load_mongo.py / sports_load_mongo.py              # Mongo 적재
│   └── outputs/                      # 산출물(gitignore)
├── insight/                          # 비정형: naver_review_geo.py · run_batch.py(INSIGHT_MODEL=gpt-4o-mini)
│   └── db/                           # 적재·리포트: load_mongo.py · catalog_insight_backfill.py · pipeline_trigger.py(:8766, launchd)
└── unified_dashboard.py → dashboard.html·assets/ 재생성
```
평면 sibling import 구조 — `insight/`·`identity/` 내부 파일을 다른 폴더로 옮기지 말 것(README 참조).

## DB
- MongoDB `mongodb://localhost:47017/?directConnection=true` (env `MONGO_URI`)
- DB명: env `INSIGHTS_DB`, 기본 **insights**(운영). 데모/실험은 반드시 `INSIGHTS_DB=insights_demo` — `demo_load_trees.py`에 안전장치 있음.
- 참고: Claude Code 세션에서는 `.claude/settings.json`의 env로 `INSIGHTS_DB=insights_demo`가 기본 주입됨 — 운영 적재는 명시적으로 `INSIGHTS_DB=insights` 프리픽스 필요.
- 컬렉션: `products` / `sources` / `chunks` (insight 적재) · `furniture_products` (가구, 기존 3컬렉션과 분리 보관) · `brand_profiles`(가구 브랜드별 크롤 프로파일·속성 스키마·수집 통계, `_id`=slug)

## 실행 진입점
```bash
set -a; eval "$(grep '^export ' run.sh)"; set +a          # API 키 로드(공통)
cd identity && python3 -m pytest tests/ -q                 # 119 테스트
python3 run_catalog.py                                     # 스포츠 카탈로그 재빌드(무료·수초)
python3 furniture_catalog.py all                           # 가구 재빌드
python3 map_geo_furniture.py && python3 qa_geo_mapping.py  # 가구 GEO 매핑+QA
cd insight && python3 run_batch.py                         # 비정형 배치(비용 발생)
```
자동화: n8n(localhost:5678) + 트리거 서버 `insight/db/pipeline_trigger.py`(:8766) — 워크플로 ID는 HANDOFF.md §4.

## 하지 말 것
- **산출 데이터 커밋 금지**: `identity/outputs/`, `insight/archive/`, `insight/data/*.html`, `insight/site/`, `assets/`, `*.csv`, `*.jsonl` — 이미 gitignore. `git add -f` 절대 금지. **`insight/run.sh`는 실 API 키 보유 — 커밋 금지.**
- **대량 크롤링/LLM 배치는 명시 요청 시에만.** 실행 전 대상 건수·예상 비용(토큰/원화)을 먼저 보고하고 승인받을 것. `run_batch.py`·`catalog_geo.py --batch 0`·`run_furniture_pipeline.py --force`가 해당.
- 크롤링 시 몰별 가드 존중: dongsuh는 ~300요청 주기 IP 타르핏 실측 → 1.2s 감속 유지, godomall 계열은 재개형(진행 저널) 구조 유지. 딜레이 임의 단축 금지.
- **DB 쓰기 전 대상 DB·컬렉션 이름을 확인·보고**하고 진행. 운영 `insights`에 실험 데이터 쓰지 말 것.
- **근거 없는 단정 금지.** 수치·상태 보고 시 ✅실측(직접 실행/파일 확인)과 ⚠️추정을 구분 표기. 확인 안 한 경로·스크립트명은 쓰지 않는다.
- launchd 잡(`com.steve.*`)·n8n 활성 워크플로를 임의로 켜거나 끄지 말 것.
