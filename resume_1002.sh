#!/bin/zsh
# 1002 배치 일일 재개 — cron에서 호출. 키 로드 후 run_batch.py를 이어서 실행.
# YouTube 쿼터 소진 시 자동 중단(미처리분은 다음날 재개). 결과는 batch_full.log에 누적.
export PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export ORA_LIB="/opt/homebrew/lib"   # oracledb thick 클라이언트 dylib 위치
PY=/usr/bin/python3                   # requests/oracledb/openai 설치된 인터프리터
cd /Users/a1101417/Workspace/business-model || exit 1

# 중복 실행 방지 락(이미 돌고 있으면 스킵). 쿼터 소진 시 즉시 종료라 무해.
LOCK=/tmp/insights1002.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date '+%F %T') 이미 실행 중 — 스킵"; exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# run.sh의 export 라인에서 API 키 로드(키 복제 안 함)
set -a
eval "$(grep -E '^export (NAVER_|OPENAI_|YOUTUBE_|BRAVE_)' run.sh)"
set +a

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 일일 재개 시작 ($(wc -l < insights_1002.jsonl)건 완료 상태) ====="
OUT=insights_1002.jsonl "$PY" run_batch.py
# 매 실행 후 HTML 자동 갱신(항상 최신 browse_1002.html 유지)
"$PY" make_browse_1002.py insights_1002.jsonl
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 종료 (누적 $(wc -l < insights_1002.jsonl)건, HTML 갱신) ====="
