#!/bin/zsh
# n8n /step/furniture_extract 용 — 재추출을 **비동기 킥**하고 즉시 상태 JSON 반환.
# (크롤이 수 시간이라 다른 step 처럼 동기 완주 불가 — 킥 후 진행은 로그/재호출로 확인.)
# 이미 실행 중이면 busy, 아니면 run_furniture_extract.sh 를 세션 분리로 기동.
# remaining 은 항상 0 — n8n 드레인 루프 불필요(주1회 단발).
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK=/tmp/furniture_extract.lock
LOG="$HERE/furniture_extract.log"
PY="${PY:-/usr/bin/python3}"

PID="$(cat "$LOCK/pid" 2>/dev/null)"
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  STATE="busy"
else
  nohup /bin/zsh "$HERE/run_furniture_extract.sh" >/dev/null 2>&1 &
  STATE="started"
fi

"$PY" - "$HERE" "$STATE" "$LOG" <<'PY'
import json, os, sys
here, state, log = sys.argv[1], sys.argv[2], sys.argv[3]
def n(p):
    p = os.path.join(here, "outputs", p)
    if not os.path.exists(p):
        return 0
    with open(p, encoding="utf-8-sig") as f:
        return max(0, sum(1 for _ in f) - 1)
last = ""
try:
    last = open(log, encoding="utf-8", errors="replace").read().splitlines()[-1]
except Exception:
    pass
print(json.dumps({
    "stage": "furniture_extract", "state": state,   # started | busy
    "rows_extracted": n("furniture_all_brands.csv"),
    "catalogs": n("catalogs_furniture.csv"), "variants": n("catalog_variants_furniture.csv"),
    "log_tail": last,
    "progress": {"furniture_extract": {"total": 1, "done": 0 if state == "busy" else 1,
                                       "remaining": 0}},
}, ensure_ascii=False))
PY
