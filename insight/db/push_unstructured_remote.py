#!/usr/bin/env python3
"""비정형 인사이트(catalogs[].insight) → 원격 10xtf.aiCatalogUnstructuredAttribute 적재.

로컬 insights_demo.products 의 카탈로그(SKU)별 insight 를 카탈로그 1건=문서 1건으로 펼쳐
원격 공용 DB에 넣는다. _id=ctlg_no 로 upsert 라 재실행 안전(중복 없음, 갱신만).
최상위 필드는 원격 컬렉션 관례(camelCase)를 따르고, insight 내부 구조(dims/faqs/근거)는
소비자가 원형대로 보도록 그대로 둔다.

  REMOTE_URI='mongodb://10xtfUser:...@172.28.112.67:27017,.../10xtf?authSource=10xtf' \
  INSIGHTS_DB=insights_demo python3 db/push_unstructured_remote.py [--dry-run] [--limit N]
"""
import os
import sys
import argparse
from datetime import datetime, timezone

from pymongo import MongoClient, ReplaceOne

BATCH = 200  # insight 문서가 근거 인용까지 담아 커서 낮게


def to_doc(pkg, cat, loaded_at):
    ins = cat["insight"]
    return {
        "_id": cat["ctlg_no"],
        "ctlgNo": cat["ctlg_no"],
        "dispNm": cat.get("disp"),
        "category": pkg.get("category"),
        "pkgId": pkg["_id"],
        "insight": ins,  # dims(대표 속성별 포인트+근거) / faqs / n_sources / fetched_at / source
        "loadedAt": loaded_at,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="적재할 카탈로그 수(0=전체)")
    ap.add_argument("--dry-run", action="store_true", help="건수·샘플 1건만 출력")
    args = ap.parse_args()

    src = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights_demo")]

    dst = None
    if not args.dry_run:
        remote_uri = os.environ.get("REMOTE_URI")
        if not remote_uri:
            sys.exit("REMOTE_URI 환경변수가 필요합니다 (10xtf 접속 문자열)")
        dst = MongoClient(remote_uri, serverSelectionTimeoutMS=10000)["10xtf"]
        dst.client.admin.command("ping")

    loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor = src.products.find({"catalogs.insight": {"$exists": True}},
                               {"category": 1, "catalogs": 1})
    total, buf, shown = 0, [], False
    for pkg in cursor:
        for cat in pkg.get("catalogs") or []:
            if not cat.get("insight") or not cat.get("ctlg_no"):
                continue
            doc = to_doc(pkg, cat, loaded_at)
            if args.dry_run and not shown:
                import json
                print("샘플 문서:", json.dumps(doc, ensure_ascii=False, default=str)[:1500])
                shown = True
            total += 1
            if not args.dry_run:
                buf.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
                if len(buf) >= BATCH:
                    dst.aiCatalogUnstructuredAttribute.bulk_write(buf, ordered=False)
                    buf = []
                    if total % 5000 < BATCH:
                        print(f"  {total:,}건 적재", flush=True)
            if args.limit and total >= args.limit:
                break
        if args.limit and total >= args.limit:
            break
    if buf:
        dst.aiCatalogUnstructuredAttribute.bulk_write(buf, ordered=False)
    print(f"{'대상' if args.dry_run else '적재 완료'} {total:,}건 → 10xtf.aiCatalogUnstructuredAttribute")
    if dst is not None:
        print("원격 현재 건수:", dst.aiCatalogUnstructuredAttribute.estimated_document_count())


if __name__ == "__main__":
    main()
