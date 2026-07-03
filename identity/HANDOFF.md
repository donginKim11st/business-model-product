# 카탈로그 파이프라인 핸드오프 (2026-07-03 기준)

> 다음 세션 진입점. 스포츠(30몰)+가구(9몰) 정형 카탈로그 → 타이틀 2종 → n8n 자동화까지의 전체 상태.
> 상세 설계: `CATALOG_TITLES.md`(제목 2종 근거) · `CATALOG_README.md`(스포츠 실행) · `FURNITURE_CATALOG_DESIGN.md`(가구 3층 설계)

## 1. 핵심 개념 — 타이틀 2종
| | title_geo | title_commerce |
|---|---|---|
| 용도 | AI검색/엔티티(GEO), 비정형 조인 키 | 구매자 노출(SKU) |
| 구조 | 브랜드+canonical모델명+유형 (색상·사이즈·성별 제외) | 브랜드+상품명+성별(공용제외)+유형+색상+사이즈(신발 mm) |
| 단위 | 모델/엔티티 1개=1행 | 색상×사이즈(가구: 옵션 조합) 1개=1행 |
| canonical | gpt-4o-mini 배치(캐시, 건당 ~$0.00004) | 규칙 |

- 속성 순서: 성별→유형→색상→사이즈, 최대 5. `공용`은 제목 제외(정책). 색상 한글화(COLOR_KO).
- 속성값은 **개별 컬럼에도** 노출(schema/필터용). 모델 롤업 이름엔 색상·사이즈 제외.

## 2. 산출물 (identity/outputs/, gitignore)
**스포츠**: `catalog_decomposed.csv`(264,219 per-size) · `catalogs.csv`(35,684 모델) · `catalog_entities.csv`(30,394 GEO엔티티=비정형 조인 단위) · canonical `_catalog_canonical.json`(38,347)
**가구**: `catalog_variants_furniture.csv`(**168,277** variant) · `catalogs_furniture.csv`(20,096) · `furniture_decomposed.csv` · canonical `_catalog_canonical_furniture.json`(21,691+) · 옵션군 `options_groups_furniture_<slug>.csv`(라벨+값 JSON, cascade 병합됨)
엑셀은 같은 이름 .xlsx + `catalog_variants_furniture_attrs.xlsx`(속성 16컬럼 전개).

## 3. 모듈 맵 (identity/)
**스포츠**: `catalog_lexicon.py`(사전: 브랜드30·유형·TYPE_ALIASES·COLOR_KO·STYLECODE_SUFFIX) → `catalog_decompose.py`(Stage1+사이즈전개) → `catalog_group.py`(모델롤업+`entity_rollup`) → `catalog_llm_gate.py`(needs_llm 보정, OFF) → `run_catalog.py`
**가구**: `catalog_lexicon_furniture.py` → `map_geo_furniture.py`(GEO매핑+오버레이 3종: options/option_groups/OCR) → `furniture_catalog.py`(decompose→group→verify; `_promote_option` 축승격, `_group_combos` 군교차, cat_class, title 2종) → `qa_geo_mapping.py`
**공용**: `catalog_geo.py`(canonical LLM 배치 — 파라미터: `--in --store --brand-col --name-col --type-col --stage-key --batch 0=전량 --workers --redo-collisions`)
**수집**: `refetch_options.py` — `<slug|all> [N]` 옵션 재수집 · `--groups` 군구조 수집 · `--cascade` godomall 종속 2·3차(goods_ps.php mode=option_select→nextOption; `_CASCADE_BASE`에 몰 추가)
**리뷰차원**: `catalog_review_dims.py`(카테고리별, driver=size_fit 등 — 비정형용, 아직 미사용)

## 4. 인프라 / n8n
- 트리거 서버: `insight/db/pipeline_trigger.py` — launchd `com.steve.pipeline-trigger`(:8766, plist에 OPENAI_API_KEY 있음). 재기동: `launchctl kickstart -k gui/$(id -u)/com.steve.pipeline-trigger`
- STAGES: catalog · catalog_geo · furniture · furniture_geo (+기존 structured/unstructured/youtube/rebuild/identity/report/excel/dashboard)
- step 스크립트: `identity/step_catalog(.sh|_geo.sh)` `step_furniture(.sh|_geo.sh)` — JSON 1줄 반환(progress.<stage>.remaining)
- n8n(localhost:5678, 활성): `catalog`(UboDgcgC24jq0Sug, 매일) · `catalog_geo`(0qdeoWrfCFTdXK4V, 10분 드레인→완료시 /step/catalog) · `furniture`(2XugD6j47WYe1oB0) · `furniture_geo`(4J2OyMxhJfHiEKbA). 사본 `insight/db/n8n/*.json`
- OPENAI 키: **gitignore된 `insight/run.sh`** (root run.sh는 심링크). 절대 커밋 금지.

