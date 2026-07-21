"""재현성 — 프롬프트/모델/사전 버전을 run_meta로 고정."""
import hashlib
from datetime import datetime, timezone

import naver_review_geo as nrg
from insight_engine.types import EngineConfig

ENGINE_VERSION = "0.1.0"


def prompt_version() -> str:
    combined = (nrg.EXTRACT_SOURCED_PROMPT + nrg.EXTRACT_CONTEXT_PROMPT
                + nrg.EXTRACT_ASPECT_VERDICT_PROMPT).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:12]


def build_run_meta(cfg: EngineConfig) -> dict:
    return {
        "engine_version": ENGINE_VERSION,
        "prompt_version": prompt_version(),
        "model": cfg.model,
        "lexicon_version": cfg.lexicon_version,
        "source_config": dict(cfg.sources),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
