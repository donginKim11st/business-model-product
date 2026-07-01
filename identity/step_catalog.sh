#!/bin/zsh
# 카탈로그명 추출 1배치(동기) — n8n /step/catalog 용.
# run_catalog.py(스포츠 정형 all_brands.csv → catalog_decomposed.csv + catalogs.csv)를 1회 실행하고,
# 산출 카운트 진행률 JSON 한 줄만 stdout 으로 낸다(트리거 서버가 마지막 JSON 줄을 파싱).
# verbose 는 catalog_pipeline.log 로. 전체 재변환·멱등(수초). 진행률은 항상 remaining=0(1회성).
# 옵션 env: CATALOG_LLM_GATE=1 CATALOG_LLM_LIMIT=N (기본: 규칙만·무료).
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HERE/catalog_pipeline.log"
PY="${PY:-/usr/bin/python3}"

LLM_ARGS=""
[ "${CATALOG_LLM_GATE:-0}" = "1" ] && LLM_ARGS="--llm-gate --llm-limit ${CATALOG_LLM_LIMIT:-0}"

echo "===== $(date '+%F %T') [catalog] step 시작 (llm=${CATALOG_LLM_GATE:-0}) =====" >> "$LOG"
"$PY" "$HERE/run_catalog.py" ${=LLM_ARGS} >> "$LOG" 2>&1
RC=$?
echo "$(date '+%F %T') [catalog] run_catalog rc=$RC" >> "$LOG"

# stdout 마지막 줄 = 진행률 JSON(트리거 서버 파싱용). run_catalog stdout 은 로그로 갔음.
"$PY" - "$HERE/outputs/catalog_decomposed.csv" "$HERE/outputs/catalogs.csv" "$RC" <<'PY'
import csv, json, os, sys
dec, cat, rc = sys.argv[1], sys.argv[2], int(sys.argv[3])
def n(p):
    if not os.path.exists(p):
        return 0
    with open(p, encoding="utf-8-sig") as f:
        return max(0, sum(1 for _ in f) - 1)
rows, cats = n(dec), n(cat)
print(json.dumps({"stage": "catalog", "rc": rc, "rows": rows, "catalogs": cats,
                  "progress": {"catalog": {"total": rows, "done": rows, "remaining": 0}}},
                 ensure_ascii=False))
PY
