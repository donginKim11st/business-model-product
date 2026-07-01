# 카탈로그명 추출 (스포츠/아웃도어 정형 → 카탈로그)

`outputs/all_brands.csv`(30 브랜드 공식몰 정형)에서 깨끗한 카탈로그명을 뽑고 모델 단위로 묶는다.

## 실행
```bash
python3 run_catalog.py                              # 규칙만(무료·결정적)
python3 run_catalog.py --limit 500 \
  --dec-out outputs/catalog_decomposed.sample.csv \
  --cat-out outputs/catalogs.sample.csv             # 골든 샘플
python3 run_catalog.py --llm-gate --llm-limit 300   # 잔여 하드케이스만 gpt-4o-mini 보정
OPENAI_API_KEY=.. python3 catalog_geo.py --batch 200  # title_geo canonical LLM 배치(증분·캐시). n8n /step/catalog_geo 로 드레인
```

## 명명규칙 (타이틀 2종)
- **title_geo** = 브랜드 + canonical 모델명 + 유형 (색상·사이즈·성별 제외; AI검색/엔티티용). canonical 은 `catalog_geo.py` 배치가 채우며, 없으면 원 상품명 폴백.
- **title_commerce** = 브랜드 + 상품명 + 성별(공용 제외) + 유형 + 색상 + 사이즈. 신발 숫자는 mm, 가방 치수는 제외, 아디 A/ 접두 정리.

## 산출 (사이즈 단위 전개)
- `outputs/catalog_decomposed.csv` — **사이즈별 개별 카탈로그**(style_code 사이즈 리스트를 사이즈마다 별도 행). 컬럼: **title_geo**·**title_commerce**·product_name·gender·product_type·color·size·material·origin·needs_llm.
- `outputs/catalogs.csv` — 모델 롤업 요약(title_geo·title_commerce[사이즈 제외] + colors/n_colors/size_range/materials/origins/가격 집계)

## 구조
- `catalog_lexicon.py` — 도메인 사전(브랜드·성별·유형·색상·FOOTWEAR·COLOR_KO·style-code 접미사) 단일 출처
- `catalog_decompose.py` — Stage1(행별 분해·사이즈 전개) + title_geo/title_commerce
- `catalog_group.py` — Stage2(모델 롤업). style-code base(브랜드별) 우선, 이름 폴백
- `catalog_llm_gate.py` — 옵션 LLM 보정(needs_llm 잔여, 기본 OFF)
- `catalog_geo.py` — title_geo canonical LLM 배치(증분·캐시 `_catalog_canonical.json`)
- `run_catalog.py` / `step_catalog.sh` — 원샷 러너 / n8n `/step/catalog`
- `step_catalog_geo.sh` — n8n `/step/catalog_geo`(canonical 배치 드레인)

## 테스트
```bash
python3 -m pytest -v
```
