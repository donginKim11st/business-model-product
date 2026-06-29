#!/bin/zsh
# 6단계 카탈로그 비정형 인사이트 — 백그라운드 '상시' 루프.
# catalog_insight_backfill.py(네이버 리뷰→LLM)는 재개안전(insight 있으면 skip)이라 반복 호출하면
# 새 카탈로그만 채운다. 네이버 일일 쿼터(25k)에 막히면 빈 결과가 쏟아지므로(=ok 0, empty 多)
# 그때는 길게 쉬고, 큐가 비면 더 길게 쉰다. --retry-empty 로 쿼터때 오염된 빈 insight 자가복구.
# 단일 인스턴스(락). 결과는 db/catalog_insight.log 에 누적. 멈추려면: pkill -f run_catalog_insight_loop
export PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY=${PY:-/usr/bin/python3}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
LOG="db/catalog_insight.log"

export MONGO_URI="${MONGO_URI:-mongodb://localhost:47017/?directConnection=true}"
export INSIGHTS_DB="${INSIGHTS_DB:-insights_demo}"

# 튜닝 파라미터(환경변수로 덮어쓰기 가능)
BATCH="${CI_BATCH:-1500}"          # 한 패스 처리 상한
WORKERS="${CI_WORKERS:-10}"       # 네이버 초당 제한(429) 완화 — 높이면 빈결과 늘고 --retry-empty 재작업 증가
SLEEP_DRAIN="${CI_SLEEP_DRAIN:-120}"     # 정상 진행 중 패스 간 짧은 휴식
SLEEP_IDLE="${CI_SLEEP_IDLE:-3600}"      # 큐 비었을 때(할 일 없음)
SLEEP_QUOTA="${CI_SLEEP_QUOTA:-10800}"   # 네이버 쿼터 소진 의심(ok 0, empty 多)

# 단일 인스턴스 락 — stale 내성(crash/SIGKILL 후 죽은 락이면 회수). KeepAlive 재기동이 막히지 않게.
LOCK=/tmp/catalog_insight_loop.lock
if [ -d "$LOCK" ]; then
  OLDPID="$(cat "$LOCK/pid" 2>/dev/null)"
  if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null; then
    echo "$(date '+%F %T') 이미 실행 중(pid $OLDPID) — 스킵" >> "$LOG"; exit 0
  fi
  rm -rf "$LOCK" 2>/dev/null   # 죽은 락 회수
fi
mkdir "$LOCK" 2>/dev/null || { echo "$(date '+%F %T') 락 경합 — 스킵" >> "$LOG"; exit 0; }
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK" 2>/dev/null' EXIT

# 키 로드(값 노출 안 함)
set -a
eval "$(grep -E '^export (NAVER_|OPENAI_)' run.sh)"
set +a

echo "===== $(date '+%F %T') 6단계 상시 루프 시작 (DB=$INSIGHTS_DB · batch=$BATCH · workers=$WORKERS) =====" >> "$LOG"
while true; do
  TMP="$(mktemp)"
  "$PY" db/catalog_insight_backfill.py --workers "$WORKERS" --retries 3 \
        --retry-empty --limit "$BATCH" 2>&1 | tee -a "$LOG" > "$TMP"
  # 요약 파싱: "완료 · 인사이트 {ok} · 빈것 {empty} · 오류 {err} · 쿼터미처리 {quota} ..."
  SUMMARY="$(grep -E '^완료 ·' "$TMP" | tail -1)"
  rm -f "$TMP"
  OK="$(echo "$SUMMARY"     | sed -nE 's/.*인사이트 ([0-9]+).*/\1/p')"
  EMPTY="$(echo "$SUMMARY"  | sed -nE 's/.*빈것 ([0-9]+).*/\1/p')"
  ERR="$(echo "$SUMMARY"    | sed -nE 's/.*오류 ([0-9]+).*/\1/p')"
  QUOTA="$(echo "$SUMMARY"  | sed -nE 's/.*쿼터미처리 ([0-9]+).*/\1/p')"
  OK="${OK:-0}"; EMPTY="${EMPTY:-0}"; ERR="${ERR:-0}"; QUOTA="${QUOTA:-0}"

  if [ "$QUOTA" -gt 30 ]; then
    echo "$(date '+%F %T') 네이버 쿼터 미처리 ${QUOTA}건 → ${SLEEP_QUOTA}s 대기(큐 보존, 다음 패스 재개)" >> "$LOG"; sleep "$SLEEP_QUOTA"
  elif [ "$OK" -eq 0 ] && [ "$EMPTY" -eq 0 ] && [ "$ERR" -eq 0 ] && [ "$QUOTA" -eq 0 ]; then
    echo "$(date '+%F %T') 큐 비었음(수렴) → ${SLEEP_IDLE}s 대기" >> "$LOG"; sleep "$SLEEP_IDLE"
  else
    sleep "$SLEEP_DRAIN"
  fi
done
