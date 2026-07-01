# 카탈로그명 추출 (스포츠/아웃도어 정형 → 카탈로그)

`outputs/all_brands.csv`(30 브랜드 공식몰 정형)에서 깨끗한 카탈로그명을 뽑고 모델 단위로 묶는다.

## 실행
```bash
python3 run_catalog.py                              # 규칙만(무료·결정적)
python3 run_catalog.py --limit 500 \
  --dec-out outputs/catalog_decomposed.sample.csv \
  --cat-out outputs/catalogs.sample.csv             # 골든 샘플
python3 run_catalog.py --llm-gate --llm-limit 300   # 잔여 하드케이스만 gpt-4o-mini 보정
```

## 산출 (명명규칙: 브랜드 + 상품명 + 속성 최대 3, 성별→유형→색상)
- `outputs/catalog_decomposed.csv` — 행별 분해(brand_norm·**product_name**·gender·product_type·color·size·material·origin·**catalog_name**·needs_llm). 속성값은 개별 컬럼으로도 노출.
- `outputs/catalogs.csv` — 모델 단위 카탈로그(대표 **catalog_name**=브랜드+상품명+성별+유형[색상제외] + colors/n_colors/size_range/materials/origins/가격 집계)

## 구조
- `catalog_lexicon.py` — 도메인 사전(브랜드·성별·유형·색상·style-code 접미사) 단일 출처
- `catalog_decompose.py` — Stage1(행별 분해). `decompose_row(row)`
- `catalog_group.py` — Stage2(모델 묶음). style-code base(브랜드별) 우선, 이름 폴백
- `catalog_llm_gate.py` — 옵션 LLM 보정(기본 OFF, 캐시·비용상한)

## 테스트
```bash
python3 -m pytest -v
```
