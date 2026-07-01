#!/bin/zsh
# 인사이트 검증+autofix 1회 — n8n 버튼/수동용. 규칙(flag_drift/source_mismatch/stale_schema)을
# products.catalogs[].insight 에 적용하고 JSON 요약 반환. 리포트: exports/validation_report_*.json
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT" || exit 1
export MONGO_URI="${MONGO_URI:-mongodb://localhost:47017/?directConnection=true}"
export INSIGHTS_DB="${INSIGHTS_DB:-insights_demo}"
PY=${PY:-/usr/bin/python3}
# OPENAI 키 로드(LLM 게이트용) — 없으면 게이트는 자동 비활성(휴리스틱만).
set -a; eval "$(grep -E '^export OPENAI_' run.sh 2>/dev/null)"; set +a
mkdir -p exports
RES="$("$PY" db/validate_insights.py "$@" 2>>exports/validate.log)"
[ -z "$RES" ] && RES='{"stage":"validate","error":"fail"}'
echo "$RES"
