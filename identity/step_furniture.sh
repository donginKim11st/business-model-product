#!/bin/zsh
# 가구 카탈로그 재빌드 1회(동기) — n8n /step/furniture 용.
# furniture_catalog.py all (decompose→group→verify) — 기추출 furniture_geo_mapped.jsonl 기반,
# 크롤 없음·무료·멱등. title_geo 는 _catalog_canonical_furniture.json(LLM 스토어) 반영.
# stdout 마지막 줄 = 진행률 JSON. verbose 는 catalog_pipeline.log.
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HERE/catalog_pipeline.log"
PY="${PY:-/usr/bin/python3}"

echo "===== $(date '+%F %T') [furniture] step 시작 =====" >> "$LOG"
"$PY" "$HERE/furniture_catalog.py" all >> "$LOG" 2>&1
RC=$?
echo "$(date '+%F %T') [furniture] rc=$RC" >> "$LOG"

"$PY" - "$HERE/outputs/catalogs_furniture.csv" "$HERE/outputs/catalog_variants_furniture.csv" "$RC" <<'PY'
import json, os, sys
cat, var, rc = sys.argv[1], sys.argv[2], int(sys.argv[3])
def n(p):
    if not os.path.exists(p):
        return 0
    with open(p, encoding="utf-8-sig") as f:
        return max(0, sum(1 for _ in f) - 1)
c, v = n(cat), n(var)
print(json.dumps({"stage": "furniture", "rc": rc, "catalogs": c, "variants": v,
                  "progress": {"furniture": {"total": c, "done": c, "remaining": 0}}},
                 ensure_ascii=False))
PY
