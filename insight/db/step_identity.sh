#!/bin/zsh
# identity(정형) 합류 1배치(동기) — n8n 배치 드라이버용.
# 주 경로: Oracle PD_CTLG 정형 팩트를 ctlg_no 정확 조인(비정형이 쓰는 그 Oracle 공용).
#   oracle_structured_backfill: catalogs[].identity(name/brand/model/opt/색/사이즈/barcode/용량) + products.identity.
# 퍼지 매칭/게이트 불필요(ctlg_no 1:1). 고시(소재/제조국)·가격은 별도(brand-mall/Naver) 보충.
# 사용: zsh db/step_identity.sh [LIMIT]   (기본 SP_ID_BATCH 또는 200)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE=identity-step
LOCK=/tmp/identity_backfill.lock
LOG="db/identity_pipeline.log"
source "$(dirname "$0")/_pipeline_common.sh"
acquire_step_lock identity

BATCH="${1:-${SP_ID_BATCH:-200}}"
TMP="$(mktemp)"

"$PY" db/oracle_structured_backfill.py --limit "$BATCH" 2>&1 | tee -a "$LOG" > "$TMP"
S="$(grep -E '^완료 ·' "$TMP" | tail -1)"
DONE="$(echo "$S"  | sed -nE 's/.*done=([0-9]+).*/\1/p')";  DONE="${DONE:-0}"
EMPTY="$(echo "$S" | sed -nE 's/.*empty=([0-9]+).*/\1/p')"; EMPTY="${EMPTY:-0}"
rm -f "$TMP"

PROG="$("$PY" db/pipeline_progress.py --stage identity 2>/dev/null)"
echo "{\"stage\":\"identity\",\"done\":${DONE},\"empty\":${EMPTY},\"progress\":${PROG:-null}}"
