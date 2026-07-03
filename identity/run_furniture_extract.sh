#!/bin/zsh
# 가구 전량 재추출(크롤) 1회 — n8n 주1회 /step/furniture_extract 가 백그라운드 기동.
# run_furniture_pipeline.py --force: 추출(9몰 크롤)→병합→GEO매핑→QA게이트→카탈로그(골든 verify)→Mongo.
# 수 시간 소요라 동기 step 배치(STEP_TIMEOUT=900s) 불가 → 단발 백그라운드 + 단일 인스턴스 락.
# 신규 카탈로그 키의 canonical 은 n8n furniture_geo 10분 드레인이 증분 처리(여기서 LLM 호출 없음).
# 멈추려면: pkill -f run_furniture_pipeline
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK=/tmp/furniture_extract.lock
LOG="$HERE/furniture_extract.log"
PY="${PY:-/usr/bin/python3}"
export PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# 단일 인스턴스 락 — stale 내성(_pipeline_common.sh acquire_lock 과 동일 패턴)
if [ -d "$LOCK" ]; then
  OLDPID="$(cat "$LOCK/pid" 2>/dev/null)"
  if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null; then
    echo "$(date '+%F %T') [furniture_extract] 이미 실행 중(pid $OLDPID) — 스킵" >> "$LOG"
    exit 0
  fi
  rm -rf "$LOCK" 2>/dev/null
fi
mkdir "$LOCK" 2>/dev/null || { echo "$(date '+%F %T') [furniture_extract] 락 경합 — 스킵" >> "$LOG"; exit 0; }
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK" 2>/dev/null' EXIT

echo "===== $(date '+%F %T') [furniture_extract] 전량 재추출 시작 (--force --parallel 3) =====" >> "$LOG"
"$PY" "$HERE/run_furniture_pipeline.py" --force --parallel 3 >> "$LOG" 2>&1
RC=$?
echo "$(date '+%F %T') [furniture_extract] 종료 rc=$RC" >> "$LOG"
exit $RC
