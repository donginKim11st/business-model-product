# 설계: 스포츠 정형 데이터 → 카탈로그명 추출

> 작성 2026-07-01 · 대상 리포 `business-model-product` · 위치 `identity/`
> 상태: 승인됨(brainstorming). 다음 단계 = 구현 계획(writing-plans).

## 0. 한 줄 요약

30개 스포츠/아웃도어 브랜드 공식몰 정형 CSV(`outputs/all_brands.csv`, 61,119행)의 원본 마케팅명(`name`)에서
**깨끗한 카탈로그명**을 규칙 기반으로 추출하고(Stage1 분해/정규화), 색상·사이즈로 흩어진 행을
**모델 단위 카탈로그**로 묶어 대표 카탈로그명을 만든다(Stage2 묶음). 기본은 결정적·무료, `--llm-gate`로
잔여 하드케이스만 gpt-4o-mini 보정.

## 1. 배경 / 문제

정형 데이터에 `name` 컬럼이 이미 있으나 이는 **카탈로그명이 아니라 원본 마케팅명**이라 잡음이 섞임:

| 원본 `name` | 잡음 |
|---|---|
| `나이키 에어 포스 1 '07 LV8 남성 신발` | 성별·유형 접미사(`남성 신발`) |
| `푸마 아반티 LS Puma Avanti LS` | 한글·영문 이름 중복 |
| `F50 하이퍼패스트 클럽 벨크로 아스트로 터프 축구화 키즈` | 브랜드는 별도 컬럼인데 이름엔 속성·성별 혼재 |
| `슈퍼브레이크 BLACK` | 색상 토큰이 이름에 붙음 |

기존 정규화기 `pig/normalize.py`는 **가전·화장품용 사전**(sony/apple, 앰플/세럼)이라 스포츠/신발엔 부적합.
→ 스포츠 정형 데이터용 카탈로그명 추출은 신규 구현이 필요.

### 데이터 프로파일(설계 근거, `all_brands.csv` 실측)
- 총 61,119행 / 30 브랜드. 컬럼: `source, brand, style_code, name, color, price, currency, category, gender, sizes, origin, material, mfg_date, url`.
- `name.startswith(brand)` = **1%** → 브랜드는 대개 이름에 없음(별도 컬럼). 크록스·휠라 등 일부만 포함.
- 한글+영문 혼재 = 26%(전부 중복은 아님; 색상·유형 영문 토큰 다수).
- `gender` 오염: `남성/여성/공용/키즈/WOMEN/MEN/FEMALE/male/아동/OUTLET/ACC/SHOES/남녀공용…`(성별 아닌 값 포함).
- `category` 혼재: `신발/의류/아울렛/상의/하의/가방/여성/남성/퍼포먼스/…`.
- `brand` 컬럼 오염: `KIDS/INTIMO/OUTLET` 등 브랜드 아닌 값 존재 → **브랜드는 `source` 슬러그 기준으로 정규화**.
- `style_code`의 색상 접미사는 **분리 가능하나 브랜드별 규칙이 다름**:
  아레나 `A6BL1LO15WHT`(끝3=색), 케이투 `KUF26C53HB`(끝2), 컬럼비아 `C72YM3621346`(끝3), 나이키 `IM5752-300`(`-`),
  푸마 `409960_01`(`_`), 아디다스 `KK1334`(색이 코드에 안 드러남).

## 2. 목표 / 비목표

**목표**
- 각 행에서 정규 `catalog_name`(브랜드 + 정제 상품명, 유형 명사 유지)과 분해 필드를 산출.
- 색상 통합 모델 단위로 묶어 대표 카탈로그명 + 집계(색상/가격/사이즈/변형수) 산출.
- 기본 결정적·무료. LLM은 옵션·잔여 한정·비용상한.

**비목표(YAGNI)**
- HTML 대시보드/뷰어(후속, 기존 `all_brands_html.py` 패턴 재사용 가능).
- DB 적재(`MERGE_PLAN.md` 소관).
- 11번가 PD_CTLG 매칭(별도 단계).

## 3. 확정된 결정사항

1. **catalog_name에 상품유형(축구화/자켓 등) 유지** — 11번가 카탈로그명 스타일(브랜드 + 상품명, 유형 명사 포함).
2. **묶음 단위 = 색상 통합 모델**(색상은 별도 SKU가 아니라 한 카탈로그의 변형).
3. **산출은 CSV 2종**(`catalog_decomposed.csv`, `catalogs.csv`).
4. **접근 = A코어 + `--llm-gate` 옵션**(규칙 기본, 잔여만 gpt-4o-mini).

