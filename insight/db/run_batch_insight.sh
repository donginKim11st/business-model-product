#!/bin/zsh
# ─────────────────────────────────────────────────────────────────────────────
# 비정형 인사이트 OpenAI Batch API 러너 (submit / status / fetch)
#
# 3단계 비동기 파이프라인을 감싼다:
#   submit  네이버 크롤 → 요청 .jsonl → OpenAI Batch 제출 → manifest 기록
#   status  제출한 배치들의 진행 상태 조회(무료)
#   fetch   완료된 배치 회수 → 인사이트 조립 → Mongo 적재(멱등)
#
# 사용:
#   db/run_batch_insight.sh submit [SKU수]     # 기본 25000(네이버 일일쿼터 상한)
#   db/run_batch_insight.sh status
#   db/run_batch_insight.sh fetch
#
# 환경(선택):
#   INSIGHTS_DB   대상 DB (기본 insights_demo — 대량 카탈로그가 여기 있음)
#   INSIGHT_MODEL LLM 모델 (기본 gpt-4o-mini)
#   BATCH_RUNDIR  특정 run-dir 지정(status/fetch는 최근 run-dir 자동 사용)
# ─────────────────────────────────────────────────────────────────────────────
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"          # .../insight/db
ROOT="$(cd "$HERE/.." && pwd)"                  # .../insight
cd "$ROOT"

# API 키 로드(NAVER/OPENAI). run.sh 는 커밋 금지 실키 보유.
if [ -f run.sh ]; then
  set -a; eval "$(grep '^export ' run.sh)"; set +a
fi
export INSIGHTS_DB="${INSIGHTS_DB:-insights_demo}"
export INSIGHT_MODEL="${INSIGHT_MODEL:-gpt-4o-mini}"

CMD="${1:-}"
PY=python3
STATE="$HERE/.batch_current_rundir"            # 최근 run-dir 포인터

case "$CMD" in
  submit)
    LIMIT="${2:-25000}"
    STAMP="$(date +%Y%m%d_%H%M%S)"
    RUNDIR="${BATCH_RUNDIR:-db/insight_engine_batch/run_$STAMP}"
    LOG="db/batch_openai_submit_$STAMP.log"
    echo "$RUNDIR" > "$STATE"
    echo "[submit] DB=$INSIGHTS_DB · model=$INSIGHT_MODEL · limit=$LIMIT · run-dir=$RUNDIR"
    echo "[submit] 크롤은 네이버 쿼터에 묶여 오래 걸립니다 → 백그라운드 실행, 로그: $LOG"
    nohup $PY db/run_insight_batch_openai.py --submit --limit "$LIMIT" --yes \
          --run-dir "$RUNDIR" > "$LOG" 2>&1 &
    echo "[submit] PID=$!  · 진행: wc -l $RUNDIR/staging.jsonl  · 완료: $RUNDIR/manifest.json"
    ;;

  status)
    RUNDIR="${BATCH_RUNDIR:-$(cat "$STATE" 2>/dev/null)}"
    [ -n "$RUNDIR" ] || { echo "run-dir 없음 — 먼저 submit 하세요"; exit 1; }
    echo "[status] run-dir=$RUNDIR"
    $PY db/run_insight_batch_openai.py --status --run-dir "$RUNDIR"
    ;;

  fetch)
    RUNDIR="${BATCH_RUNDIR:-$(cat "$STATE" 2>/dev/null)}"
    [ -n "$RUNDIR" ] || { echo "run-dir 없음 — 먼저 submit 하세요"; exit 1; }
    echo "[fetch] DB=$INSIGHTS_DB · run-dir=$RUNDIR"
    $PY db/run_insight_batch_openai.py --fetch --run-dir "$RUNDIR"
    ;;

  *)
    echo "사용: db/run_batch_insight.sh {submit [SKU수] | status | fetch}"
    echo "  submit  네이버 크롤 → OpenAI Batch 제출(백그라운드)"
    echo "  status  배치 진행 상태 조회"
    echo "  fetch   완료 배치 회수 → Mongo 적재(멱등, 완료까지 반복 호출)"
    exit 1
    ;;
esac
