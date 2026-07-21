"""단건 동기 래퍼 — 잡 큐 위 얇은 편의 함수."""
import tempfile

from insight_engine import jobs, engine
from insight_engine.types import InsightResult


def extract_one(target, cfg, *, extract=engine.extract_insight,
                llm=None, creds=None) -> InsightResult:
    captured = {}
    def wrap(t, c, *, llm=None, creds=None):
        r = extract(t, c, llm=llm, creds=creds)
        captured["r"] = r
        return r
    with tempfile.NamedTemporaryFile(suffix=".jsonl") as tf:
        jobs.submit([target], cfg, jobs.JobStore(tf.name),
                    extract=wrap, llm=llm, creds=creds)
    return captured["r"]
