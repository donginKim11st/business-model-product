#!/bin/zsh
# 비정형 인사이트 원격 동기화 루프 — 1시간마다 push_unstructured_remote.py 재실행.
#
# 로컬 insights_demo 에 새로 쌓인 catalogs[].insight 를 10xtf.aiCatalogUnstructuredAttribute 로
# upsert(_id=ctlg_no) — 재실행 안전이라 단순 전량 재밀어넣기로 준실시간 동기화를 대신한다.
# REMOTE_URI 는 env 로 직접 주거나(~/.10xtf_creds 가 있으면 폴백 로드). 크리덴셜은 디스크에 저장하지 않는다.
#   REMOTE_URI='mongodb://...' zsh db/run_remote_push_loop.sh
# 멈추려면: pkill -f run_remote_push_loop
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE=remote-push
LOCK=/tmp/remote_push_loop.lock
LOG="db/remote_push.log"
source "$(dirname "$0")/_pipeline_common.sh"
acquire_lock

[ -z "$REMOTE_URI" ] && [ -f "$HOME/.10xtf_creds" ] && source "$HOME/.10xtf_creds"
[ -z "$REMOTE_URI" ] && { echo "$(date '+%F %T') [원격push] REMOTE_URI 미설정 — 종료" >> "$LOG"; exit 1; }
export REMOTE_URI
INTERVAL="${SP_PUSH_INTERVAL:-3600}"

echo "===== $(date '+%F %T') [원격push] 루프 시작 (DB=$INSIGHTS_DB → 10xtf.aiCatalogUnstructuredAttribute · ${INTERVAL}s 주기) =====" >> "$LOG"

while true; do
  "$PY" db/push_unstructured_remote.py 2>&1 | grep -Ev "NotOpenSSLWarning|warnings.warn" | \
    sed "s/^/$(date '+%F %T') [원격push] /" >> "$LOG"
  sleep "$INTERVAL"
done
