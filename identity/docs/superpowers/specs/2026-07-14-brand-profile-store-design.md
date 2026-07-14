# 브랜드 프로필 스토어 설계 (brand-profile-store)

- 날짜: 2026-07-14
- 브랜치: feat/canonical-identity-join
- 상태: 설계 확정, 구현 대기

## 배경 / 문제

가구 공식몰을 harvest할 때마다 브랜드별 "공식몰 속성"이 필요하다:

1. **크롤 기술 프로파일** — 플랫폼, 카테고리 코드, 딜레이·타르핏 가드, 워치독, 셀렉터
2. **상품 속성 스키마** — 이 브랜드 상품이 통합 HEADER 필드 중 뭘 실제로 채우나, 고유 옵션 구조
3. **브랜드 도메인 지식** — 주력 카테고리, 네이밍 패턴, 고시정보 위치(이미지 OCR 여부)
4. **수집 결과 통계** — 건수, 실패/독약 URL, 커버리지, 지난 harvest 대비 변동

현재 이 지식은 `brands_furniture.json`의 자유서술 `note` 필드 + `catalog_lexicon_furniture.py`/`furniture_catalog.py`의 `MALL_PROFILES` + 코드 하드코딩에 흩어져 있다. harvest마다 축적되지 않고, "이 브랜드일 때 검색해서 참고"하는 경로가 없다.

목표: 네 종류를 브랜드별로 축적하고, **다음 크롤러/추출기**와 **GEO·카탈로그 파이프라인**이 slug로 조회해 참고할 수 있게 한다.

## 핵심 설계 결정: 두 층 분리

성격이 다른 데이터를 하나의 저장소에 넣지 않는다.

| 층 | 종류 | 소비 시점 | 성격 | 저장소 |
|---|---|---|---|---|
| **A. 크롤 설정** | ① 크롤 기술 프로파일 | 크롤 시작 **전** | 입력/config, 사람이 PR 리뷰 | **git** (`brands_furniture.json`) |
| **B. 축적 지식** | ② 속성 스키마 ③ 도메인 ④ 통계 | 크롤 **이후** 파이프라인 | 산출/파생, harvest마다 갱신, 커밋 금지 | **MongoDB** (`brand_profiles`) |

**왜 ①은 Mongo가 아닌가**: 크롤러가 "이 몰을 어떻게 긁을지" 알려면 프로파일을 먼저 읽어야 한다. Mongo에 두면 크롤 시작에 DB 의존이 생기고, 딜레이·셀렉터 같은 결정적 설정이 PR 리뷰를 벗어난다. dongsuh 1.2s 타르핏 가드 같은 값은 git diff로 보여야 한다.

**왜 ②③④는 Mongo인가**: 이미 `furniture_products`가 Mongo에 있고 GEO·카탈로그 파이프라인이 Mongo를 읽는다. harvest 산출물이라 프로젝트 규칙상 git 커밋 금지 대상이다. MCP `mongodb-insights`로 사람/Claude 세션도 바로 질의할 수 있다.

## 아키텍처

```
   [크롤 전]   crawler ──읽음──▶  A층: brands_furniture.json (git)
                                     brand.crawl_profile = {…}
                      │ harvest 실행
                      ▼
   [크롤 후]   brand_profile.py (프로파일러 — 신규 모듈)
                      │ 산출 CSV/로그 → 지식 계산 → upsert(slug)
                      ▼
   [소비]      B층: Mongo brand_profiles 컬렉션
                GEO/카탈로그 ─get_profile()─▶  ·  MCP ─질의─▶
```

A층(입력·config·리뷰 대상)과 B층(산출·harvest마다 갱신·커밋 금지)을 잇는 것은 `brand_profile.py` 단일 모듈. 크롤러는 A만 읽고(부트스트랩 무의존), 파이프라인은 B만 읽는다.

## A층: `crawl_profile` 구조 (brands_furniture.json 확장)

기존 `note` 자유서술은 유지(사람용 요약)하고, 크롤러 동작을 결정하는 값만 구조화한다.

```jsonc
{
  "slug": "dongsuh", "name_ko": "동서가구",
  "base_url": "https://www.dongsuhfurniture.co.kr",
  "platform": "godomall", "status": "active",
  "note": "종합가구. cateCd 019=BEST(중복 제외)",   // 유지

  "crawl_profile": {                    // 신규 — 크롤러 부트스트랩
    "category_codes": ["019"],
    "category_note": "019=BEST 중복 제외",
    "delay_s": 1.2,                     // 타르핏 가드 — 임의 단축 금지
    "resumable": true,                  // godomall 재개형(진행 저널)
    "watchdog_s": 90,
    "gosi_in_image": true               // 고시 OCR 필요 플래그
  }
}
```

`crawl_profile`에는 스키마·통계를 넣지 않는다(그건 B층 산출).

## B층: Mongo `brand_profiles` 스키마

harvest마다 slug 키로 upsert되는 문서 1개/브랜드.

