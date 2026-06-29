#!/usr/bin/env python3
"""YouTube 디커플 backfill — 일일 쿼터 한도 안에서 product.youtube 를 '천천히' 채운다.

왜 분리?  YouTube Data API 일일 쿼터는 작다(search.list = 100 units/키워드).
1만 쿼터면 하루 ~95 키워드뿐이라, 24k 번들 전체엔 수개월이 걸린다. 메인 인사이트 배치
(run_batch.py)는 이걸 기다리면 안 되므로 네이버(+다나와)만으로 무정지로 끝내고, product 는
youtube.status='pending' 으로 적재된다. 이 스크립트가 그 pending 큐를 우선순위대로,
하루 쿼터 예산만큼만 처리해 product.youtube 에 '출처 + 유튜브 전용 인사이트'를 누적한다.

  product.youtube = {
    status: pending|done|empty|error,
    taxonomy: {...}, faqs: [...],        # 유튜브 근거로만 추출한 비정형 인사이트
    n_sources, n_videos, n_comments,     # 수집 규모
    fetched_at: "ISO", attempts: int, last_error: str|null,
  }
  sources 컬렉션에는 유튜브 원문이 kind='youtube' 로 추가된다(_id=uid:yt:<sid>).

재개 안전: status=='pending'(또는 youtube 필드 없음)만 처리. 쿼터 소진/오류면 그 항목을 pending 으로
되돌리고 우아하게 중단 → 다음 실행이 이어받는다. 매일 1회 launchd/cron 권장.

키는 run.sh export 로드(값 출력 안 함):
  set -a; eval "$(grep '^export ' run.sh)"; set +a
  MONGO_URI="mongodb://localhost:47017/?directConnection=true" \
    python3 db/youtube_backfill.py --daily-units 9000 [--include-variants] [--dry-run] [--limit N]
"""
import os
import sys
import time
import argparse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

os.environ.setdefault("INSIGHT_MODEL", "gpt-4o-mini")

import run_batch
import naver_review_geo as nrg
import load_mongo
from pymongo import MongoClient, InsertOne, DeleteMany

# 쿼터 추정: search.list(영상검색)=100, commentThreads.list(영상당 댓글)=1
SEARCH_UNITS = 100


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def est_units(n_videos):
    return SEARCH_UNITS + n_videos      # 영상검색 1회 + 영상당 댓글 1회


def priority(d):
    """업무가치 큰 순서로 먼저 채운다: 기존 리뷰량(분석수)·다중몰 노출 가중.
    (price_summary 는 footwear 전용 메타라 food 문서에선 n_malls=0 → 분석수만으로 정렬됨.)"""
    n_rev = d.get("analyzed_count") or (d.get("sources") or {}).get("naver") or 0
    n_malls = (d.get("price_summary") or {}).get("n_malls") or 0
    return (n_rev + n_malls * 5, n_rev)


def pick_queue(db, include_variants, limit):
    q = {"$or": [{"youtube.status": "pending"}, {"youtube": {"$exists": False}}]}
    if not include_variants:
        q["type"] = {"$ne": "variant"}     # 유튜브 댓글은 base 일반 내용 → 변형 제외(쿼터 절약)
    proj = {"_id": 1, "keyword": 1, "variant_value": 1, "type": 1,
            "analyzed_count": 1, "sources": 1, "price_summary": 1, "youtube": 1}
    docs = list(db.products.find(q, proj))
    docs.sort(key=priority, reverse=True)
    return docs[:limit] if limit else docs


def store_youtube(db, uid, keyword, items, block, attempts):
    """유튜브 원문 → sources(kind=youtube), 유튜브 인사이트 → product.youtube($set)."""
    n_videos = len({it.get("video_id") or it.get("link") for it in items})
    yt = {"status": "done",
          "taxonomy": (block or {}).get("taxonomy") or {},
          "faqs": (block or {}).get("faqs") or [],
          "source_counts": (block or {}).get("sources") or {},
          "n_sources": len(items), "n_videos": n_videos, "n_comments": len(items),
          "fetched_at": now_iso(), "attempts": attempts, "last_error": None}
    db.products.update_one({"_id": uid}, {"$set": {"youtube": yt}})
    # 유튜브 원문 교체(멱등): 이 상품의 기존 youtube source 지우고 다시
    db.sources.delete_many({"product_uid": uid, "kind": "youtube"})
    sdocs = []
    for sid, s in ((block or {}).get("source_index") or {}).items():
        sdocs.append(dict(s, _id=f"{uid}:yt:{sid}", product_uid=uid, local_id=sid, kind="youtube"))
    if sdocs:
        db.sources.insert_many(sdocs, ordered=False)
    n_pt = sum(len(v) for _, v in load_mongo.walk_points(yt["taxonomy"]))
    return n_pt, len(sdocs)