## 4. 아키텍처

이미 만들어진 `outputs/all_brands.csv`를 읽어 변환만 하므로 **활성 추출 배치와 완전 독립**(파일 리더).

```
all_brands.csv ─▶ [Stage1 decompose] ─▶ outputs/catalog_decomposed.csv (61K행 + 분해컬럼)
                                             │
                                             ▼
                            [Stage2 group] ─▶ outputs/catalogs.csv (카탈로그 엔티티 + 대표명)
   (두 스테이지 결정적 · --llm-gate 시 잔여만 gpt-4o-mini)
```

### 파일 구성(신규)
| 파일 | 역할 · 인터페이스 |
|---|---|
| `catalog_lexicon.py` | 도메인 지식 단일 출처. `BRAND_SLUG`(슬러그→{ko,en,aliases}), `GENDER_MAP`, `PRODUCT_TYPES`(명사 리스트), `COLOR_TOKENS`, `STYLECODE_SUFFIX`(브랜드→접미사 규칙). 데이터만, 로직 없음 |
| `catalog_decompose.py` | Stage1. 순수함수 `decompose_row(row: dict) -> dict` + `main()` CLI. 입력 `all_brands.csv`, 출력 `catalog_decomposed.csv`. `--limit N` `--llm-gate` `--llm-limit N` |
| `catalog_group.py` | Stage2. `model_key(row) -> str` + `group(rows) -> list[catalog]` + CLI. 입력 `catalog_decomposed.csv`, 출력 `catalogs.csv`. `--llm-gate` |
| `catalog_llm_gate.py` | 공용 얇은 LLM 게이트. `gate_decompose(names)`, `gate_group(member_names)`. stdlib urllib, gpt-4o-mini(temp 0), 배치, `outputs/_catalog_llm_cache.json` 재개캐시, 비용상한 |
| `run_catalog.py` | 원샷 러너(Stage1→Stage2) + 요약 출력 |
| `tests/test_catalog_decompose.py` | `decompose_row` 브랜드 관례별 픽스처 검증(LLM off) |
| `tests/test_catalog_group.py` | `model_key`/묶음 검증(style-code base · 이름 폴백) |

설계 원칙: 도메인 지식은 전부 `catalog_lexicon.py`에 분리(파서 코드 순수·테스트 가능). `pig/normalize.py`가 사전-주도인 것과 동일.

## 5. Stage 1 — 행별 분해/정규화 (`decompose_row`)

원본 컬럼(brand·source·name·color·gender·category·style_code)으로 아래를 산출·추가:

| 산출 컬럼 | 규칙 |
|---|---|
| `brand_norm` | **`source` 슬러그** 기준(brand 컬럼 오염 회피) → `BRAND_SLUG[source]`의 한글명 |
| `gender_norm` | `GENDER_MAP`: `남성/MEN/MALE/남`→`M` · `여성/WOMEN/여`→`W` · `공용/UNISEX/남녀공용`→`U` · `키즈/KIDS/아동`→`K` · `OUTLET/ACC/SHOES/∅`→`∅`. 컬럼 우선, 미검출 시 name 토큰 보조 |
| `product_type` | `PRODUCT_TYPES` 명사 매칭(신발/축구화/러닝화/등산화/트레킹화/샌들/슬리퍼/부츠/자켓/재킷/바람막이/패딩/베이스레이어/티셔츠/맨투맨/후드/팬츠/바지/레깅스/원피스/브라탑/토트백/백팩/캡/모자/양말…). category+name 헤드/테일 노운 |
| `colorway` | `color` 컬럼 원본 보존 |
| `product_line` | **정제 상품명**. 처리순서: NFKC·공백정규화 → 괄호/`[]`/`★…★` 잡음 제거 → 성별토큰 제거(`(남)`/`(여)` 포함) → 색상토큰 제거(`color`값 분해 + `COLOR_TOKENS`, 트레일링 대문자 영문색 포함) → 브랜드 별칭 제거 → 한/영 중복 제거(휴리스틱: 트레일링 ASCII 세그먼트가 한글부 로마자화와 근사하면 드롭) → 공백정규화 |
| `catalog_name` | **정규 카탈로그명 = `brand_norm` + " " + `product_line`** (유형 명사는 상품명에 유지) |
| `needs_llm` | 저신뢰 플래그: `product_line` 공백/과단축(≤1토큰)/미인식 관례/잔여 대문자영문 다수 → `--llm-gate` 대상 |

