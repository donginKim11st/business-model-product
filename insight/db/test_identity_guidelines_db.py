#!/usr/bin/env python3
"""identity_guidelines_db CRUD 통합 테스트 (Phase 1). 격리 DB, 끝나면 drop. Mongo 없으면 SKIP.

실행: python3 insight/db/test_identity_guidelines_db.py
"""
import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import identity_guidelines_db as gdb

URI = os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")
DB1, DB2 = "insights_identity_dbtest", "insights_identity_dbtest2"


def main():
    try:
        from pymongo import MongoClient
        client = MongoClient(URI, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
    except Exception as e:
        print(f"SKIP (Mongo 없음): {str(e)[:80]}")
        return 0

    db = client[DB1]
    for c in (gdb.GUIDELINES, gdb.LABELS, gdb.RUNS):
        db[c].delete_many({})
    fails = []

    # 라벨 dedup: 같은 (category,seed,cand) 두 번 → 1개, 첫 라벨 보존(perturb 자동).
    gdb.add_labels(db, "C", [{"seed_disp": "a", "cand_name": "x", "score": 0.5, "label": 1}])
    gdb.add_labels(db, "C", [{"seed_disp": "a", "cand_name": "x", "score": 0.9, "label": 0}])
    labs = gdb.get_labels(db, "C")
    if len(labs) != 1 or labs[0]["label"] != 1:
        fails.append(f"dedup/setOnInsert: {labs}")

    # overwrite=True(사람 ingest): 라벨 갱신.
    gdb.add_labels(db, "C", [{"seed_disp": "a", "cand_name": "x", "score": 0.9, "label": 0}], overwrite=True)
    if gdb.get_labels(db, "C")[0]["label"] != 0:
        fails.append("overwrite 미반영")

    # 가이드라인 upsert/get/thresh_map
    gdb.upsert_guideline(db, "C", name_thresh=0.5, status="effective")
    gdb.upsert_guideline(db, "default", name_thresh=0.4)
    gdb.upsert_guideline(db, "D", name_thresh=None, status="needs_strong_key")  # None → thresh_map 제외
    g = gdb.get_guideline(db, "C")
    if not g or g["name_thresh"] != 0.5 or g["status"] != "effective":
        fails.append(f"guideline get: {g}")
    tm = gdb.thresh_map(db)
    if tm.get("C") != 0.5 or tm.get("default") != 0.4 or "D" in tm:
        fails.append(f"thresh_map: {tm}")

    # 보정 이력
    gdb.add_calib_run(db, "C", n_labels=1, recommended=0.5, verdict="effective", applied=True)
    if len(gdb.get_calib_runs(db, "C")) < 1:
        fails.append("calib_run 미기록")

    # export/import 라운드트립
    p = tempfile.mktemp(suffix=".json")
    gdb.export_thresh_json(db, p)
    m = json.load(open(p, encoding="utf-8"))
    if m.get("C") != 0.5 or "default" not in m or "D" in m:
        fails.append(f"export json: {m}")
    db2 = client[DB2]
    for c in (gdb.GUIDELINES, gdb.LABELS, gdb.RUNS):
        db2[c].delete_many({})
    n = gdb.import_thresh_json(db2, p)
    g2 = gdb.get_guideline(db2, "C")
    if n < 2 or not g2 or g2["name_thresh"] != 0.5:
        fails.append(f"import roundtrip: n={n} g2={g2}")

    client.drop_database(DB1)
    client.drop_database(DB2)
    os.unlink(p)

    if fails:
        print("DB FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("DB PASS: 라벨 dedup/overwrite · 가이드라인 upsert/thresh_map · 보정이력 · JSON export/import (격리 DB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
