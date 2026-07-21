"""잡 큐 — JSONL 백엔드, 재개·멱등, 쿼터 인지 동기 드레인."""
import json
import os
from dataclasses import asdict

import run_batch
from insight_engine import engine
from insight_engine.types import InsightResult

_JOBS: dict = {}


class JobStore:
    def __init__(self, path: str):
        self.path = path

    def done_keys(self) -> set:
        if not os.path.exists(self.path):
            return set()
        keys = set()
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    keys.add(json.loads(line)["target_uid"])
        return keys

    def append(self, result: InsightResult) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")


def _is_quota(r: InsightResult) -> bool:
    return r.block is None and "quota" in (r.error or "").lower()


def submit(targets, cfg, store, *, extract=engine.extract_insight,
           llm=None, creds=None) -> str:
    job_id = f"job-{len(_JOBS) + 1}"
    done = store.done_keys()
    state = {"job_id": job_id, "total": len(targets), "done": 0,
             "empty": 0, "errors": 0, "quota_paused": False, "cost_usd": 0.0}
    _JOBS[job_id] = state
    baseline = run_batch.usd()  # 프로세스 누적 비용 기준선 — 잡별 델타 계산용

    for t in targets:
        if t.key() in done:
            continue
        r = extract(t, cfg, llm=llm, creds=creds)
        if _is_quota(r):
            state["quota_paused"] = True
            break
        store.append(r)
        state["done"] += 1
        state["cost_usd"] = max(0.0, r.cost_usd - baseline)
        if r.error:
            state["errors"] += 1
        elif r.block is None:
            state["empty"] += 1
    return job_id


def get(job_id: str) -> dict:
    return dict(_JOBS[job_id])
