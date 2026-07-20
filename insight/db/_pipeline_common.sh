#!/bin/zsh
# 원천 파이프라인 공통 셋업 — 정형(run_structured_loop.sh)·비정형(run_unstructured_loop.sh)이 source.
#
# 호출 스크립트가 source 전에 반드시 정의해야 하는 변수:
#   ROOT   : insight 루트 절대경로
#   STAGE  : structured | unstructured (로그 헤더·표식용)
#   LOCK   : 단일 인스턴스 락 디렉토리(스테이지별로 달라야 동시 실행 가능)
#   LOG    : 로그 파일(ROOT 기준 상대경로 권장)
# source 후 acquire_lock 을 호출하면 락 획득(+ stale 회수, EXIT 시 자동 해제).
# 정형 루프는 추가로 seed_trees 를 호출해 기존 trees 산출물을 캐노니컬로 1회 시드한다.

export PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY=${PY:-/usr/bin/python3}
cd "$ROOT" || exit 1

export MONGO_URI="${MONGO_URI:-mongodb://localhost:47017/?directConnection=true}"
export INSIGHTS_DB="${INSIGHTS_DB:-insights_demo}"
export ORA_LIB="${ORA_LIB:-/opt/homebrew/lib}"
# build_all_bndl.py(=archive) + tree_core(=archive) + naver_review_geo(=root) import 경로
export PYTHONPATH="$ROOT:$ROOT/archive"
export NO_YT="${NO_YT:-1}"                # 빌드 시 유튜브 수집 생략(쿼터 절약·속도)

# 캐노니컬 산출물(빌드 체크포인트 = 적재 소스). 정형·비정형이 동일 파일을 참조.
TREES="${TREES:-trees_src.jsonl}"
BCAT="${BCAT:-bndl_category.jsonl}"

# 튜닝 파라미터(정형/비정형 공통 — 각 루프에서 쓰는 것만 참조)
BUILD_BATCH="${SP_BUILD_BATCH:-50}"       # 한 패스 새 BNDL 그룹 빌드 수(LLM 비쌈 → 보수적)
LOAD_LIMIT="${SP_LOAD_LIMIT:-100000}"     # 한 패스 Mongo 적재 신규 번들 상한(사실상 전부)
INS_BATCH="${SP_INS_BATCH:-1500}"         # 한 패스 SKU 인사이트 처리 상한
BUILD_WORKERS="${SP_BUILD_WORKERS:-8}"    # 빌드 병렬(네이버 429 완화)
INS_WORKERS="${SP_INS_WORKERS:-10}"       # SKU 인사이트 병렬
REG="${SP_REG:-1002}"                     # 등록유형(콤마구분 가능: "1002,801")
SLEEP_DRAIN="${SP_SLEEP_DRAIN:-120}"      # 정상 진행 중 패스 간 짧은 휴식
SLEEP_IDLE="${SP_SLEEP_IDLE:-3600}"       # 원천 소진(할 일 없음)
SLEEP_QUOTA="${SP_SLEEP_QUOTA:-10800}"    # 네이버 쿼터 소진 의심

# 키 로드 — NAVER/OPENAI/YOUTUBE. 값 노출 안 함.
# (YOUTUBE 키는 step_youtube.sh 의 youtube_backfill 가 필요 — 빌드 단계는 NO_YT=1 이라 안 씀.)
set -a
eval "$(grep -E '^export (NAVER_|OPENAI_|YOUTUBE_)' run.sh)"
# Oracle 자격증명(~/.ora_creds: ORA_USER/ORA_PW) → 정형 단계(CATEGORY/SINGLES)가 env 로 읽음.
eval "$(grep -E '^ORA_(USER|PW)=' "$HOME/.ora_creds" 2>/dev/null | sed 's/^/export /')"
set +a

# 단일 인스턴스 락 — stale 내성(crash/SIGKILL 후 죽은 락이면 회수).
acquire_lock() {
  if [ -d "$LOCK" ]; then
    OLDPID="$(cat "$LOCK/pid" 2>/dev/null)"
    if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null; then
      echo "$(date '+%F %T') [$STAGE] 이미 실행 중(pid $OLDPID) — 스킵" >> "$LOG"; exit 0
    fi
    rm -rf "$LOCK" 2>/dev/null
  fi
  mkdir "$LOCK" 2>/dev/null || { echo "$(date '+%F %T') [$STAGE] 락 경합 — 스킵" >> "$LOG"; exit 0; }
  echo $$ > "$LOCK/pid"
  trap 'rm -rf "$LOCK" 2>/dev/null' EXIT
}

# step 락 — n8n 배치 드라이버용. 이미 처리 중이면 busy JSON(+진행률)만 내고 종료(중복 step 방지).
# 호출 전 LOCK 변수 지정 필요. 인자: progress --stage 값(structured|unstructured|youtube).
acquire_step_lock() {
  local pstage="$1"
  if ! mkdir "$LOCK" 2>/dev/null; then
    local op="$(cat "$LOCK/pid" 2>/dev/null)"
    if [ -n "$op" ] && kill -0 "$op" 2>/dev/null; then
      local prog="$("$PY" db/pipeline_progress.py --stage "$pstage" 2>/dev/null)"
      echo "{\"stage\":\"$pstage\",\"busy\":true,\"progress\":${prog:-null}}"
      exit 0
    fi
    rm -rf "$LOCK" 2>/dev/null
    mkdir "$LOCK" 2>/dev/null || { echo "{\"stage\":\"$pstage\",\"busy\":true,\"error\":\"lock-contend\"}"; exit 0; }
  fi
  echo $$ > "$LOCK/pid"
  trap 'rm -rf "$LOCK" 2>/dev/null' EXIT
}

# 1회 시드: 기존 trees 산출물(food + 전카테고리)을 캐노니컬로 병합(번들 dedup).
# build_all_bndl 는 OUT 에 이미 있는 그룹을 skip → 과거 LLM 작업 재사용(비용↓).
seed_trees() {
  [ -f "$TREES" ] && return 0
  echo "$(date '+%F %T') [시드] 기존 trees 산출물 → $TREES 병합(dedup)" >> "$LOG"
  "$PY" - "$TREES" trees_food.jsonl archive/trees_bndl.jsonl archive/trees_pkg.jsonl <<'PYSEED' 2>>"$LOG"
import sys, json, os
out, srcs = sys.argv[1], sys.argv[2:]
seen, n = set(), 0
with open(out, "w", encoding="utf-8") as w:
    for src in srcs:
        if not os.path.exists(src):
            continue
        for line in open(src, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                g = json.loads(line).get("bndl_grp")
            except Exception:
                continue
            if g is None or g in seen:
                continue
            seen.add(g); w.write(line + "\n"); n += 1
print(f"[시드] {n}개 번들 병합 → {out}")
PYSEED
}
