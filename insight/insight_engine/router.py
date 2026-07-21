"""실행 라우팅 — cfg.execution 으로 sync(즉시)/batch(OpenAI Batch) 선택.

하나의 submit() 으로 두 백엔드를 혼용한다. 급한 단건·소량 → sync,
대량·저비용 → batch. batch_openai 는 lazy import(그 경로에서만 cib/Mongo 를
끌어옴) → sync-only 배포는 Mongo 의존 없이 가볍게 유지된다.
"""
import io
import os
import json

import run_batch
from insight_engine import jobs, engine


def _creds(creds):
    if creds:
        return creds["nid"], creds["nsec"]
    return (os.environ.get("NAVER_CLIENT_ID", ""),
            os.environ.get("NAVER_CLIENT_SECRET", ""))


def submit(targets, cfg, *, sync_store=None, client=None, creds=None, extract=None,
           max_bytes=180_000_000, max_count=50_000):
    """cfg.execution 에 따라 분기.
    sync  → jobs.submit 위임 → {"mode":"sync","job_id","state"}
    batch → 크롤·요청빌드·제출 → {"mode":"batch","batch_ids","staging","request_count"}
    (batch staging 영속화는 호출자 몫 — 라우터는 디스크·Mongo 무의존)."""
    if cfg.execution == "batch":
        return _submit_batch(targets, cfg, client, creds, max_bytes, max_count)
    job_id = jobs.submit(targets, cfg, sync_store,
                         extract=extract or engine.extract_insight)
    return {"mode": "sync", "job_id": job_id, "state": jobs.get(job_id)}


def _submit_batch(targets, cfg, client, creds, max_bytes, max_count):
    from insight_engine import batch_openai  # lazy: batch 경로에서만 cib/Mongo 끌어옴
    nid, nsec = _creds(creds)
    staging, lines = [], []
    for t in targets:
        try:
            items = run_batch.collect(t.keyword, nid, nsec, raise_blog_quota=True)
        except run_batch.QuotaStop:
            continue
        except Exception:
            continue
        if not items:
            continue
        ctlg = t.uid or t.keyword
        staging.append({"uid": t.key(), "ctlg_no": ctlg, "kw": t.keyword, "items": items})
        lines.extend(batch_openai.build_request_lines(ctlg, t.keyword, items, cfg.model))

    batch_ids = []
    for chunk in batch_openai.chunk_by_size(lines, max_bytes=max_bytes, max_count=max_count):
        data = ("\n".join(json.dumps(l, ensure_ascii=False) for l in chunk)).encode("utf-8")
        buf = io.BytesIO(data)
        buf.name = "requests.jsonl"
        up = client.files.create(file=buf, purpose="batch")
        b = client.batches.create(input_file_id=up.id, endpoint="/v1/chat/completions",
                                   completion_window="24h")
        batch_ids.append(b.id)

    return {"mode": "batch", "batch_ids": batch_ids, "staging": staging,
            "request_count": len(lines)}
