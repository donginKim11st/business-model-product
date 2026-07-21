#!/bin/zsh
# 서버 배포용 자족 번들 생성 — sibling import 구조 보존. insight/ 에서 실행.
# 키·산출 데이터는 담지 않는다(.env 는 서버에서 작성).
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"          # insight/
OUT="${1:-$SRC/deploy/bundle}"
rm -rf "$OUT"; mkdir -p "$OUT/db"
# 코어 패키지(테스트 제외)
rsync -a --exclude tests --exclude __pycache__ "$SRC/insight_engine" "$OUT/"
# 루트 런타임 모듈
cp "$SRC/naver_review_geo.py" "$SRC/run_batch.py" "$OUT/"
# db/ 런타임 모듈(배치 오케스트레이터·적재)
for f in catalog_insight_backfill.py load_mongo.py claude_llm.py run_insight_batch_openai.py run_batch_insight.sh; do
  cp "$SRC/db/$f" "$OUT/db/" 2>/dev/null || true
done
cp deploy/requirements.txt deploy/.env.example deploy/insight-engine.service "$OUT/"
echo "번들 생성: $OUT"
echo "포함: insight_engine/ · naver_review_geo.py · run_batch.py · db/*(배치) · requirements·env·service"
echo "미포함(정상): run.sh 실키·산출 데이터·tests·playwright"
