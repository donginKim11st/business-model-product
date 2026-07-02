#!/bin/zsh
# 가구 title_geo canonical 1배치(동기) — n8n /step/furniture_geo 용(드레인 루프).
# catalog_geo.py 를 가구 파라미터로: catalogs_furniture.csv 의 (brand, product_name) 유니크를
# gpt-4o-mini canonical 화 → _catalog_canonical_furniture.json 누적. OPENAI_API_KEY 필요.
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HERE/catalog_pipeline.log"
PY="${PY:-/usr/bin/python3}"
BATCH="${1:-${CATALOG_GEO_BATCH:-200}}"

echo "===== $(date '+%F %T') [furniture_geo] batch=$BATCH =====" >> "$LOG"
"$PY" "$HERE/catalog_geo.py" \
  --in "$HERE/outputs/catalogs_furniture.csv" \
  --store "$HERE/outputs/_catalog_canonical_furniture.json" \
  --brand-col brand --name-col product_name --type-col l2 \
  --stage-key furniture_geo --batch "$BATCH" 2>>"$LOG"
