"""엔진 데이터 계약 — 순수 dataclass (I/O·상태 없음)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractTarget:
    keyword: str
    uid: str = ""
    name: str = ""
    sku: str = ""
    brand: str = ""
    context: str = ""

    def key(self) -> str:
        """멱등 재개용 안정 식별자. uid 우선, 없으면 keyword."""
        return self.uid or self.keyword


@dataclass
class EngineConfig:
    model: str = "gpt-4o-mini"
    sources: dict = field(
        default_factory=lambda: {"blog": True, "danawa": False, "youtube": False})
    lexicon_version: str = "v1"
    retries: int = 3
    execution: str = "sync"   # "sync"(즉시) | "batch"(OpenAI Batch·비동기·50%↓)


@dataclass
class InsightResult:
    target_uid: str
    keyword: str
    block: dict | None
    run_meta: dict
    cost_usd: float = 0.0
    dropped: int = 0
    error: str = ""
