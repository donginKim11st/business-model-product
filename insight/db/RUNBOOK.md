# 실행 런북 — 상품 인사이트 인텔리전스 파이프라인

직접 처음부터 끝까지 돌리는 순서. 데모 DB(`insights_demo`)는 운영(`insights`)과 분리돼 안전.
모든 수치는 직접 수집·실측이며 합성 없음(가격 추이만 데모 시드는 별도, 운영 미사용).

## 0. 사전 준비 (매 세션 1회)
```bash
cd /Users/a1101417/Work/business-model
# API 키 로드(값 노출 안 함) — NAVER/OPENAI/YOUTUBE
set -a; eval "$(grep '^export ' run.sh)"; set +a
# 공통 환경 — 데모는 insights_demo, 운영은 INSIGHTS_DB 빼면 기본 insights
export MONGO_URI="mongodb://localhost:47017/?directConnection=true"
export INSIGHTS_DB=insights_demo
# (MongoDB가 localhost:47017 에 떠 있어야 함)
```

## A. 데모 전체 파이프라인 (순서대로)
```bash
# 1) 적재: trees_food(실제 식품 taxonomy 2.7k) → insights_demo (패키지+카탈로그+데모 카테고리)   [~1분]
python3 db/demo_load_trees.py --limit 800

# 2) 카탈로그 크로스몰 가격(offers) + 가격사다리   [~10분, 네이버 ~2.3k콜]
#    이름매칭(카탈로그명↔리스팅명 bigram recall≥0.4 — 다른 브랜드/맛 새는 것 차단) +
#    개수매칭(1개가 x2개 카탈로그에 새는 것 차단 — count 필드 비면 disp 명의 'x2개·*3' 표기로 보강) +
#    이상치/중고(productType 4~6) 정정 내장
python3 db/food_price_backfill.py --per-pkg-cap 50 --max-calls 24000
#  ▶ 이름매칭(문자, 무비용): 네이버 쇼핑 검색이 fuzzy 라 같은 규격의 다른 상품(스파클 검색에 미라클, 사골미역국에
#    해물맛)이 섞여 최저가를 오염. 카탈로그명 문자 bigram recall<0.4 리스팅 제거(전부 미달이면 가격 비움=틀린 가격보다
#    옳음). 임계값은 LLM 골드셋(232건) 보정값. 끄기 --no-match-name · 조절 --name-thresh(env PRICE_NAME_THRESH).
#  ▶ LLM 의미게이트(--llm-verify, 권장): 문자유사도가 못 가르는 잔존 — 같은 브랜드 다른 변형(국간장↔진간장·딥스
#    골드↔에코그린·발아현미↔현미)·묶음(살코기5+고추5·골라담기)·용량차(500g↔250g) — 을 의심구간만 LLM 으로
#    동일/다름 판정해 제거. 의심 = 이름recall<--llm-hi(0.7) OR 묶음신호 OR 용량불일치. (카탈로그명,상품명) verdict
#    를 offer_match_cache 에 캐시 → 일배치 --refresh 는 신규 쌍만 호출(비용 최소). OPENAI 키 필요. 모델 gpt-4o-mini
#    (env PRICE_MATCH_MODEL). cron 래퍼(run_food_price_backfill.sh)는 기본 ON(끄기 FOOD_PRICE_NO_LLM=1).
#    한계: 연결어 없는 이중상품 나열(틈새라면…꼬꼬면)은 드물게 잔존.
#  ▶ 기존 데이터 무비용 정정:  python3 db/food_price_backfill.py --reclean [--llm-verify]   (네이버 0회)

# 3) 카테고리 실제화: offers의 네이버 카테고리(cat2 최빈) → category_l1 + 변형 전파   [~10초]
python3 db/realize_category.py

# 4) 카테고리 속성 랭킹 + 번들 대표 인사이트 materialize   [~20초]
python3 db/category_rank.py            # 기본 hybrid · 강점 고정 · 소수카테고리 가드

# 5) 언급량(buzz): 패키지 풀네임으로 네이버 블로그/쇼핑 total 실측   [~5분]
python3 db/buzz_backfill.py --refresh --yt-max 0      # 유튜브 카운트는 쿼터 아껴 끔(0)

# 6) 카탈로그(SKU)별 비정형 인사이트 — 네이버 리뷰→LLM, 병렬   [~40분, 워커 32]
python3 db/catalog_insight_backfill.py --workers 32 --retries 3

# 7) 유튜브 인사이트 — 쿼터 한도까지(일 ~90 패키지). 쿼터 리셋=태평양 자정   [~20분]
python3 db/youtube_backfill.py --daily-units 9000

# 8) 리포트 생성 (단일 HTML, 외부 의존 0)
python3 db/exec_report.py --html data/exec_report.html
```
> 2~7은 재개 안전(이미 된 건 skip). 중간에 끊겨도 같은 명령 재실행하면 이어감.
> `--dry-run` 지원: food_price_backfill / buzz_backfill / catalog_insight_backfill / youtube_backfill — API 없이 대상만 확인.

