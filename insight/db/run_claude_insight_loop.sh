#!/bin/zsh
# claude(Max 구독) 비정형 인사이트 **야간** 루프 — 22시~08시에만 가동. API 비용 0원.
#
# 기존 run_unstructured_loop.sh 와 같은 큐(catalog_insight_backfill)를 처리하되 INSIGHT_LLM=claude 로
# claude -p 헤드리스(구독)를 쓴다. 같은 큐를 나눠 먹으면 동시 중복 처리가 생길 수 있으므로
# 이 루프를 켤 때는 API 루프(run_unstructured_loop)를 꺼 둔다.
#
# 실측 처리량(haiku): 워커 5 기준 ⚠️시간당 ~65건 → 야간 10시간 ~650건.
# 구독 사용량 한도(5시간 윈도우) 도달 시 backfill 이 '쿼터미처리'로 큐를 보존 → 여기서 길게 쉼.
# 멈추려면: pkill -f run_claude_insight_loop
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE=claude-insight
LOCK=/tmp/claude_insight_loop.lock
LOG="db/claude_insight_pipeline.log"
source "$(dirname "$0")/_pipeline_common.sh"
acquire_lock

C_WORKERS="${SP_CLAUDE_WORKERS:-5}"
C_BATCH="${SP_CLAUDE_BATCH:-50}"     # 패스당 처리 상한 — 워커 5 기준 ~45분(야간 게이트 재확인 주기)
NIGHT_FROM="${SP_CLAUDE_FROM:-22}"   # 가동 시작(시)
NIGHT_TO="${SP_CLAUDE_TO:-8}"        # 가동 종료(시)

in_night() {
  local h=$((10#$(date +%H)))
  [ "$h" -ge "$NIGHT_FROM" ] || [ "$h" -lt "$NIGHT_TO" ]
}

echo "===== $(date '+%F %T') [claude야간] 루프 시작 (DB=$INSIGHTS_DB · batch=$C_BATCH · workers=$C_WORKERS · ${NIGHT_FROM}시~${NIGHT_TO}시) =====" >> "$LOG"

while true; do
  if ! in_night; then
    sleep 600; continue
  fi
  TMP="$(mktemp)"

  INSIGHT_LLM=claude "$PY" db/catalog_insight_backfill.py --workers "$C_WORKERS" --retries 1 \
        --retry-empty --limit "$C_BATCH" 2>&1 | tee -a "$LOG" > "$TMP"
  SUMMARY="$(grep -E '^완료 ·' "$TMP" | tail -1)"
  OK="$(echo "$SUMMARY"    | sed -nE 's/.*인사이트 ([0-9]+).*/\1/p')"
  EMPTY="$(echo "$SUMMARY" | sed -nE 's/.*빈것 ([0-9]+).*/\1/p')"
  ERR="$(echo "$SUMMARY"   | sed -nE 's/.*오류 ([0-9]+).*/\1/p')"
  QUOTA="$(echo "$SUMMARY" | sed -nE 's/.*쿼터미처리 ([0-9]+).*/\1/p')"
  OK="${OK:-0}"; EMPTY="${EMPTY:-0}"; ERR="${ERR:-0}"; QUOTA="${QUOTA:-0}"
  rm -f "$TMP"

  echo "$(date '+%F %T') [claude야간요약] SKU인사이트 ${OK}(빈 ${EMPTY}/오류 ${ERR}/쿼터 ${QUOTA})" >> "$LOG"

  # 휴식 정책 — 쿼터(구독 5시간 한도 또는 네이버 429) 감지 시 1시간, 수렴 시 길게.
  if [ "$QUOTA" -gt "$C_WORKERS" ]; then
    echo "$(date '+%F %T') [claude야간] 쿼터/구독한도 미처리 ${QUOTA}건 → 3600s 대기" >> "$LOG"; sleep 3600
  elif [ "$OK" -eq 0 ] && [ "$EMPTY" -eq 0 ] && [ "$ERR" -eq 0 ]; then
    echo "$(date '+%F %T') [claude야간] 처리 대상 없음(수렴) → ${SLEEP_IDLE}s 대기" >> "$LOG"; sleep "$SLEEP_IDLE"
  else
    sleep 30
  fi
done
