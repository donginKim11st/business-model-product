#!/bin/zsh
# title_geo canonical 1배치(동기) — n8n /step/catalog_geo 용(드레인 루프).
# catalog_geo.py 가 유니크 모델 batch 개를 gpt-4o-mini 로 canonical 화 → _catalog_canonical.json 누적.
# stdout 마지막 줄 = 진행률 JSON(progress.catalog_geo.remaining>0 이면 n8n 이 재호출).
# OPENAI_API_KEY 필요(트리거 서버 프로세스 env). 없으면 error+remaining 반환(무해).
# 배치 다 돌린 뒤 /step/catalog 를 1회 호출하면 title_geo 가 canonical 로 갱신된다.
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HERE/catalog_pipeline.log"
PY="${PY:-/usr/bin/python3}"
BATCH="${1:-${CATALOG_GEO_BATCH:-200}}"

echo "===== $(date '+%F %T') [catalog_geo] batch=$BATCH =====" >> "$LOG"
"$PY" "$HERE/catalog_geo.py" --batch "$BATCH" 2>>"$LOG"
