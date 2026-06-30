#!/usr/bin/env python3
"""파이프라인 진행률 집계 — n8n 이 '몇 개 중 몇 개'를 표시할 수 있도록 JSON 반환.

정형(structured)   : done = Mongo 적재 카탈로그(SKU) 수(n_catalogs 합).
                     total = Oracle REG 전체 카탈로그 수(1일 캐시; ORA 없으면 캐시/폴백).
비정형(unstructured): total = 적재 SKU 수, done = 인사이트 채워진 SKU 수(insight.dims 비어있지 않음).
유튜브(youtube)     : total = variant 제외 product 수, done = youtube.status=='done'.

  MONGO_URI=.. INSIGHTS_DB=insights_demo [ORA_USER/ORA_PW/SP_REG ..] \
    python3 db/pipeline_progress.py [--stage all|structured|unstructured|youtube]

출력(예): {"structured":{"total":37034,"done":12480,"remaining":24554}, ...}
"""
import os
import sys
import json
import time
import argparse
from pymongo import MongoClient

STRUCT_CACHE = "/tmp/pipeline_struct_total.json"
CACHE_TTL = 86400  # 정형 분모(Oracle count) 캐시 수명 1일


def loaded_skus(db):
    """Mongo 적재 카탈로그(SKU) 수 = catalogs 보유 패키지의 n_catalogs 합."""
    r = next(db.products.aggregate([
        {"$match": {"n_catalogs": {"$exists": True}}},
        {"$group": {"_id": None, "s": {"$sum": "$n_catalogs"}}},
    ]), None)
    return (r or {}).get("s", 0)


def unstructured_counts(db):
    """적재 SKU 대비 인사이트 완료(insight.dims 비어있지 않음) 수."""
    r = next(db.products.aggregate([
        {"$unwind": "$catalogs"},
        {"$match": {"catalogs.ctlg_no": {"$ne": None}}},
        {"$group": {"_id": None,
                    "total": {"$sum": 1},
                    "done": {"$sum": {"$cond": [
                        {"$gt": [{"$size": {"$ifNull": ["$catalogs.insight.dims", []]}}, 0]},
                        1, 0]}}}},
    ]), None) or {}
    t, d = r.get("total", 0), r.get("done", 0)
    return {"total": t, "done": d, "remaining": max(0, t - d)}


def youtube_counts(db):
    r = next(db.products.aggregate([
        {"$match": {"type": {"$ne": "variant"}}},
        {"$group": {"_id": None,
                    "total": {"$sum": 1},
                    "done": {"$sum": {"$cond": [{"$eq": ["$youtube.status", "done"]}, 1, 0]}},
                    "empty": {"$sum": {"$cond": [{"$eq": ["$youtube.status", "empty"]}, 1, 0]}},
                    "error": {"$sum": {"$cond": [{"$eq": ["$youtube.status", "error"]}, 1, 0]}}}},
    ]), None) or {}
    t, d = r.get("total", 0), r.get("done", 0)
    return {"total": t, "done": d, "remaining": max(0, t - d),
            "empty": r.get("empty", 0), "error": r.get("error", 0)}


def identity_counts(db):
    """identity 정형 합류 진행률. category-agnostic — 모든 product 가 적격(부분집합 스코핑 ✗).
    done = status ∈ {done, empty}(터미널), joined = 실제 팩트 합류(done), remaining = 미처리.
    추출기 없는 카테고리는 매칭 안 돼 remaining 에 남음(정확한 backlog — 추출기 확장 시 감소)."""
    r = next(db.products.aggregate([
        {"$match": {"type": {"$ne": "variant"}}},
        {"$group": {"_id": None,
                    "total": {"$sum": 1},
                    "done": {"$sum": {"$cond": [{"$in": ["$identity.status", ["done", "empty"]]}, 1, 0]}},
                    "joined": {"$sum": {"$cond": [{"$eq": ["$identity.status", "done"]}, 1, 0]}},
                    "empty": {"$sum": {"$cond": [{"$eq": ["$identity.status", "empty"]}, 1, 0]}},
                    "error": {"$sum": {"$cond": [{"$eq": ["$identity.status", "error"]}, 1, 0]}}}},
    ]), None) or {}
    t, d = r.get("total", 0), r.get("done", 0)
    return {"total": t, "done": d, "remaining": max(0, t - d),
            "joined": r.get("joined", 0), "empty": r.get("empty", 0), "error": r.get("error", 0)}


def _oracle_struct_total():
    """Oracle REG 전체 카탈로그 수(분모). 실패 시 예외."""
    import oracledb
    oracledb.init_oracle_client(lib_dir=os.environ.get("ORA_LIB", "/opt/homebrew/lib"))
    conn = oracledb.connect(
        user=os.environ["ORA_USER"], password=os.environ["ORA_PW"],
        dsn=oracledb.makedsn(os.environ.get("ORA_HOST", "172.18.176.69"),
                             int(os.environ.get("ORA_PORT", "1528")),
                             sid=os.environ.get("ORA_SID", "TMALL")))
    cur = conn.cursor()
    reg = ",".join(f"'{r.strip()}'" for r in os.environ.get("SP_REG", "1002").split(",") if r.strip())
    cur.execute(f"SELECT COUNT(*) FROM pd_ctlg "
                f"WHERE REG_TYP_CD IN ({reg}) AND DISP_MODEL_NM IS NOT NULL")
    v = int(cur.fetchone()[0])
    conn.close()
    return v


def structured_total(fallback):
    """정형 분모 = Oracle 카탈로그 수. 1일 캐시 → ORA 있으면 갱신 → 폴백(적재 SKU)."""
    try:
        c = json.load(open(STRUCT_CACHE))
        if time.time() - c["ts"] < CACHE_TTL:
            return c["value"]
    except Exception:
        pass
    if os.environ.get("ORA_USER") and os.environ.get("ORA_PW"):
        try:
            v = _oracle_struct_total()
            json.dump({"value": v, "ts": time.time()}, open(STRUCT_CACHE, "w"))
            return v
        except Exception as e:
            print(f"[progress] Oracle count 실패: {e}", file=sys.stderr)
    try:
        return json.load(open(STRUCT_CACHE))["value"]  # 만료됐어도 마지막 값 사용
    except Exception:
        return fallback  # 캐시도 없으면 적재 SKU 로 폴백(remaining 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=["all", "structured", "unstructured", "youtube", "identity"])
    args = ap.parse_args()

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights_demo")]

    out = {}
    skus = loaded_skus(db)
    if args.stage in ("all", "structured"):
        total = structured_total(skus)
        out["structured"] = {"total": total, "done": skus, "remaining": max(0, total - skus)}
    if args.stage in ("all", "unstructured"):
        out["unstructured"] = unstructured_counts(db)
    if args.stage in ("all", "youtube"):
        out["youtube"] = youtube_counts(db)
    if args.stage in ("all", "identity"):
        out["identity"] = identity_counts(db)

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
