#!/bin/zsh
# 리포트·사이트 자동 리빌드 — 매일 1회(launchd). API 없이 Mongo만 읽음(안전·재실행 무해).
# 순서: 카테고리 랭킹(글로벌 재계산) → 리포트 5종 → 정적 사이트(nav 주입). 7단계 유튜브 일배치(09:00) 뒤 10:00 권장.
# 결과 로그 db/rebuild.log. 단일 인스턴스(stale 내성 락). 수동 실행도 동일.
export PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY=${PY:-/usr/bin/python3}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
LOG="db/rebuild.log"
export MONGO_URI="${MONGO_URI:-mongodb://localhost:47017/?directConnection=true}"
export INSIGHTS_DB="${INSIGHTS_DB:-insights_demo}"

# 단일 인스턴스 락(stale 내성)
LOCK=/tmp/insights_rebuild.lock
if [ -d "$LOCK" ]; then
  OLDPID="$(cat "$LOCK/pid" 2>/dev/null)"
  if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null; then
    echo "$(date '+%F %T') 이미 실행 중(pid $OLDPID) — 스킵" >> "$LOG"; exit 0
  fi
  rm -rf "$LOCK" 2>/dev/null
fi
mkdir "$LOCK" 2>/dev/null || { echo "$(date '+%F %T') 락 경합 — 스킵" >> "$LOG"; exit 0; }
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK" 2>/dev/null' EXIT

run() {   # 단계 실행(실패해도 다음 단계 진행 — 이전 산출물 유지)
  echo "  · $*" >> "$LOG"
  "$PY" "$@" >> "$LOG" 2>&1 || echo "    ✗ 실패: $*" >> "$LOG"
}

echo "===== $(date '+%F %T') 리빌드 시작 (DB=$INSIGHTS_DB) =====" >> "$LOG"
run db/category_rank.py
run db/exec_report.py       --html data/exec_report.html
run db/report_site.py       --html data/report.html
run db/bundle_view.py       --html data/package_explorer.html
run db/seller_dashboard.py  --html data/seller_dashboard.html
run db/consumer_guide.py    --html data/consumer_guide.html
run db/site_build.py
echo "===== $(date '+%F %T') 리빌드 종료 =====" >> "$LOG"
