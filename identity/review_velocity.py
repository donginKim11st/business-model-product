#!/usr/bin/env python3
"""
리뷰 모멘텀 + 평점 — 누적 리뷰수 한계('얼마나 팔렸나')를 넘어 '지금 활발한가'를.
다나와 vssearch(평점·누적리뷰·pcode) + 리뷰상세 날짜표본으로 '최근 활동도' 산출,
그리고 날짜별 스냅샷을 저장해 실측 '리뷰 증가 속도'(Δ/일)를 시간이 지나며 채운다.

    python3 review_velocity.py     # → outputs/velocity.json (+ 스냅샷)
키 불필요(다나와 공개 AJAX). requests 사용.
"""
import datetime
import json
import os
import re
import sys
import warnings
warnings.filterwarnings("ignore")

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
sys.path.insert(0, "/Users/a1101417/Work/business-model/insight")
from naver_review_geo import fetch_danawa_reviews  # 리뷰 상세(날짜) 수집 재사용

VSAPI = "https://prod.danawa.com/api/vssearch/searchProducts.php"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_TAG = re.compile(r"<[^>]+>")
_STOP = {"싸이닉", "더", "심플", "엔조이", "정품"}
TODAY = datetime.date.today()


def vssearch(keyword):
    from urllib.parse import quote
    try:
        r = requests.get(VSAPI, params={"keyword": keyword, "page": 1, "limit": 24},
                         headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                                  "Referer": "https://search.danawa.com/dsearch.php?query=" + quote(keyword),
                                  "Accept": "application/json"}, timeout=15)
        prods = (r.json().get("result") or {}).get("products") or []
    except Exception:
        return []
    return [{"pcode": str(p.get("productCode") or ""), "name": _TAG.sub("", p.get("productName") or ""),
             "reviews": int(p.get("reviewCount") or 0), "star": p.get("starPoint"),
             "min_price": int(p.get("minPrice") or 0)} for p in prods]


def _toks(s):
    return {t for t in re.split(r"[^가-힣a-zA-Z0-9]+", s.lower()) if len(t) >= 2 and t not in _STOP}


def best_match(product, cands):
    size, pt = product.get("size"), _toks(product["name"])
    scored = []
    for c in cands:
        if size and size not in c["name"].replace(" ", ""):
            continue
        ov = len(pt & _toks(c["name"]))
        if ov >= 2 and c["pcode"]:
            scored.append((ov, c["reviews"], c))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return scored[0][2] if scored else None


def recent_activity(pcode, name):
    """리뷰상세 날짜 표본 → 최근성 지표. (표본 기준 — 전수 아님)"""
    try:
        revs = fetch_danawa_reviews(pcode, name, max_pages=2)
    except Exception:
        revs = []
    dates = sorted(d for d in (r.get("postdate") for r in revs) if d and len(d) == 8)
    if not dates:
        return {"sample": 0, "recent180": 0, "ratio": 0, "newest": None}
    def age(d):
        return (TODAY - datetime.date(int(d[:4]), int(d[4:6]), int(d[6:]))).days
    recent180 = sum(1 for d in dates if age(d) <= 180)
    return {"sample": len(dates), "recent180": recent180,
            "ratio": round(recent180 / len(dates), 2), "newest": dates[-1]}


def main():
    data = json.load(open(os.path.join(OUT, "naver_crossmarket_v3.json"), encoding="utf-8"))
    prods = sorted((p for p in data["products"] if p.get("official_unit")),
                   key=lambda p: -p["undercut_pct"])[:14]
    rows = []
    for p in prods:
        kw = " ".join([t for t in p["name"].split() if not re.match(r"^\d", t)][:5])
        m = best_match(p, vssearch(kw))
        act = recent_activity(m["pcode"], m["name"]) if m else {"sample": 0, "recent180": 0, "ratio": 0, "newest": None}
        rows.append({"name": p["name"], "matched": bool(m), "pcode": m["pcode"] if m else None,
                     "rating": (m["star"] if m else None), "reviews": m["reviews"] if m else 0,
                     "recent180": act["recent180"], "recent_ratio": act["ratio"],
                     "sample": act["sample"], "newest": act["newest"],
                     "undercut_pct": p["undercut_pct"], "lowest_unit": p["lowest_unit"],
                     "lowest_mall": p["lowest_mall"], "n_malls": p["n_malls"]})

    # 진짜 '증가 속도': 이전 리뷰 스냅샷과 diff (있으면)
    snapdir = os.path.join(OUT, "snapshots")
    os.makedirs(snapdir, exist_ok=True)
    prev = sorted(f for f in os.listdir(snapdir) if f.startswith("reviews_"))
    prevmap = {}
    prevdate = None
    if prev:
        pj = json.load(open(os.path.join(snapdir, prev[-1]), encoding="utf-8"))
        prevdate = pj.get("date")
        prevmap = {r["name"]: r["reviews"] for r in pj["rows"]}
    for r in rows:
        if r["name"] in prevmap and prevdate:
            days = max(1, (TODAY - datetime.date.fromisoformat(prevdate)).days)
            r["velocity_per_day"] = round((r["reviews"] - prevmap[r["name"]]) / days, 1)
        else:
            r["velocity_per_day"] = None  # 실측은 2회차부터
    json.dump({"date": TODAY.isoformat(), "rows": [{"name": r["name"], "reviews": r["reviews"]} for r in rows]},
              open(os.path.join(snapdir, f"reviews_{TODAY.isoformat()}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # 모멘텀 점수 = 누적리뷰(정규화) × 최근활동비율
    rmax = max((r["reviews"] for r in rows), default=1) or 1
    for r in rows:
        r["momentum"] = round((r["reviews"] / rmax) * (r["recent_ratio"] or 0), 3)
    rows.sort(key=lambda r: -r["momentum"])

    json.dump({"date": TODAY.isoformat(), "has_real_velocity": bool(prevmap),
               "prev_date": prevdate, "rows": rows},
              open(os.path.join(OUT, "velocity.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"제품 {len(rows)} · 매칭 {sum(r['matched'] for r in rows)} · 실측속도 {'있음' if prevmap else '없음(2회차부터)'}")
    for r in rows[:8]:
        v = f"{r['velocity_per_day']}/일" if r["velocity_per_day"] is not None else "측정중"
        print(f"  {r['name'][:24]:26} ★{r['rating']} 리뷰 {r['reviews']:>5,} 최근활동 {int((r['recent_ratio'] or 0)*100)}% 속도 {v} · −{r['undercut_pct']}%")
    print("outputs/velocity.json 생성")


if __name__ == "__main__":
    main()
