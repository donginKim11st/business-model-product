#!/usr/bin/env python3
"""
SNS 버즈 — 제품별 네이버 블로그·카페 언급량 + 유튜브 최근 영상수.
velocity.json(리뷰·평점·속도)에 buzz 필드를 덧붙여 '한눈에' 보강.
stdlib(urllib). 키: NAVER_CLIENT_ID/SECRET + YOUTUBE_API_KEY (run.sh 재사용).

    python3 review_velocity.py    # velocity.json 먼저
    NAVER_CLIENT_ID=.. NAVER_CLIENT_SECRET=.. YOUTUBE_API_KEY=.. python3 buzz.py
"""
import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")


def naver_recent(kind, kw, cid, csec, days=30):
    """누적 total + 최근 days일 글 수(증가 속도). blog는 sort=date로 날짜정렬돼 정확.
    반환 (total, recent_count, has_date)."""
    url = f"https://openapi.naver.com/v1/search/{kind}.json?" + urllib.parse.urlencode(
        {"query": kw, "display": 100, "sort": "date"})
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", cid)
    req.add_header("X-Naver-Client-Secret", csec)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            js = json.load(r)
    except Exception:
        return 0, 0, False
    total = int(js.get("total", 0))
    cut = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y%m%d")
    dated = [it.get("postdate", "") for it in js.get("items", []) if it.get("postdate")]
    recent = sum(1 for d in dated if d >= cut)
    return total, recent, bool(dated)


def yt_recent(kw, key, days=180):
    after = (datetime.date.today() - datetime.timedelta(days=days)).isoformat() + "T00:00:00Z"
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(
        {"part": "snippet", "q": kw, "type": "video", "maxResults": 25, "publishedAfter": after, "key": key})
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            return len(json.load(r).get("items", []))
    except Exception:
        return None  # 키 없음/쿼터초과 등


def bkw(name, size):
    t = name.replace(size or "", "")
    t = re.sub(r"(싸이닉)\s+싸이닉", r"\1", t)
    toks = [x for x in t.split() if not re.match(r"^\d", x)]
    return " ".join(toks[:4]).strip()


def main():
    cid, csec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    yt = os.environ.get("YOUTUBE_API_KEY")
    if not (cid and csec):
        print("✗ NAVER 키 필요"); sys.exit(1)
    if not yt:
        print("⚠ YOUTUBE_API_KEY 없음 — 유튜브는 건너뜀")
    v = json.load(open(os.path.join(OUT, "velocity.json"), encoding="utf-8"))
    for r in v["rows"]:
        kw = bkw(r["name"], None)
        bt, br, _ = naver_recent("blog", kw, cid, csec)
        ct, cr, cd = naver_recent("cafearticle", kw, cid, csec)
        r["blog"], r["blog_recent"] = bt, br
        r["cafe"], r["cafe_recent"] = ct, (cr if cd else None)
        r["yt"] = yt_recent(kw, yt) if yt else None   # 최근 180일 영상 수
        r["buzz_kw"] = kw
    v["buzz_at"] = datetime.date.today().isoformat()
    json.dump(v, open(os.path.join(OUT, "velocity.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"버즈 수집 {len(v['rows'])} 제품 (유튜브 {'O' if yt else 'X'}) — 최근 30일 글 수 기준")
    for r in v["rows"][:8]:
        cafe = r['cafe_recent'] if r['cafe_recent'] is not None else '—'
        print(f"  {r['name'][:22]:24} 블로그 +{r['blog_recent']:>3}/30일(누적 {r['blog']:,}) · 카페 +{cafe} · 📺{r['yt'] if r['yt'] is not None else '—'}")
    print("velocity.json 갱신(buzz 추가)")


if __name__ == "__main__":
    main()
