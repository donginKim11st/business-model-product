#!/usr/bin/env python3
"""identity 합류 라이브 통합 테스트 (T6). 격리 DB(insights_identity_test)에서 end-to-end.

검증: backfill 합류(catalogs[].identity + products.identity) · price_summary 공존 보존 ·
status:empty(--mark-empty) · progress 집계 · reload 보존(load_mongo ReplaceOne 경로).
실데이터(insights_demo) 미접촉. 끝나면 테스트 DB drop. Mongo 없으면 SKIP.

실행: python3 insight/db/test_integration_identity.py
"""
import os
import sys
import csv
import json
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

URI = os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")
TEST_DB = "insights_identity_test"
PY = sys.executable


def _connect():
    from pymongo import MongoClient
    c = MongoClient(URI, serverSelectionTimeoutMS=3000)
    c.admin.command("ping")
    return c


def _run(mod_args, db):
    env = dict(os.environ, MONGO_URI=URI, INSIGHTS_DB=db)
    p = subprocess.run([PY, os.path.join(HERE, mod_args[0])] + mod_args[1:],
                       cwd=os.path.dirname(HERE), capture_output=True, text=True, env=env)
    return p


def main():
    try:
        client = _connect()
    except Exception as e:
        print(f"SKIP (Mongo 없음): {str(e)[:80]}")
        return 0

    db = client[TEST_DB]
    db.products.delete_many({})
    # 픽스처: 의류 패키지(가격 있음=공존 테스트) + 식품 패키지(매칭 없음=empty 테스트)
    db.products.insert_many([
        {"_id": "P9001", "type": "package", "bndl_grp": 9001, "keyword": "나이키 운동화",
         "category_l1": "신발", "n_catalogs": 1,
         "catalogs": [{"ctlg_no": "C1", "disp": "나이키 운동화 270", "price_summary": {"min": 50000}}]},
        {"_id": "P9002", "type": "package", "bndl_grp": 9002, "keyword": "쿡시 미역국",
         "category_l1": "국·탕·찌개", "n_catalogs": 1,
         "catalogs": [{"ctlg_no": "F1", "disp": "쿡시 미역국 490g"}]},
    ])

    # uid 스탬프 CSV(P9001/C1 만 매칭 — P9002 는 산출 없음)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8")
    cols = ["insight_uid", "ctlg_no", "brand", "style_code", "color", "origin", "material", "mfg_date", "name"]
    w = csv.DictWriter(tmp, fieldnames=cols); w.writeheader()
    w.writerow({"insight_uid": "P9001", "ctlg_no": "C1", "brand": "나이키", "style_code": "KK1334",
                "color": "검정", "origin": "베트남", "material": "가죽", "mfg_date": "2026-01",
                "name": "나이키 운동화 270"})
    tmp.close()

    fails = []

    # 1) backfill 합류
    p = _run(["identity_backfill.py", "--csv", tmp.name], TEST_DB)
    if p.returncode != 0:
        fails.append(f"backfill rc={p.returncode}: {p.stderr[-200:]}")
    d1 = db.products.find_one({"_id": "P9001"})
    cident = (d1["catalogs"][0].get("identity") or {})
    if cident.get("style_code") != "KK1334":
        fails.append(f"catalogs[].identity.style_code={cident.get('style_code')}")
    if cident.get("color") != "검정":
        fails.append(f"catalogs[].identity.color={cident.get('color')}")
    if cident.get("gosi") != {"origin": "베트남", "material": "가죽", "mfg_date": "2026-01"}:
        fails.append(f"gosi={cident.get('gosi')}")
    if d1["catalogs"][0].get("price_summary") != {"min": 50000}:
        fails.append(f"price_summary 미보존: {d1['catalogs'][0].get('price_summary')}")
    if (d1.get("identity") or {}).get("status") != "done" or d1["identity"].get("brand") != "나이키":
        fails.append(f"products.identity={d1.get('identity')}")

    # 2) status:empty (--mark-empty 로 미매칭 P9002 드레인)
    _run(["identity_backfill.py", "--csv", tmp.name, "--mark-empty"], TEST_DB)
    d2 = db.products.find_one({"_id": "P9002"})
    if (d2.get("identity") or {}).get("status") != "empty":
        fails.append(f"P9002 status != empty: {d2.get('identity')}")

    # 3) progress 집계
    pp = _run(["pipeline_progress.py", "--stage", "identity"], TEST_DB)
    try:
        prog = json.loads([l for l in pp.stdout.splitlines() if l.strip()][-1])["identity"]
        if prog["total"] < 2 or prog["joined"] < 1 or prog["empty"] < 1:
            fails.append(f"progress={prog}")
    except Exception as e:
        fails.append(f"progress parse: {e} / out={pp.stdout[-150:]}")

    # 4) reload 보존: load_mongo ReplaceOne 경로가 identity 를 보존하는지(T1 실DB 검증)
    import load_mongo
    from pymongo import ReplaceOne
    existing = db.products.find_one({"_id": "P9001"}, {"youtube": 1, "representative": 1, "identity": 1})
    fresh = {"_id": "P9001", "type": "package", "keyword": "나이키 운동화",
             "youtube": {"status": "pending", "attempts": 0}}        # identity 없는 새 빌드
    load_mongo._preserve_async_fields(fresh, existing)
    db.products.bulk_write([ReplaceOne({"_id": "P9001"}, fresh, upsert=True)])
    after = db.products.find_one({"_id": "P9001"})
    if (after.get("identity") or {}).get("status") != "done":
        fails.append(f"reload 후 identity 소멸: {after.get('identity')}")

    # cleanup
    client.drop_database(TEST_DB)
    os.unlink(tmp.name)

    if fails:
        print("INTEGRATION FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("INTEGRATION PASS: 합류·공존보존·empty·progress·reload보존 (격리 DB end-to-end)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
