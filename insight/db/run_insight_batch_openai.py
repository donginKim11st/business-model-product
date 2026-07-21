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

    return _upload_and_submit(client, all_lines, model, run_dir, staging_path)


def _upload_and_submit(client, all_lines, model, run_dir, staging_path,
                       max_bytes=180_000_000, max_count=50_000):
    """요청 라인들을 OpenAI Batch 파일 한도(200MB/50k)에 맞춰 바이트 청킹 →
    메모리(BytesIO) 업로드 → 배치 생성 → manifest 기록. 디스크 temp 없음."""
    import io
    chunks = bo.chunk_by_size(all_lines, max_bytes=max_bytes, max_count=max_count)
    batch_ids, chunk_meta = [], []
    for chunk in chunks:
        data = ("\n".join(json.dumps(l, ensure_ascii=False) for l in chunk)).encode("utf-8")
        buf = io.BytesIO(data); buf.name = "requests.jsonl"
        up = client.files.create(file=buf, purpose="batch")
        b = client.batches.create(input_file_id=up.id, endpoint="/v1/chat/completions",
                                   completion_window="24h")
        batch_ids.append(b.id)
        chunk_meta.append({"batch_id": b.id, "file_id": up.id, "n": len(chunk)})

    manifest = {"batch_run_id": os.path.basename(run_dir), "created_at": now_iso(),
                "model": model, "batch_ids": batch_ids, "staging_path": staging_path,
                "request_count": len(all_lines), "chunks": chunk_meta, "status": "submitted"}
    write_manifest(run_dir, manifest)
    return manifest


def submit_from_staging(client, run_dir, model=None):
    """이미 크롤된 staging.jsonl 로 재크롤 없이 배치만 (재)제출. 파일 초과 실패 후 재개용."""
    staging_path = os.path.join(run_dir, "staging.jsonl")
    if model is None:
        mpath = os.path.join(run_dir, "manifest.json")
        model = (read_manifest(run_dir).get("model") if os.path.exists(mpath) else None) \
                or os.environ.get("INSIGHT_MODEL", "gpt-4o-mini")
    lines = []
    with open(staging_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("items"):
                lines.extend(bo.build_request_lines(rec["ctlg_no"], rec["kw"], rec["items"], model))
    return _upload_and_submit(client, lines, model, run_dir, staging_path)
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
                # custom_id는 str이므로 staging도 str 키로 매칭(원래 타입은 rec 안에 보존).
                out[str(rec["ctlg_no"])] = rec
    return out


def fetch(db, client, run_dir):
    m = read_manifest(run_dir)
    staging = load_staging(run_dir)
    parsed = []
    pending = 0
    malformed = 0
    for bid in m["batch_ids"]:
        b = client.batches.retrieve(bid)
        if b.status != "completed":
            pending += 1
            continue
        raw = client.files.content(b.output_file_id).read()
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = bo.parse_output_line(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                item = None
            if item is None:
                malformed += 1      # 잘린/실패 응답 — 이 SKU만 건너뜀
            else:
                parsed.append(item)
    grouped = bo.regroup_by_sku(parsed)
    loaded = skipped = 0
    for ctlg, trio in grouped.items():
        rec = staging.get(ctlg)
        if not rec:
            continue
        # 멱등: 이미 insight 있으면 skip. Mongo 매칭은 원래 타입(int 가능) 유지.
        orig_ctlg = rec["ctlg_no"]
        doc = db.products.find_one({"_id": rec["pkg_uid"], "catalogs.ctlg_no": orig_ctlg},
                                   {"catalogs.$": 1})
        existing = ((doc or {}).get("catalogs") or [{}])[0].get("insight") if doc else None
        if existing:
            skipped += 1
            continue
        ins = bo.assemble_insight(rec["items"], trio, m["model"])
        db.products.update_one({"_id": rec["pkg_uid"]},
                               {"$set": {"catalogs.$[c].insight": ins}},
                               array_filters=[{"c.ctlg_no": orig_ctlg}])
        loaded += 1
    return {"loaded": loaded, "skipped": skipped, "pending_batches": pending,
            "malformed": malformed}


# mini 정가(1M): in $0.15 / out $0.60. batch = 50%. 요청당 평균 토큰은 보수적 추정.
_AVG_IN_TOK = 4000   # snippets 포함 프롬프트 평균(보수적)
_AVG_OUT_TOK = 900


def estimate_cost(request_count, model="gpt-4o-mini"):
    price = {"gpt-4o-mini": (0.15, 0.60)}.get(model, (0.15, 0.60))
    usd_full = request_count * (_AVG_IN_TOK / 1e6 * price[0] + _AVG_OUT_TOK / 1e6 * price[1])
    usd = usd_full * 0.5  # Batch API 50% 할인
    return {"request_count": request_count, "model": model, "usd": round(usd, 2),
            "krw": round(usd * 1380), "discounted": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--resubmit", action="store_true",
                    help="재크롤 없이 기존 run-dir 의 staging.jsonl 로 배치만 재제출")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default=os.environ.get("INSIGHT_MODEL", "gpt-4o-mini"))
    ap.add_argument("--run-dir", default=os.path.join(HERE, "insight_engine_batch", "run"))
    ap.add_argument("--yes", action="store_true", help="비용 확인 프롬프트 생략")
    args = ap.parse_args()

    from openai import OpenAI
    client = OpenAI()
    dbname = os.environ.get("INSIGHTS_DB", "insights")
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[dbname]

    if args.submit:
        nid = os.environ["NAVER_CLIENT_ID"]; nsec = os.environ["NAVER_CLIENT_SECRET"]
        q = build_queue(db, args.limit)
        est = estimate_cost(len(q) * 3, args.model)
        print(f"[대상 DB={dbname}] 미처리 SKU {len(q)} × 3콜 = {est['request_count']}요청 · "
              f"예상 ≈ ${est['usd']} (≈₩{est['krw']}, Batch 50%할인 반영)")
        if not args.yes and input("진행? [y/N] ").strip().lower() != "y":
            print("취소"); return
        m = submit(db, client, args.run_dir, nid, nsec, args.model, args.limit)
        print(f"제출완료 · 배치 {len(m['batch_ids'])}개 · 요청 {m['request_count']} · run_dir={args.run_dir}")
    elif args.resubmit:
        staging = os.path.join(args.run_dir, "staging.jsonl")
        n = sum(1 for _ in open(staging, encoding="utf-8")) if os.path.exists(staging) else 0
        est = estimate_cost(n * 3, args.model)
        print(f"[재제출] staging {n} SKU × 3콜 = {est['request_count']}요청 · "
              f"예상 ≈ ${est['usd']} (≈₩{est['krw']}) · run_dir={args.run_dir}")
        if not args.yes and input("진행? [y/N] ").strip().lower() != "y":
            print("취소"); return
        m = submit_from_staging(client, args.run_dir, args.model)
        print(f"재제출완료 · 배치 {len(m['batch_ids'])}개 · 요청 {m['request_count']}")
    elif args.status:
        print(json.dumps(status(client, args.run_dir), ensure_ascii=False, indent=2))
    elif args.fetch:
        print(f"[대상 DB={dbname}] FETCH")
        print(json.dumps(fetch(db, client, args.run_dir), ensure_ascii=False))
    else:
        ap.error("--submit | --status | --fetch 중 하나")


if __name__ == "__main__":
    main()
