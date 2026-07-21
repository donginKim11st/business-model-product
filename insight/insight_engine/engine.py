"""추출 코어 파사드 — 기존 run_batch 함수를 재사용하는 얇은 순수 래퍼."""
from __future__ import annotations

import os

import run_batch
from insight_engine.types import ExtractTarget, EngineConfig, InsightResult
from insight_engine import versioning


def _creds(creds: dict | None) -> tuple[str, str]:
    if creds:
        return creds["nid"], creds["nsec"]
    return (os.environ.get("NAVER_CLIENT_ID", ""),
            os.environ.get("NAVER_CLIENT_SECRET", ""))


def extract_insight(target: ExtractTarget, cfg: EngineConfig, *,
                    llm=None, creds: dict | None = None) -> InsightResult:
    run_meta = versioning.build_run_meta(cfg)
    nid, nsec = _creds(creds)
    if llm is None:
        os.environ.setdefault("INSIGHT_MODEL", cfg.model)
        llm = run_batch.make_client()

    try:
        items = run_batch.collect(target.keyword, nid, nsec,
                                  use_yt=cfg.sources.get("youtube", False),
                                  raise_blog_quota=True)
    except run_batch.QuotaStop as e:
        return InsightResult(target.key(), target.keyword, None, run_meta,
                             cost_usd=run_batch.usd(), error=str(e) or "quota")

    block = run_batch.extract_full(target.keyword, items, llm)
    return InsightResult(target.key(), target.keyword, block, run_meta,
                         cost_usd=run_batch.usd())
