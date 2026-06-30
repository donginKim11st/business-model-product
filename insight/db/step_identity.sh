#!/bin/zsh
# identity 합류 1배치(동기) — n8n 배치 드라이버용. 조인만 수행(크롤 없음 → 타임아웃 안전).
#   1) export_identity_seed : identity.status 부재 product → seed.csv
#   2) identity_seed_match  : 기존 identity 산출(all_brands.csv)과 매칭 + insight_uid 스탬프
#   3) identity_backfill    : uid 스탬프 CSV → product 합류 1배치(catalogs[].identity + products.identity)
# identity 추출기 크롤(extract_all)은 별도 프로세스. 이 step 은 이미 추출된 것을 join 만 한다.
# 사용: zsh db/step_identity.sh [LIMIT]   (기본 SP_ID_BATCH 또는 200)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE=identity-step
LOCK=/tmp/identity_backfill.lock
LOG="db/identity_pipeline.log"
source "$(dirname "$0")/_pipeline_common.sh"
acquire_step_lock identity

BATCH="${1:-${SP_ID_BATCH:-200}}"
SEED="${ID_SEED:-identity/seeds/seed.csv}"
EXTRACTED="${ID_EXTRACTED:-identity/outputs/all_brands.csv}"
UIDCSV="${ID_UIDCSV:-identity/outputs/all_brands_uid.csv}"
TMP="$(mktemp)"

"$PY" db/export_identity_seed.py --out "$SEED" >> "$LOG" 2>&1
if [ -f "$EXTRACTED" ]; then
  "$PY" db/identity_seed_match.py --seed "$SEED" --extracted "$EXTRACTED" \
        --out "$UIDCSV" --name-thresh "${ID_NAME_THRESH:-0.4}" >> "$LOG" 2>&1
  "$PY" db/identity_backfill.py --csv "$UIDCSV" --limit "$BATCH" 2>&1 | tee -a "$LOG" > "$TMP"
else
  echo "$(date '+%F %T') [identity step] 산출 CSV 없음($EXTRACTED) — identity 추출 먼저 필요" >> "$LOG"
  : > "$TMP"
fi
S="$(grep -E '^완료 ·' "$TMP" | tail -1)"
DONE="$(echo "$S"  | sed -nE 's/.*done=([0-9]+).*/\1/p')";  DONE="${DONE:-0}"
EMPTY="$(echo "$S" | sed -nE 's/.*empty=([0-9]+).*/\1/p')"; EMPTY="${EMPTY:-0}"
rm -f "$TMP"

PROG="$("$PY" db/pipeline_progress.py --stage identity 2>/dev/null)"
echo "{\"stage\":\"identity\",\"done\":${DONE},\"empty\":${EMPTY},\"progress\":${PROG:-null}}"