## B. 리포트만 다시 만들기 (데이터 그대로, 표시만 갱신)
```bash
python3 db/exec_report.py --html data/exec_report.html              # 경영진/투자자용(라이트, 카탈로그 모달)
python3 db/report_site.py --html data/report.html                   # 차트 대시보드(다크)
python3 db/bundle_view.py --html data/package_explorer.html         # 패키지 탐색기(전체 2,713·검색/카테고리칩·카드 펼침 lazy)
#   기본=전체 패키지(미처리 포함). --insight-only / --priced-only 로 좁히기. 상세는 <template>로 펼칠 때만 렌더(경량).
#   ▶ 결합: 카드 펼치면 인사이트(좌)+카탈로그/가격(우) 아래에 [소비자 정직가이드(주요몰최저가·솔직단점)] + [셀러
#     인텔리전스(갭·약점·가격경쟁력)] 스택. consumer_guide.gather/seller_dashboard.gather 데이터를 uid로 매핑해 재사용
#     (cg 엔트리에 uid 추가됨, 리렌더는 bundle_view 다크테마 cg-/sl- 네임스페이스). 카테고리 셀러보드는 site nav 탭으로.
python3 db/seller_dashboard.py --html data/seller_dashboard.html    # 셀러: 몰별 가격경쟁력+약점·갭+다중제품 비교
python3 db/consumer_guide.py --html data/consumer_guide.html        # 소비자 정직 가이드(주요몰 최저가·왜 사야/말아야·근거)
```
> 셀러/가이드의 '주요 몰' 판정은 consumer_guide.py 의 MAJOR_KEYS(화이트리스트). 새 대형몰 누락 시 여기에 추가.

## B-2. 정적 사이트로 묶기 (검색차단·노출용)
```bash
python3 db/site_build.py            # data/ 4개 HTML → site/ (공통 nav 주입 + README + robots noindex)
#   index.html=exec · dashboard.html=report · seller.html=셀러 · guide.html=가이드
# 외부 공개(사내 BI라 푸시는 직접):  ! git -C site add -A && git -C site commit -m "..." && git -C site push
```

## C. 수치 직접 검증 (리포트 숫자가 DB와 일치하는지)
```bash
python3 - <<'PY'
import os
from pymongo import MongoClient
db=MongoClient(os.environ["MONGO_URI"])[os.environ.get("INSIGHTS_DB","insights")]
P={"type":"package"}
print("패키지:", db.products.count_documents(P))
print("대표(비정형) 보유:", db.products.count_documents({**P,"representative.dims.0":{"$exists":True}}))
print("언급량 보유:", db.products.count_documents({**P,"buzz.naver_blog":{"$exists":True}}))
print("유튜브 done:", db.products.count_documents({**P,"youtube.status":"done"}))
print("offers:", db.offers.count_documents({}))
agg=list(db.products.aggregate([{"$match":P},{"$project":{
 "ci":{"$size":{"$filter":{"input":{"$ifNull":["$catalogs",[]]},"as":"c","cond":{"$gt":[{"$size":{"$ifNull":["$$c.insight.dims",[]]}},0]}}}},
 "pc":{"$size":{"$filter":{"input":{"$ifNull":["$catalogs",[]]},"as":"c","cond":{"$ne":[{"$ifNull":["$$c.price_summary.min",None]},None]}}}},
 "nc":{"$size":{"$filter":{"input":{"$ifNull":["$catalogs",[]]},"as":"c","cond":{"$ne":["$$c.ctlg_no",None]}}}}}},
 {"$group":{"_id":None,"ci":{"$sum":"$ci"},"pc":{"$sum":"$pc"},"nc":{"$sum":"$nc"}}}]))[0]
print(f"카탈로그 총 {agg['nc']} · 가격 {agg['pc']} · SKU인사이트 {agg['ci']}")
PY
```

