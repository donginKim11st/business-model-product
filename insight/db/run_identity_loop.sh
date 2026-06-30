#!/bin/zsh
# identity(정형 합류) 상시 루프 — insight product 에 identity 정형 팩트를 uid 로 합류.
#
#   1) export_identity_seed : identity.status 부재 product → 씨앗
#   2) identity_seed_match  : 기존 identity 산출과 매칭 + insight_uid 스탬프(강키 우선 + 이름 폴백)
#   3) identity_backfill    : uid 스탬프 CSV → product 합류(재개안전, status enum)
#
# 조인만 한다(크롤 없음). identity 추출기(extract_all)는 별도 프로세스가 all_brands.csv 를 채운다 —
# 이 루프는 그걸 insight 우주에 합류시킬 뿐(추출기 본체 불변). category-agnostic: 모든 카테고리 수용.
# 재개·멱등. 할 일 없으면 길게 쉰다. 멈추려면: pkill -f run_identity_loop
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE=identity
LOCK=/tmp/source_identity_loop.lock
LOG="db/identity_pipeline.log"
source "$(dirname "$0")/_pipeline_common.sh"
acquire_lock

SEED="${ID_SEED:-identity/seeds/seed.csv}"
EXTRACTED="${ID_EXTRACTED:-identity/outputs/all_brands.csv}"
UIDCSV="${ID_UIDCSV:-identity/outputs/all_brands_uid.csv}"
ID_BATCH="${SP_ID_BATCH:-1000}"

echo "===== $(date '+%F %T') [identity] 합류 루프 시작 (DB=$INSIGHTS_DB · batch=$ID_BATCH) =====" >> "$LOG"

while true; do
  TMP="$(mktemp)"
  "$PY" db/export_identity_seed.py --out "$SEED" >> "$LOG" 2>&1

  if [ -f "$EXTRACTED" ]; then
    "$PY" db/identity_seed_match.py --seed "$SEED" --extracted "$EXTRACTED" \
          --out "$UIDCSV" --name-thresh "${ID_NAME_THRESH:-0.4}" \
          --thresh-map "${ID_THRESH_MAP:-db/identity_name_thresh.json}" >> "$LOG" 2>&1
    "$PY" db/identity_backfill.py --csv "$UIDCSV" --limit "$ID_BATCH" 2>&1 | tee -a "$LOG" > "$TMP"
  else
    echo "$(date '+%F %T') [identity] 산출 CSV 없음($EXTRACTED) — identity 추출 대기" >> "$LOG"
    : > "$TMP"
  fi
  SUMMARY="$(grep -E '^완료 ·' "$TMP" | tail -1)"
  DONE="$(echo "$SUMMARY"  | sed -nE 's/.*done=([0-9]+).*/\1/p')";  DONE="${DONE:-0}"
  EMPTY="$(echo "$SUMMARY" | sed -nE 's/.*empty=([0-9]+).*/\1/p')"; EMPTY="${EMPTY:-0}"
  rm -f "$TMP"

  echo "$(date '+%F %T') [identity요약] 합류 done=${DONE} empty=${EMPTY}" >> "$LOG"

  if [ "$DONE" -eq 0 ] && [ "$EMPTY" -eq 0 ]; then
    echo "$(date '+%F %T') [identity] 합류 대상 없음 → ${SLEEP_IDLE}s 대기" >> "$LOG"; sleep "$SLEEP_IDLE"
  else
    sleep "$SLEEP_DRAIN"
  fi
done
