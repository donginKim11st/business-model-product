#!/usr/bin/env python3
"""[DEMO 전용] 가격 추이 UI를 '지금' 보여주기 위한 합성 이력 생성.

네이버 쇼핑은 과거 가격을 안 주므로 진짜 추이는 food_price_backfill 을 매일 돌려 스냅샷을 쌓아야 한다
(price_history 컬렉션, 일자별 1행). 그런데 데모 시점엔 오늘 스냅샷 1개뿐이라 추이가 안 보인다 →
이 스크립트가 현재 가격을 기준으로 지난 N일치 '합성' min/median 을 결정론적으로 만들어 price_history 에
넣는다(source="synthetic_demo"). 오늘(i=0) 행만 실제 현재가(source="naver_shop"). 운영에선 불필요.

  INSIGHTS_DB=insights_demo MONGO_URI=... DAYS=14 python3 db/seed_price_history_demo.py
"""
import os
import sys
import math
import hashlib
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient


def _h(s):
    return int(hashlib.md5(str(s).encode()).hexdigest(), 16)


def main():
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights_demo")]
    days = int(os.environ.get("DAYS", "14"))
    today = datetime.now(timezone.utc).date()

    n_cat = n_rows = 0
    for p in db.products.find({"catalogs.price_summary.min": {"$ne": None}}, {"_id": 1, "catalogs": 1}):
        for c in p.get("catalogs") or []:
            ps = c.get("price_summary") or {}
            ctlg = c.get("ctlg_no")
            if not (ps.get("min") and ctlg):
                continue
            n_cat += 1
            base_min = ps["min"]; base_med = ps.get("median") or base_min; base_max = ps.get("max") or base_min
            seed = _h(ctlg)
            amp = 0.04 + (seed % 6) / 100.0          # 진폭 4~9%
            period = 5 + seed % 7                      # 주기 5~11일
            drift = ((seed % 3) - 1) * 0.0035          # 일별 완만한 추세 -0.35%~+0.35%
            ops = []
            for i in range(days, -1, -1):              # i일 전 … 오늘(0)
                date = (today - timedelta(days=i)).isoformat()
                if i == 0:
                    f, src = 1.0, "naver_shop"          # 오늘은 실제 현재가
                else:
                    f = 1 + amp * math.sin((seed + i) / period) + drift * i
                    src = "synthetic_demo"
                mn = max(1, round(base_min * f))
                md = max(mn, round(base_med * f))
                ops.append({"_id": f"{ctlg}@{date}", "ctlg_no": ctlg, "package_uid": p["_id"],
                            "date": date, "min": mn, "max": round(base_max * f), "median": md,
                            "n_malls": ps.get("n_malls"), "low_mall": ps.get("low_mall"), "source": src})
            for d in ops:
                db.price_history.update_one({"_id": d["_id"]}, {"$set": d}, upsert=True)
                n_rows += 1
    db.price_history.create_index("ctlg_no"); db.price_history.create_index("date")
    print(f"[DEMO] 합성 가격이력 생성 · 카탈로그 {n_cat} · 행 {n_rows} ({days+1}일치/카탈로그) "
          f"· source=synthetic_demo(오늘만 실제)")


if __name__ == "__main__":
    main()
