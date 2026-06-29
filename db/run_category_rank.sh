#!/bin/zsh
# 카테고리 속성 랭킹 + 번들 대표 materialize — 주 1회 배치(cron/launchd에서 호출).
# 추출 파이프라인과 독립(이미 적재된 Mongo 만 읽고 representative/category_attribute_rank 갱신).
# 결과는 db/category_rank.log 에 누적. API 키 불필요(로컬 Mongo 만).
export PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY=${PY:-/usr/bin/python3}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"          # db/ 의 상위 = repo 루트
cd "$ROOT" || exit 1
export MONGO_URI="${MONGO_URI:-mongodb://localhost:47017/?directConnection=true}"

# 중복 실행 방지 락
LOCK=/tmp/category_rank.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date '+%F %T') 이미 실행 중 — 스킵" >> db/category_rank.log; exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

echo "===== $(date '+%F %T') 주간 카테고리 랭킹 시작 =====" >> db/category_rank.log
"$PY" db/category_rank.py \
  --top-n "${RANK_TOP_N:-5}" --min-coverage "${RANK_MIN_COV:-0.2}" \
  --per-dim "${RANK_PER_DIM:-3}" --coverage-by "${RANK_COVERAGE_BY:-bundle}" \
  --rank-by "${RANK_BY:-hybrid}" --min-support "${RANK_MIN_SUPPORT:-2}" \
  >> db/category_rank.log 2>&1
echo "===== $(date '+%F %T') 종료 =====" >> db/category_rank.log