**정제 예시**
- `F50 하이퍼패스트 클럽 … 터프 축구화 키즈`(source=adidas) → 성별 `키즈` 제거 → `catalog_name=아디다스 F50 하이퍼패스트 클럽 … 터프 축구화`, type=축구화, gender=K
- `슈퍼브레이크 BLACK`(source=jansport) → 색상 `BLACK` 제거 → `잔스포츠 슈퍼브레이크`, colorway=BLACK
- `푸마 아반티 LS Puma Avanti LS`(source=puma) → 한영중복·브랜드 제거 → `푸마 아반티 LS`

`--llm-gate` 시: `needs_llm`인 행만 `gate_decompose`로 `{product_line, product_type, gender}` 재산출(비용상한 내).

## 6. Stage 2 — 모델 묶음 + 대표 카탈로그명

### model_key 산출(우선순위)
1. 브랜드에 `STYLECODE_SUFFIX` 규칙 있으면 → `base_style_code`(접미사 제거) → `model_key=(source, base)`
   (아레나 끝3·케이투 끝2·컬럼비아 끝3·나이키 `-NNN`·푸마 `_NN` 등)
2. 규칙 없고 일반 구분자(`-`/`_`) 있으면 → 제네릭 접미사 분리
3. 그래도 불명 → **폴백** `model_key=(source, normalize(product_line + product_type + gender_norm))` (이름 기반)

### 그룹 집계 → `outputs/catalogs.csv`
`source · brand_norm · model_key · catalog_name(대표) · product_type · gender · colorways[] · style_codes(n) · price_min · price_max · size_range · n_variants · sample_url`

- **대표 catalog_name** = 그룹 내 최빈 `catalog_name`(동률 시 최단·완전형).
- `--llm-gate` 시: 멤버 `product_line`이 임계 이상 불일치하는 **의심 그룹만** `gate_group`으로 확인/분할·대표명 선택.

## 7. LLM 게이트 (`catalog_llm_gate.py`, 기본 OFF)

- gpt-4o-mini(env `OPENAI_MODEL` 오버라이드), temperature 0, `response_format=json_object`, stdlib urllib(=`llm_split.py` 패턴).
- 배치 프롬프트(다건/1콜). 재개 캐시 `outputs/_catalog_llm_cache.json`(입력 해시 키).
- 비용상한 `--llm-limit N`(기본 소량). **상한 초과분은 로그로 명시**(무음 절단 금지).
- cost-priority 기본값 준수: 게이트는 옵트인, 잔여 한정.

## 8. 오류처리 / 견고성

- `all_brands.csv` 부재 → 명확한 에러 + `extract_all.py` 실행 안내.
- 오염 `brand/gender/category` 값 → `∅`/None 매핑, 절대 크래시 금지.
- 빈 `name` 행 → 카운트 후 skip(요약에 보고).
- I/O는 `utf-8-sig`(기존 CSV와 동일). 재실행 시 산출 덮어쓰기(순수 변환·멱등).
- LLM 실패 → 해당 행/그룹은 규칙 결과 유지(폴백), 계속 진행.

## 9. 테스트 (결정적, LLM off)

- `test_catalog_decompose.py`: 브랜드 관례별 픽스처 → `product_line/gender_norm/product_type/catalog_name` 검증
  - 선두성별(블랙야크 `남성 …`), 트레일링유형+성별(아디다스 `… 축구화 키즈`), 상품명만(아이더 `ST 슬라이드 2`),
    트레일링색(잔스포츠 `… BLACK`), 한영중복(푸마), `(남)`(콜핑).
- `test_catalog_group.py`: style-code base 묶음(아레나/케이투/나이키 색상 통합) + 이름 폴백 묶음(아디다스) 검증.
- 500행 골든 샘플(`outputs/catalog_decomposed.sample.csv`) 육안검수.
- LLM 게이트는 테스트에서 off(결정성 보장).

## 10. 구현 순서(개략)

1. `catalog_lexicon.py`(사전) → 2. `catalog_decompose.py` + 단위테스트(TDD) → 3. `catalog_group.py` + 단위테스트
→ 4. `catalog_llm_gate.py`(옵션) → 5. `run_catalog.py` 러너 → 6. 500행 골든 샘플 검수 → 7. 전량 실행.
