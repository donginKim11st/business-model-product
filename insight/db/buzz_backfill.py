#!/usr/bin/env python3
"""언급량(buzz) 수집 — 이 제품이 지금 네이버/유튜브에서 얼마나 언급되는지(실제 API 카운트).

각 패키지(제품)의 base 키워드로:
  · 네이버 블로그 total  : openapi 검색 결과 total(정확) — 블로그에서 얼마나 다뤄지나
  · 네이버 쇼핑  total   : shop 검색 결과 total(정확) — 판매 리스팅 수(시장 관심/유통 폭)
  · 유튜브 totalResults  : search.list pageInfo.totalResults(YouTube가 주는 *추정치*) — 영상 노출량
저장: products.buzz = {naver_blog, naver_shop, youtube, youtube_status, fetched_at, source:"api"}

전부 실제 API 호출이며 합성/가공 없음. 네이버는 저렴(패키지당 2콜, 일 25k 한도)해 전량, 유튜브는
search.list=100 units(일 1만 한도)라 예산(--yt-max 패키지)만큼만, 나머지는 youtube_status=pending.
재개 안전: buzz 있으면 skip(--refresh 로 갱신). 쿼터 소진 시 우아하게 중단.

  set -a; eval "$(grep '^export ' run.sh)"; set +a
  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/buzz_backfill.py [--yt-max 60] [--limit N] [--refresh]
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

import requests
import naver_review_geo as nrg
from collections import Counter
from pymongo import MongoClient


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def package_fullname(p):
    """언급량 질의용 풀네임 = base + 대표(최빈) 사이즈. generic 이름('화이트') 모호성 완화.
    카탈로그 개수까지는 안 붙임(너무 좁아 0 수렴). 사이즈 없으면 base."""
    base = (p.get("keyword") or "").strip()
    sizes = [c.get("size") for c in (p.get("catalogs") or []) if c.get("size")]
    if base and sizes:
        sz = Counter(sizes).most_common(1)[0][0]
        if sz and sz not in base:
            return f"{base} {sz}".strip()
    return base


def naver_total(endpoint, kw, nid, nsec):
    h = {"X-Naver-Client-Id": nid, "X-Naver-Client-Secret": nsec}
    r = requests.get(endpoint, headers=h, params={"query": kw, "display": 1}, timeout=10)
    r.raise_for_status()
    return int(r.json().get("total") or 0)


def youtube_total(kw, ytk):
    """search.list pageInfo.totalResults (YouTube 제공 *추정치*). 100 units."""
    r = requests.get(nrg.YT_SEARCH, params={"part": "snippet", "q": kw, "type": "video",
                                            "maxResults": 1, "key": ytk}, timeout=10)
    r.raise_for_status()
    return int(((r.json().get("pageInfo") or {}).get("totalResults")) or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yt-max", type=int, default=int(os.environ.get("BUZZ_YT_MAX", "60")),
                    help="유튜브 언급량 수집할 최대 패키지 수(100 units/콜, 일 1만 한도 보호). 0=유튜브 끔")
    ap.add_argument("--limit", type=int, default=0, help="처리 패키지 수(0=전체)")
    ap.add_argument("--refresh", action="store_true", help="이미 buzz 있어도 재수집")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    nid = os.environ.get("NAVER_CLIENT_ID"); nsec = os.environ.get("NAVER_CLIENT_SECRET")
    ytk = os.environ.get("YOUTUBE_API_KEY")
    if not args.dry_run and not (nid and nsec):
        sys.exit("✗ NAVER_CLIENT_ID/SECRET 필요 (run.sh export 로드)")

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]
    q = {"type": "package"}
    if not args.refresh:
        q["buzz"] = {"$exists": False}
    pkgs = list(db.products.find(q, {"_id": 1, "keyword": 1, "buzz": 1, "catalogs.size": 1}))
    # 언급 큰 순으로 보기 좋게: 쇼핑 리스팅 많은 패키지부터… 는 아직 모르니 그냥 순서대로
    if args.limit:
        pkgs = pkgs[:args.limit]
    print(f"buzz 수집 대상 패키지 {len(pkgs)}개 (유튜브 최대 {args.yt_max}, refresh={args.refresh})")
    print("=" * 64)
    if args.dry_run:
        for p in pkgs[:15]:
            print(f"  {p['_id']}  {p.get('keyword','')[:40]}")
        return

    t0 = time.time(); n = n_yt = 0; yt_dead = False
    for i, p in enumerate(pkgs, 1):
        kw = package_fullname(p)            # 풀네임(base+대표 사이즈)로 질의
        if not kw:
            continue
        buzz = {"source": "api", "fetched_at": now_iso(), "keyword": kw}
        try:
            buzz["naver_blog"] = naver_total(nrg.NAVER_ENDPOINT, kw, nid, nsec)
            buzz["naver_shop"] = naver_total(nrg.NAVER_SHOP_ENDPOINT, kw, nid, nsec)
        except Exception as e:
            print(f"  ✗ {p['_id']} 네이버 오류: {str(e)[:60]}", flush=True)
            continue
        # 유튜브: 예산 내에서만(쿼터 비쌈)
        if ytk and not yt_dead and n_yt < args.yt_max:
            try:
                buzz["youtube"] = youtube_total(kw, ytk)
                buzz["youtube_status"] = "done"
                n_yt += 1
            except Exception as e:
                if nrg and ("quota" in str(e).lower() or "403" in str(e)):
                    yt_dead = True
                    buzz["youtube_status"] = "pending"
                    print(f"  ■ 유튜브 쿼터 소진 → 이후 네이버만. ({n_yt}건 수집)", flush=True)
                else:
                    buzz["youtube_status"] = "error"
        else:
            buzz["youtube_status"] = "pending"
        db.products.update_one({"_id": p["_id"]}, {"$set": {"buzz": buzz}})
        n += 1
        if i % 50 == 0 or i == len(pkgs):
            print(f"  [{i}/{len(pkgs)}] buzz {n} · 유튜브 {n_yt} · {time.time()-t0:.0f}s", flush=True)
    db.products.create_index("buzz.naver_blog")
    print("=" * 64)
    print(f"완료 · buzz {n} 패키지 · 유튜브 {n_yt} · {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
