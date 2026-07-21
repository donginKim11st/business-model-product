#!/usr/bin/env python3
"""비정형 인사이트 OpenAI Batch API 백엔드 — SUBMIT/STATUS/FETCH 오케스트레이터.
staging.jsonl + manifest.json 로 24h/재시작 재개. 크롤=동기(네이버 쿼터), LLM=Batch(50% 할인)."""
import os, sys, json, argparse, tempfile
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
os.environ.setdefault("INSIGHT_MODEL", "gpt-4o-mini")

import run_batch
import catalog_insight_backfill as cib
from insight_engine import batch_openai as bo
from pymongo import MongoClient


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_queue(db, limit=0):
    pkgs = list(db.products.find({"type": "package"}, {"_id": 1, "catalogs": 1}))
    pkgs.sort(key=lambda p: -len(p.get("catalogs") or []))
    q = []
    for p in pkgs:
        for c in p.get("catalogs") or []:
            if not c.get("ctlg_no"):
                continue
            if c.get("insight"):
                continue
            q.append((p["_id"], c["ctlg_no"], c.get("disp")))
    return q[:limit] if limit else q


def write_manifest(run_dir, data):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_manifest(run_dir):
    with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def submit(db, client, run_dir, nid, nsec, model, limit=0, max_per_batch=40000, workers=16):
    os.makedirs(run_dir, exist_ok=True)
    queue = build_queue(db, limit)
    staging_path = os.path.join(run_dir, "staging.jsonl")
    all_lines = []

    def work(task):
        pkg_uid, ctlg, disp = task
        kw = cib.clean(disp)
        try:
            items = run_batch.collect(kw, nid, nsec, raise_blog_quota=True)
        except run_batch.QuotaStop:
            return None
        except Exception:
            return None
        rec = {"pkg_uid": pkg_uid, "ctlg_no": ctlg, "kw": kw, "items": items}
        lines = bo.build_request_lines(ctlg, kw, items, model) if items else []
        return rec, lines

    with open(staging_path, "w", encoding="utf-8") as sf, ThreadPoolExecutor(max_workers=workers) as ex:
        for out in ex.map(work, queue):
            if not out:
                continue
            rec, lines = out
            sf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            all_lines.extend(lines)

    chunks = bo.chunk_requests(all_lines, max_per_batch)
    batch_ids, chunk_meta = [], []
    for i, chunk in enumerate(chunks):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        for l in chunk:
            tmp.write(json.dumps(l, ensure_ascii=False) + "\n")
        tmp.close()
        with open(tmp.name, "rb") as fh:
            up = client.files.create(file=fh, purpose="batch")
        b = client.batches.create(input_file_id=up.id, endpoint="/v1/chat/completions",
                                  completion_window="24h")
        os.unlink(tmp.name)
        batch_ids.append(b.id)
        chunk_meta.append({"batch_id": b.id, "file_id": up.id, "n": len(chunk)})

    manifest = {"batch_run_id": os.path.basename(run_dir), "created_at": now_iso(),
                "model": model, "batch_ids": batch_ids, "staging_path": staging_path,
                "request_count": len(all_lines), "chunks": chunk_meta, "status": "submitted"}
    write_manifest(run_dir, manifest)
    return manifest


def status(client, run_dir):
    m = read_manifest(run_dir)
    rows = []
    for bid in m["batch_ids"]:
        b = client.batches.retrieve(bid)
        rows.append({"batch_id": bid, "status": b.status})
    return {"batch_run_id": m["batch_run_id"], "batches": rows}


def load_staging(run_dir):
    m = read_manifest(run_dir)
    out = {}
    with open(m["staging_path"], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                out[rec["ctlg_no"]] = rec
    return out


def fetch(db, client, run_dir):
    m = read_manifest(run_dir)
    staging = load_staging(run_dir)
    parsed = []
    pending = 0
    for bid in m["batch_ids"]:
        b = client.batches.retrieve(bid)
        if b.status != "completed":
            pending += 1
            continue
        raw = client.files.content(b.output_file_id).read()
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                parsed.append(bo.parse_output_line(json.loads(line)))
    grouped = bo.regroup_by_sku(parsed)
    loaded = skipped = 0
    for ctlg, trio in grouped.items():
        rec = staging.get(ctlg)
        if not rec:
            continue
        # 멱등: 이미 insight 있으면 skip
        doc = db.products.find_one({"_id": rec["pkg_uid"], "catalogs.ctlg_no": ctlg},
                                   {"catalogs.$": 1})
        existing = ((doc or {}).get("catalogs") or [{}])[0].get("insight") if doc else None
        if existing:
            skipped += 1
            continue
        ins = bo.assemble_insight(rec["items"], trio, m["model"])
        db.products.update_one({"_id": rec["pkg_uid"]},
                               {"$set": {"catalogs.$[c].insight": ins}},
                               array_filters=[{"c.ctlg_no": ctlg}])
        loaded += 1
    return {"loaded": loaded, "skipped": skipped, "pending_batches": pending}