## D. 일일 배치 (운영 누적 — cron)
```cron
0 2 * * *  /Users/a1101417/Work/business-model/db/run_food_price_backfill.sh   # 가격+추이 스냅샷
0 3 * * *  /Users/a1101417/Work/business-model/db/run_youtube_backfill.sh       # 유튜브 인사이트
0 4 * * 0  /Users/a1101417/Work/business-model/db/run_category_rank.sh           # 주간 랭킹
```
> 래퍼는 키를 run.sh 에서 로드하고 락·로그를 둠. `crontab -e` 로 위 3줄 추가.

## D-2. 비정형 인사이트 자동화 (6·7단계 상시/일배치) — 현행 구성
대상 DB = `insights_demo`(실데이터 위치). 증분 적재 후 둘을 분리 운용한다.
```bash
# 데이터 더 적재(증분·비파괴): 이미 적재된 패키지는 skip, 신규만 추가. --reset 주면 옛 전체삭제 동작.
INSIGHTS_DB=insights_demo python3 db/demo_load_trees.py --limit 2000

# 6단계(상시): 네이버 리뷰→LLM. launchd 상주 루프(KeepAlive·재부팅 생존) — 새 카탈로그만 채움.
#   네이버 429는 QuotaStop 으로 '미기록(큐 유지)' → 오염 없음. 진짜 빈 것만 기록하되 --retry-empty 는
#   attempts<retry-empty-max(기본3)까지만 재시도해 '리뷰 없음'으로 수렴(무한루프 방지).
launchctl list | grep catalog-insight             # 등록 확인(com.steve.catalog-insight)
launchctl unload/load ~/Library/LaunchAgents/com.steve.catalog-insight.plist   # 중지/재시작
#   튜닝(env): CI_WORKERS(기본10) CI_BATCH(1500) CI_SLEEP_DRAIN/IDLE/QUOTA · 로그 db/catalog_insight.log
#   단발 수동: INSIGHTS_DB=insights_demo python3 db/catalog_insight_backfill.py --retry-empty --limit 1500

# 7단계(쿼터 찰 때마다=일배치): 유튜브. launchd 가 매일 09:00 자동 실행(태평양 자정 쿼터 리셋 후).
launchctl list | grep youtube-backfill            # 등록 확인(com.steve.youtube-backfill)
INSIGHTS_DB=insights_demo db/run_youtube_backfill.sh   # 지금 즉시 1회(쿼터 한도까지) · 로그 db/youtube_backfill.log
```
```bash
# 리포트·사이트 자동 리빌드(매일 10:00, 유튜브 09:00 뒤) — API 없이 Mongo만 읽음. 카테고리랭킹→리포트5종→site_build.
launchctl list | grep rebuild                     # 등록 확인(com.steve.rebuild)
db/run_rebuild.sh                                  # 지금 즉시 1회 · 로그 db/rebuild.log
```
> 옛 `com.steve.insights1002`(launchd)는 죽은 경로(`Workspace/`)+옛 run_batch 를 가리켜 `.disabled` 로 비활성화함.
> 현행 launchd 3종: com.steve.catalog-insight(6단계 상시) · com.steve.youtube-backfill(7단계 09:00) · com.steve.rebuild(리빌드 10:00).

## E. 운영(production, insights DB) 차이
- `INSIGHTS_DB` 빼면 기본 `insights`. 데모 대신 실제 적재(`db/load_mongo.py insights_1002.jsonl ...`) 사용.
- 카테고리: 운영은 Oracle `DISP_CTGR1_NM`(`db/export_bndl_category.py` → `--bndl-category`)가 정석. offers 기반 `realize_category.py` 는 그 대안(네이버 실측).
- 가격 추이 데모 시드(`seed_price_history_demo.py`)는 **데모 전용** — 운영은 일일 cron 이 실제 스냅샷만 누적.
