#!/usr/bin/env python3
"""identity 보정 DB — 카테고리별 가이드라인·라벨·보정이력의 source of truth (Phase 1).

Mongo(insights_demo) 컬렉션 3개:
  identity_guidelines : _id=category, {name_thresh, status, precision, recall, f1, n_labels,
                        recommended, updated_at, updated_by}. 보정한 '값'의 단일 진실.
  identity_labels     : 카테고리별 gold 라벨 {category, seed_uid, seed_disp, cand_name,
                        cand_style_code, score, label(0/1), source, labeled_by, ts}. (seed_disp,cand_name) 중복 제거.
  identity_calib_runs : 보정 실행 이력 {category, ts, n_labels, sweep[], recommended, verdict, applied, by}.

identity_name_thresh.json 은 이제 DB 의 export(캐시) — 매처는 그 JSON 을 읽으므로 Mongo 비의존 유지.
"""
import os
import json
import hashlib
from datetime import datetime, timezone

GUIDELINES = "identity_guidelines"
LABELS = "identity_labels"
RUNS = "identity_calib_runs"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_db():
    from pymongo import MongoClient
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")
    return MongoClient(uri)[os.environ.get("INSIGHTS_DB", "insights_demo")]


def ensure_indexes(db):
    db[LABELS].create_index("category")
    db[RUNS].create_index([("category", 1), ("ts", -1)])


# ── labels ────────────────────────────────────────────────────────────────────
def _label_id(category, seed_disp, cand_name):
    return hashlib.md5(f"{category}|{seed_disp}|{cand_name}".encode("utf-8")).hexdigest()


def add_labels(db, category, rows, source="perturb", by="auto", overwrite=False):
    """라벨 적재. (category, seed_disp, cand_name) 로 중복 제거.
    overwrite=False(perturb 자동): 기존 라벨 보존(첫 라벨 우선). True(사람 ingest): label 갱신."""
    from pymongo import UpdateOne
    ops = []
    for r in rows:
        _id = _label_id(category, r["seed_disp"], r.get("cand_name"))
        doc = {"category": category, "seed_uid": r.get("seed_uid"), "seed_disp": r["seed_disp"],
               "cand_name": r.get("cand_name"), "cand_brand": r.get("cand_brand"),
               "cand_style_code": r.get("cand_style_code"), "score": float(r.get("score") or 0),
               "label": int(r["label"]), "source": r.get("source", source),
               "labeled_by": by, "ts": now_iso()}
        if overwrite:
            ops.append(UpdateOne({"_id": _id}, {"$set": doc}, upsert=True))
        else:
            ops.append(UpdateOne({"_id": _id}, {"$setOnInsert": doc}, upsert=True))
    if not ops:
        return 0
    res = db[LABELS].bulk_write(ops)
    return (res.upserted_count or 0) + (res.modified_count or 0)


def get_labels(db, category):
    return list(db[LABELS].find({"category": category}))


# ── guidelines ──────────────────────────────────────────────────────────────────
def upsert_guideline(db, category, **fields):
    fields["updated_at"] = now_iso()
    db[GUIDELINES].update_one({"_id": category}, {"$set": fields}, upsert=True)


def get_guideline(db, category):
    return db[GUIDELINES].find_one({"_id": category})


def all_guidelines(db):
    return list(db[GUIDELINES].find())


def thresh_map(db):
    """매처용 {category: name_thresh}. name_thresh 설정된 카테고리만(없으면 default 폴백)."""
    return {g["_id"]: g["name_thresh"]
            for g in db[GUIDELINES].find({"name_thresh": {"$ne": None}})}


# ── calibration runs ──────────────────────────────────────────────────────────────
def add_calib_run(db, category, **fields):
    db[RUNS].insert_one({"category": category, "ts": now_iso(), **fields})


def get_calib_runs(db, category, limit=20):
    return list(db[RUNS].find({"category": category}).sort("ts", -1).limit(limit))


# ── JSON export/import (매처 캐시 ↔ DB) ──────────────────────────────────────────
def export_thresh_json(db, path):
    """DB 가이드라인 → identity_name_thresh.json(매처가 읽는 캐시). default 보장."""
    tm = thresh_map(db)
    if "default" not in tm:
        tm = {"default": 0.4, **tm}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tm, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return tm


def import_thresh_json(db, path, by="migrate"):
    """기존 identity_name_thresh.json → DB 가이드라인 마이그레이션(부트스트랩)."""
    if not os.path.exists(path):
        return 0
    m = json.load(open(path, encoding="utf-8"))
    for cat, thr in m.items():
        upsert_guideline(db, cat, name_thresh=float(thr), status="imported", updated_by=by)
    ensure_indexes(db)
    return len(m)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="identity 보정 DB 유틸")
    ap.add_argument("cmd", choices=["migrate", "export", "status"])
    ap.add_argument("--json", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "identity_name_thresh.json"))
    a = ap.parse_args()
    db = get_db()
    if a.cmd == "migrate":
        n = import_thresh_json(db, a.json)
        print(f"마이그레이션: identity_name_thresh.json → DB 가이드라인 {n}개")
    elif a.cmd == "export":
        tm = export_thresh_json(db, a.json)
        print(f"export: DB → {a.json} ({len(tm)}개)")
    else:
        gs = all_guidelines(db)
        print(f"가이드라인 {len(gs)}개:")
        for g in sorted(gs, key=lambda x: x["_id"]):
            print(f"  {g['_id']:>14}  thresh={g.get('name_thresh')}  status={g.get('status')}  "
                  f"n_labels={g.get('n_labels','-')}  P={g.get('precision','-')}")
