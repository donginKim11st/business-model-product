# identity 합류 — 후속 작업 (재개용)

> canonical product 노드 합류 파이프라인. **하류(매처·씨앗·합류·보정DB·대시보드·n8n)는 완성·가동 중.**
> 남은 건 **정확도를 실제로 올리는 데이터 공급(OPT_NM/변형 REG)** — 변형 카테고리가 스코프에 들어올 때 붙인다.

## 지금까지 (DONE, 가동 중)
- **합류 파이프라인 T1~T6:** 씨앗 export → 매칭 → backfill → reload 보존. (`export_identity_seed.py`,
  `identity_seed_match.py`, `identity_backfill.py`, `load_mongo._preserve_async_fields`)
- **오탐 방지:** 카테고리 게이트 + 색상어 가드. 실데이터 30건 오탐 3→0.
- **C1 메커니즘:** 매처 `(recall, color_match, size_match)` 사전식 tie-break. 씨앗에 size/color/barcode 컬럼.
- **보정 루프 + DB:** `identity_guidelines/labels/calib_runs`(Mongo). `identity_calibrate.py`(review/ingest/recommend).
- **대시보드:** 보정 콕핏(`identity_dashboard.py`) + 전수검증 A/B(`eval_identity_full.py`). 다크 crypto 테마.
- **인터랙티브 라벨러:** 트리거서버 `/calib/ui` (`identity_calib_api.py`).
- **n8n:** `정체(identity) 합류` 워크플로우 생성·**활성**(ID acPviC4cNOhv4WHL). 5분마다 `/step/identity` 드레인.

## 검증된 핵심 사실
- 이름만 매칭 precision **35%** → 색/사이즈 변별(C1) **76%** (+41p). recall 99%. (`eval_identity_full.py`)
- 비용 **API $0**(LLM 없음, 순수 CPU). 전체 1회 매칭 ~12분.
- C0(Oracle PD_CTLG): `BAR_CODE`는 REG 1001(67%)만 → C3 니치. **`OPT_NM`(변형 옵션)이 변형 REG에 100%** → C1의 데이터 소스.
- **주의:** REG_TYP_CD = 카테고리 아닌 '처리 대상 스코프' 마커. 강키 가용은 SP_REG에 무엇을 넣느냐에 달림.

## 남은 일 (후속, 게이트=변형 REG 스코프 유입)
1. **[C1-data] OPT_NM → catalog/씨앗** ← 최고 레버 (+41p). 설계: [C1_DATA_DESIGN.md](C1_DATA_DESIGN.md)
   - Oracle build(`archive/build_all_bndl.py`) SELECT 에 `OPT_NM, BAR_CODE` 추가 → trees `counts[]` 에 실음.
   - `demo_load_trees.iter_catalogs`(insight/db/demo_load_trees.py:57-61) → catalog 에 `color/size/barcode` 추가
     (OPT_NM 파싱: "색상:블랙/사이즈:270" → color/size).
2. **[스코프] 변형 REG 를 SP_REG 에 추가** — 의류 등 OPT_NM 있는 등록유형을 대상 스코프로.
3. **[C3-data] identity gtin → all_brands.csv 출력** — `official_extract.py`가 이미 gtin 추출, CSV 컬럼만 추가.
   barcode 있으면 매처 strong_keys 로 정확 매칭(이미 지원). 보조(REG 1001류 한정).
4. **[보정 가동]** 같은-도메인 데이터 들어오면 `/calib/ui` 에서 사람 라벨 → recommend → 의류 status
   `needs_strong_key → effective` 전환을 `identity_calib_runs` 이력으로 확인.
5. **[선택] 콕핏 라이브 서빙** — 트리거서버에 `/calib/dashboard` 라우트 추가(현재 정적 HTML 생성만).

## 현재 운영 상태 (식품 스코프)
n8n 워크플로우 활성. 매 틱 ~4s(배치 200), 식품은 교차도메인 게이트로 **합류 0**(정상), `remaining` 유지(정직한 백로그). $0.
→ 위 1·2 가 붙는 순간 같은 파이프라인이 실제 합류 + precision 35→76% 시작.

## 파일 지도
| 역할 | 파일 |
|---|---|
| 씨앗 export | `insight/db/export_identity_seed.py` |
| 매칭(게이트·색상가드·tie-break) | `insight/db/identity_seed_match.py` |
| 합류 backfill | `insight/db/identity_backfill.py` |
| reload 보존 | `insight/db/load_mongo.py` (`_preserve_async_fields`) |
| 보정 DB | `insight/db/identity_guidelines_db.py` |
| 보정 루프 CLI | `insight/db/identity_calibrate.py` |
| 보정 API/UI | `insight/db/identity_calib_api.py` (`/calib/*`) |
| 콕핏 대시보드 | `insight/db/identity_dashboard.py` |
| 전수검증 A/B | `insight/db/eval_identity_full.py` |
| n8n 워크플로우 | `insight/db/n8n/identity_join.json` |
| 임계/도메인 config | `insight/db/identity_name_thresh.json`, `identity_domain_map.json` |
| 전체 계획 | `../../CANONICAL_JOIN_PLAN.md` (repo 루트) |
| C1-data 설계 | `C1_DATA_DESIGN.md` (이 폴더) |