## 5. 실행 치트시트
```bash
cd ~/Work/business-model/identity
python3 -m pytest tests/ -q                  # 56 테스트
python3 run_catalog.py                        # 스포츠 재빌드(무료, 수초)
python3 furniture_catalog.py all              # 가구 재빌드(decompose→group→verify)
python3 map_geo_furniture.py && python3 qa_geo_mapping.py   # 가구 매핑(오버레이 반영)
# canonical (키: source ../insight/run.sh)
python3 catalog_geo.py --batch 0 --workers 12                              # 스포츠
python3 catalog_geo.py --in outputs/catalogs_furniture.csv \
  --store outputs/_catalog_canonical_furniture.json --brand-col brand \
  --name-col product_name --type-col l2 --stage-key furniture_geo --batch 0 --workers 12  # 가구
python3 refetch_options.py all --groups       # 옵션군 수집(재개형, 전상품)
python3 refetch_options.py dongsuh --cascade  # 종속 2·3차 병합
```

## 6. 주요 설계 결정(이유 포함)
- **사이즈 = 개별 카탈로그 전개**(범위 아님) — 사용자 요구. 모델 롤업/엔티티는 별도 층.
- **옵션군 구조 보존** — select별 라벨+값 JSON. 평탄화하면 색상×사이즈가 각1개로 오전개. `_group_combos`가 교차(cap 100), 추가상품 군은 라벨로 통째 제외.
- **캐스케이드**: dongsuh ~19% PDP에 존재. `_cascade` 마커로 탐지→AJAX로 nextOption 수집(936건 병합, +16.7K variant).
- **축 오염 방지**: 다토큰 세그먼트는 토큰 단위 분배, 라벨 폴백은 단일 토큰만.
- **상품명 전처리**: 추출된 속성 토큰·치수덤프·용도열거(첫개만)·열거자(1-1.)·품절·마케팅어 제거.
- **엔티티 해상도**: 콜라보/에디션 보존 프롬프트 + `--redo-collisions`. 스포츠 과병합 7.7→4.9%.
- 모음전(옵션=서로 다른 상품 열거)은 모델분해 합성행(`_opt_src`) — options 비는 게 정상.
- `_is_variantish`에 한글 사이즈(슈퍼싱글/퀸/소중대) 필수 — 없으면 사이즈 옵션이 모델분해로 오판.

## 7. 현재 수치(재빌드 시 변동)
스포츠: 유형커버 75% · 성별 84% · needs_llm 2.5% · 과병합 4.9%
가구: 속성컬럼 16종(색상 79%+ · 경도 58K · 형태 15K · 용도 1.7K) · 옵션 미분해 잔여 ~27%(대부분 정당한 구성정보)

## 8. 남은 일 (우선순위)
1. **비정형(리뷰) 조인** — 후순위 지정됨. 입력: `catalog_entities.csv`(스포츠)/`catalogs_furniture.csv`의 title_geo + `catalog_review_dims.dims_for()`. insight/ 엔진 재사용해 배치+n8n(catalog_geo 패턴).
2. ~~Mongo 적재~~ **완료(7/3)** — insights DB(47017): sports_products/catalogs/variants(61K/36K/264K) · furniture_products/variants/catalogs(19K/46K/20K) · **furniture_catalog_variants(156K, title_commerce SKU 층 신설, _id=내용해시 멱등)**. 재빌드 후 두 로더 재실행이 갱신 절차(run_furniture_pipeline.py가 가구 로더 호출).
3. OCR 백필 — "상세페이지 참고"/이미지 옵션·침구 사이즈 42% 공백 (`ocr_gosi_furniture.py`).
4. 가격편차 리뷰 큐 340건(needs_review=price_spread).
5. 재추출(크롤) 스케줄 — n8n엔 재빌드만 있음. `run_furniture_pipeline.py --force` 주1회 후보.
6. 가구 신규 canonical 키 잔여분 — n8n furniture_geo가 증분 처리 중(또는 §5 수동).

## 9. 규칙(반드시)
- 커밋에 **Claude-Session 트레일러 금지**. 원격=business-model-product. 브랜치 feat/canonical-identity-join.
- outputs/ gitignore — 산출물 커밋 금지. 코드는 conventional commit(scope catalog/furniture).
- Python 3.9 · stdlib only. 도메인 지식은 lexicon 파일에만.
- 테스트: `python3 -m pytest tests/ -q` 그린 유지. 가구는 `furniture_catalog.py verify`(골든 회귀)도.
