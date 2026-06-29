# Product Identity Graph — 크로스마켓 엔티티 레졸루션 PoC

11번가 / 쿠팡 / 네이버 / 아마존의 상품 리스팅을 하나의 **"진짜 상품"(canonical) 노드**로
묶는 엔티티 레졸루션 엔진의 검증용 미니 PoC. **의존성 없는 순수 Python (3.8+)** — 설치 없이 실행됩니다.

## 실행

```bash
python3 run.py
```

일반화 점검(사전에 없는 브랜드로 재튜닝 없이 측정):

```bash
python3 holdout_eval.py   # → outputs/holdout_report.md (in-sample vs held-out 재현율)
```

`outputs/` 에 다음이 생성됩니다:

| 파일 | 내용 |
|---|---|
| `report.md` / `report.html` | 사람이 읽는 검증 리포트 (문서/덱에 붙여넣기, 브라우저로 열기) |
| `identity_graph.json` | 해소된 canonical 상품 노드 + 멤버 리스팅 |
| `metrics.json` | 기계 판독용 지표 (블로킹 재현율, P/R/F1, 퍼널) |
| `blocking_comparison.csv` | MinHash vs 하이브리드 블로킹 비교 |

## 파이프라인 (cascade)

```
리스팅 ─▶ 블로킹 ─▶ 필드 유사도 + 속성 충돌 ─▶ 라우팅 ─▶ union-find 클러스터
           (후보 생성)   (점수 + variant 가드)    │
                                                  ├─ 점수 高 → 자동 병합
                                                  ├─ 경계 밴드 → LLM 판정 ← 여기만 비쌈
                                                  └─ 점수 低/충돌 → 자동 기각
```

- **블로킹** (`pig/blocking.py`): 제안안 `MinHash LSH` + 권장 `Hybrid`(결정적 모델키 / GTIN +
  브랜드정규화 char-ngram + MinHash). 둘의 재현율을 직접 비교합니다.
- **속성 추출** (`pig/normalize.py`): 브랜드/모델코드/색상/커넥터/용량/무게/팩수를 구조화 추출.
  변형(500ml↔1.5L, 2팩↔6팩, 블랙↔실버)을 가르는 핵심 단계.
- **유사도 + 가드** (`pig/similarity.py`): 구조 신호(브랜드·모델·GTIN·속성) 중심 점수.
  하드 속성 충돌이 있으면 병합 밴드에서 탈락 → variant/번들/GTIN재사용 오병합 방지.
- **LLM 판정** (`pig/adjudicate.py`): 경계 케이스만 에스컬레이션.
  기본은 오프라인 결정적 스탠드인. 실제 모델로 전환:

  ```bash
  PIG_USE_CLAUDE=1 ANTHROPIC_API_KEY=sk-... python3 run.py   # claude-haiku-4-5 호출
  ```

## 데이터

`data/listings.json` — 43개 합성 리스팅 / 22개 정답 엔티티. 의도적으로 어려운 케이스 포함:
한↔영 cross-lingual, 색상/용량/팩수/커넥터/사이즈 변형, 번들, 회색시장(병행수입) 난독화,
GTIN 재사용 함정, 셀러 스팸 키워드. `entity_id`는 정답 라벨(평가 전용, 파이프라인은 무시).

## 정직한 한계

- 작은 수작업 벤치마크입니다. 높은 F1은 *설계된 하드 케이스를 처리한다*는 새너티 체크지
  **운영 정확도 주장이 아닙니다.** 실데이터 하드 밴드에서 SOTA도 F1 72~85%.
- cross-lingual 브리지는 데모용 소형 이중언어 사전 → 운영은 다국어 bi-encoder + ANN으로 교체.
- **데이터 수집(크롤/API)·법적 리스크(특히 쿠팡 Akamai/DB권)는 범위 밖.** 이 결과물은 *매칭 엔진*만 증명합니다.
