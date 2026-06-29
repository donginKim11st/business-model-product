#!/bin/zsh
# YouTube 디커플 backfill — 매일 1회(cron/launchd에서 호출). 일일 쿼터 예산만큼만 채우고 종료.
# product.youtube(status=pending) 큐를 우선순위대로 → 출처 + 유튜브 전용 인사이트 누적.
# 쿼터 소진/완료 시 우아하게 중단, 다음날 자동 재개. 결과는 db/youtube_backfill.log 에 누적.
export PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY=${PY:-/usr/bin/python3}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
export MONGO_URI="${MONGO_URI:-mongodb://localhost:47017/?directConnection=true}"
export INSIGHTS_DB="${INSIGHTS_DB:-insights_demo}"   # 실데이터 위치(운영 insights는 비어있음). 필요시 호출부에서 덮어쓰기.

# 중복 실행 방지 락
LOCK=/tmp/youtube_backfill.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date '+%F %T') 이미 실행 중 — 스킵" >> db/youtube_backfill.log; exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# run.sh 의 export 라인에서 API 키 로드(키 복제 안 함) — resume_1002.sh 와 동일 관례
set -a
eval "$(grep -E '^export (NAVER_|OPENAI_|YOUTUBE_)' run.sh)"
set +a

echo "===== $(date '+%F %T') 일일 YouTube backfill 시작 =====" >> db/youtube_backfill.log
"$PY" db/youtube_backfill.py \
  --daily-units "${YT_DAILY_UNITS:-9000}" \
  --n-videos "${YT_N_VIDEOS:-3}" --n-comments "${YT_N_COMMENTS:-50}" \
  >> db/youtube_backfill.log 2>&1
echo "===== $(date '+%F %T') 종료 =====" >> db/youtube_backfill.log
