# product-graph (통합 리포지토리)

같은 대상("상품")을 두 축에서 보는 두 파이프라인을 한 리포로 합친 것.
설계 근거는 [`MERGE_PLAN.md`](MERGE_PLAN.md) 참조.

```
business-model/                통합 루트 (이름 유지)
├── insight/    상품에 대한 여론/수요 (비정형) — 기존 business-model 전부
│   ├── naver_review_geo.py · run_batch.py · cost_runner.py · make_browse_1002.py
│   ├── db/       적재·스키마·리포트 (PG/Mongo/Oracle). load_mongo 등
│   ├── site/ · data/ · *.jsonl   입력/산출
│   └── archive/  실험 잔재 (+ _discarded_identity_graph_* : 폐기된 옛 pig 스냅샷)
├── identity/   상품의 정체 (정형) — 기존 product-identity-graph 복사본
│   ├── extract_<brand>.py · official_extract.py · ocr_*.py · harvest_*.py
│   └── pig/       엔티티 레졸루션 (blocking→similarity→adjudicate→resolve)
├── run.sh      → insight/run.sh 심링크 (공통 API 키 진입점)
└── README.md · MERGE_PLAN.md
```

## 핵심 설계 — 왜 collect/serve 로 잘게 안 나눴나
두 프로젝트 모두 **평면(flat) sibling import** 구조다 (`import naver_review_geo`,
`from official_extract import …`, `import pig`). 파일을 잘게 흩으면 ~100개 파일의
import가 깨진다. 그래서 **각 프로젝트를 내부 평면 그대로 한 패키지 폴더로** 묶어
import 무변경·즉시 실행 가능을 우선했다. `collect/`·`serve/` 세분화는 import 정리가
끝난 뒤 후속 단계.

- `insight/db/` 는 `ROOT = dirname(HERE)` 로 부모의 `naver_review_geo`/`run_batch` 를
  찾으므로 반드시 `insight/` 안에 함께 둔다.
- `identity/` 의 출력 경로·키 로드 경로는 새 위치로 교정됨
  (옛 `~/Laboratory/...` → `~/Work/business-model/identity/...`,
   키는 루트 `run.sh` 심링크에서 로드).

## 실행
```bash
# API 키 로드 (공통)
set -a; eval "$(grep '^export ' run.sh)"; set +a

# 비정형 인사이트 (insight)
cd insight && MONGO_URI="mongodb://localhost:47017/?directConnection=true" python3 run_batch.py
#   또는 db 파이프라인:  python3 db/catalog_insight_backfill.py --limit 200

# 정형 정체 (identity)
cd identity && python3 extract_all.py        # 30개 브랜드 추출 → identity/outputs/
```

## 원본/백업
- 머지 전 스냅샷: `insight/` 와 `identity/` 각각 git 커밋(pre-merge)으로 보존.
- `identity/` 는 `~/Laboratory/product-identity-graph` 원본을 **복사**한 것 — 원본 그대로 남아 있음.
- 비어 있던 `~/Workspace/business-model` 는 죽은 잔재 (사용 안 함).

## launchd 잡 (자동화)
- `com.steve.catalog-insight` : **꺼둠**(인사이트 추출 수렴 완료). 재개하려면
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.steve.catalog-insight.plist`
- `com.steve.rebuild` · `com.steve.youtube-backfill` : 새 경로(`insight/db/`)로 재로드됨.
- 모든 plist 의 스크립트 경로는 `insight/db/` 로 갱신 완료.
