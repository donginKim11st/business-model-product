#!/bin/zsh
# 식품 카탈로그 크로스몰 가격(offers) + 일자별 가격추이 스냅샷 — 매일 1회(cron/launchd).
# 매일 --refresh 로 현재가를 다시 받아 price_history 에 '그 날짜' 스냅샷을 1행씩 쌓는다 → 시계열(추이).
# 네이버 쇼핑은 과거 가격을 안 주므로 이렇게 매일 스냅샷을 누적해야 추이가 생긴다(첫날 1점, 2주면 2주치).
# 네이버 쇼핑 일일 한도(약 25,000 호출) 안에서 FOOD_PRICE_LIMIT 로 패키지 수를 제한할 수 있다.
# 결과는 db/food_price_backfill.log 에 누적. NAVER 키만 필요(LLM 불필요).
export PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY=${PY:-/usr/bin/python3}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
export MONGO_URI="${MONGO_URI:-mongodb://localhost:47017/?directConnection=true}"

# 중복 실행 방지 락
LOCK=/tmp/food_price_backfill.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date '+%F %T') 이미 실행 중 — 스킵" >> db/food_price_backfill.log; exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# run.sh 의 export 라인에서 네이버+OPENAI 키 로드(키 복제 안 함) — resume_1002.sh 관례 동일
# OPENAI 는 --llm-verify(의심구간 동일/다름 의미판정) 용. 캐시(offer_match_cache)로 신규 쌍만 호출.
set -a
eval "$(grep -E '^export (NAVER_|OPENAI)' run.sh)"
set +a

# 인자 조립: 매일 갱신(--refresh)이 추이의 핵심. 기본은 대표 인사이트 보유(=화면에 노출되는) 패키지만.
ARGS=(--refresh --display "${FOOD_PRICE_DISPLAY:-30}" --per-pkg-cap "${FOOD_PRICE_CAP:-8}")
[ "${FOOD_PRICE_ALL:-}" = "1" ] || ARGS+=(--with-rep-only)     # FOOD_PRICE_ALL=1 이면 전체 카탈로그
[ -n "${FOOD_PRICE_LIMIT:-}" ] && ARGS+=(--limit "$FOOD_PRICE_LIMIT")   # 일일 호출 상한 보호용
# LLM 의미게이트(변형·묶음·용량차 제거) — 기본 ON. 끄려면 FOOD_PRICE_NO_LLM=1. (OPENAI 키 없으면 자동 비활성 권장)
[ "${FOOD_PRICE_NO_LLM:-}" = "1" ] || [ -z "${OPENAI_API_KEY:-}" ] || ARGS+=(--llm-verify)

echo "===== $(date '+%F %T') 식품 가격/추이 backfill 시작 (${ARGS[*]}) =====" >> db/food_price_backfill.log
"$PY" db/food_price_backfill.py "${ARGS[@]}" >> db/food_price_backfill.log 2>&1
echo "===== $(date '+%F %T') 종료 =====" >> db/food_price_backfill.log