```jsonc
{
  "slug": "dongsuh", "name_ko": "동서가구",
  "updated_at": "2026-07-14T…", "last_harvest_id": "2026-07-14T09:00",

  // ② 속성 스키마
  "schema": {
    "fields": {
      "material": {"coverage": 0.98, "distinct": 14, "top": ["MDF","원목"]},
      "bed_size": {"coverage": 0.0},
      "width_cm": {"coverage": 0.91}
    },
    "options": { "색상": ["월넛","화이트","오크"], "사이즈": ["Q","K"] }
  },

  // ③ 도메인 지식
  "domain": {
    "top_categories": [["침대",320],["소파",210]],
    "naming_patterns": ["모델명 = 한글명 + 영문코드"],
    "gosi_in_image": true,
    "notes_freeform": "cateCd 019=BEST 중복 제외"   // A층 note 승계
  },

  // ④ 수집 통계
  "stats": {
    "count": 1240, "new": 32, "dropped": 5,
    "failed_urls": 3, "poison_urls": ["…"],
    "coverage_delta": -0.01, "duration_s": 4120, "throttle_hits": 2,
    "regression": false
  },

  // 이력 링버퍼(최근 N=20)
  "history": [ {"harvest_id":"…","count":1208,"coverage":0.90} ]
}
```

설계 결정:
- **문서 1개/브랜드 + `history[]` 링버퍼(N=20)**: 시계열 컬렉션 대신 단일 문서. 파이프라인이 `find_one({slug})` 한 번으로 현재+추세 획득. 무한 성장 방지.
- **`schema.fields`는 harvest 산출 CSV에서 자동 계산** — 사람이 안 씀. 커버리지 급락이 곧 크롤 회귀 신호 → ④가 QA 게이트로 재활용.
- **`domain.notes_freeform`은 A층 `note` 승계**, 나머지 도메인 필드는 산출에서 계산 → 사람 서술과 기계 계산을 한 문서에서 분리 보관.

## 연결 모듈: `identity/brand_profile.py`

A층↔B층을 잇는 유일한 접점. 인터페이스 3개.

```python
def load_crawl_profile(slug) -> dict:
    """brands_furniture.json → crawl_profile. 없으면 platform 기본값. DB 무의존."""

def build_and_upsert(slug, harvest_csv, run_log) -> dict:
    """산출 CSV/로그 → schema/domain/stats 계산 → brand_profiles upsert + history append.
       Mongo 실패 시 outputs/profiles/<slug>.json 폴백. harvest는 죽이지 않음."""

def get_profile(slug) -> dict | None:
    """Mongo find_one({slug}). MCP로도 동일 컬렉션 질의 가능."""
```

소비 지점 연결:
- 크롤러(`extract_all_furniture.py`/`extract_furniture_engine.py`): 시작부 `load_crawl_profile(slug)` → 딜레이·카테고리 자동 적용. 하드코딩/`note` 참조 대체.
- harvest 파이프라인(`run_furniture_pipeline.py`): 병합 직후 `build_and_upsert(slug, …)` 한 줄.
- GEO·카탈로그(`map_geo_furniture.py` 등): `get_profile(slug)`로 도메인·스키마 참조(선택).
- 사람/Claude: MCP `mongodb-insights`로 `brand_profiles` 직접 질의.

## 에러 처리

원칙: A층은 견고하게(크롤 무중단), B층은 실패해도 harvest를 죽이지 않게.

| 상황 | 처리 |
|---|---|
| `crawl_profile` 없음 | platform 기본값 반환(godomall→resumable+watchdog 등). 크롤 안 멈춤. 하위호환 |
| Mongo 연결 실패(harvest 후) | warn 로그 + `outputs/profiles/<slug>.json` 폴백 저장. harvest 성공 유지 |
| 커버리지 급락(`coverage_delta` < 임계) | upsert하되 `stats.regression: true` + 경고. `qa_geo_mapping.py` QA 게이트와 같은 결 |
| `delay_s` 단축 시도 | 스키마 하한 검증(dongsuh≥1.2). CLAUDE.md 가드를 코드로 못박음 |

## 테스트

`identity/tests/test_brand_profile.py` (기존 pytest 119개에 합류):
- `load_crawl_profile`: 프로파일 있음 / 없음(기본값 폴백) / 미등록 slug
- `build_and_upsert`: 샘플 CSV → `schema.fields` 커버리지 정확성, history 링버퍼 N=20 상한, Mongo 없을 때 파일 폴백
- `delay_s` 하한 검증(dongsuh 1.2 미만 실패)
- Mongo는 `INSIGHTS_DB=insights_demo` 또는 mongomock로 격리 — 운영 `insights`에 테스트 쓰기 금지

## 마이그레이션 (점진적, 무중단)

1. `brand_profile.py` + 테스트 추가 (기존 흐름 무변경)
2. `brands_furniture.json` 9개 브랜드 `note` → `crawl_profile` 구조화(`note` 유지)
3. `run_furniture_pipeline.py`에 `build_and_upsert` 호출 추가 → 첫 harvest부터 `brand_profiles` 채움
4. 크롤러/엔진이 `load_crawl_profile` 읽도록 전환(브랜드 1개 검증 후 확산)
5. GEO 단계가 `get_profile` 참조(선택)

## DB 쓰기 범위

- 신규 컬렉션: `brand_profiles`
- DB: 운영 `insights`(furniture_products와 동일 DB). **실제 적재 전 명시 승인** 필요.
- 개발/테스트: `insights_demo`.

## 범위 밖 (YAGNI)

- graphify 지식그래프 연동(관계 질의 필요해지면 그때)
- 스포츠 30몰 확장(가구 9몰 검증 후)
- 시계열 전용 컬렉션(history 링버퍼로 충분)