def mark(db, uid, status, attempts, err=None):
    db.products.update_one({"_id": uid}, {"$set": {
        "youtube.status": status, "youtube.attempts": attempts,
        "youtube.last_error": err, "youtube.fetched_at": now_iso()}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-units", type=int, default=int(os.environ.get("YT_DAILY_UNITS", "9000")),
                    help="이번 실행에서 쓸 쿼터 예산(여유 두고 1만 미만 권장)")
    ap.add_argument("--n-videos", type=int, default=3)
    ap.add_argument("--n-comments", type=int, default=50)
    ap.add_argument("--limit", type=int, default=0, help="이번 실행 최대 처리 건수(0=쿼터 한도까지)")
    ap.add_argument("--max-attempts", type=int, default=5, help="이 횟수 넘게 실패하면 error 로 고정")
    ap.add_argument("--include-variants", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="status=done 도 다시 (재수집)")
    ap.add_argument("--refresh-empty", action="store_true",
                    help="status=empty 를 pending 으로 되돌림(쿼터 등으로 잘못 empty 처리된 건 복구)")
    ap.add_argument("--dry-run", action="store_true", help="API/LLM 호출 없이 큐·예산만 출력")
    args = ap.parse_args()

    ytk = os.environ.get("YOUTUBE_API_KEY")
    if not args.dry_run and not ytk:
        sys.exit("✗ YOUTUBE_API_KEY 없음 (run.sh export 로드 필요)")
    if not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
        sys.exit("✗ OPENAI_API_KEY 없음")
    nid = os.environ.get("NAVER_CLIENT_ID"); nsec = os.environ.get("NAVER_CLIENT_SECRET")

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]
    if args.refresh:
        db.products.update_many({"youtube.status": "done"}, {"$set": {"youtube.status": "pending"}})
    if args.refresh_empty:
        db.products.update_many({"youtube.status": "empty"}, {"$set": {"youtube.status": "pending"}})

    queue = pick_queue(db, args.include_variants, args.limit)
    per = est_units(args.n_videos)
    budget_n = args.daily_units // per
    print(f"backfill 큐 {len(queue):,}건 · 쿼터예산 {args.daily_units}u (건당 ~{per}u → 최대 ~{budget_n}건/회)"
          f" · 모델 {os.environ['INSIGHT_MODEL']} · 변형포함={args.include_variants}")
    print("=" * 64)

    if args.dry_run:
        for d in queue[:min(20, len(queue))]:
            print(f"  {priority(d)[0]:6.0f}  {d['_id'][:40]:40}  '{(d.get('keyword') or '')[:36]}'")
        print(f"... (총 {len(queue):,}건 중 상위 {min(20,len(queue))} 표시) — dry-run, API 호출 없음")
        return

    llm = run_batch.make_client()
    t0 = time.time(); used = 0; n_done = n_empty = n_err = 0
    for i, d in enumerate(queue, 1):
        if used + per > args.daily_units:
            print(f"\n■ 쿼터 예산 소진({used}/{args.daily_units}u) → 중단. 나머지는 다음 실행이 이어받음.", flush=True)
            break
        uid = d["_id"]; kw = d.get("keyword") or ""
        attempts = ((d.get("youtube") or {}).get("attempts") or 0) + 1
        print(f"[{i}/{len(queue)}] {uid[:38]:38} '{kw[:34]}' ...", flush=True)
        ts = time.time()
        try:
            items = nrg.collect_youtube(kw, ytk, n_videos=args.n_videos, n_comments=args.n_comments)
            used += per
        except Exception as e:
            if run_batch._is_quota_err(e):
                used += SEARCH_UNITS    # 검색 쿼터는 이미 소비됨(예산 보고용)
                print(f"\n■ YouTube 일일 쿼터 소진 → 중단(이 건 pending 유지, 시도 미카운트). 다음 실행 재개.", flush=True)
                mark(db, uid, "pending", attempts - 1)   # 공정한 시도 못했으니 attempts 롤백
                break
            n_err += 1
            status = "error" if attempts >= args.max_attempts else "pending"
            mark(db, uid, status, attempts, str(e)[:160])
            print(f"     ✗ 수집오류({status}, 시도 {attempts}): {str(e)[:70]}", flush=True)
            continue
        for it in items:
            it["is_ad"] = nrg.is_ad(it); it["ad_signals"] = nrg.ad_signals(it)
        if not items:
            mark(db, uid, "empty", attempts)
            n_empty += 1
            print("     → 유튜브 결과 없음 (empty)", flush=True)
            continue
        try:
            block = run_batch.extract_full(kw, items, llm)
        except Exception as e:
            n_err += 1
            status = "error" if attempts >= args.max_attempts else "pending"
            mark(db, uid, status, attempts, "extract: " + str(e)[:140])
            print(f"     ✗ 추출오류({status}): {str(e)[:70]}", flush=True)
            continue
        n_pt, n_src = store_youtube(db, uid, kw, items, block, attempts)
        n_done += 1
        print(f"     → 영상댓글 {len(items)} · youtube point {n_pt} · {time.time()-ts:.1f}s", flush=True)

    cost = run_batch.usd()
    print("=" * 64)
    print(f"완료: done {n_done} · empty {n_empty} · err {n_err} · 쿼터 {used}u "
          f"· {time.time()-t0:.0f}s · LLM ≈ ${cost:.4f} (≈ ₩{cost*1380:.0f})")
    q_remain = {"$or": [{"youtube.status": "pending"}, {"youtube": {"$exists": False}}]}
    if not args.include_variants:
        q_remain["type"] = {"$ne": "variant"}     # 큐 필터와 동일(0으로 수렴하도록)
    print(f"남은 pending: {db.products.count_documents(q_remain):,}건")


if __name__ == "__main__":
    main()
