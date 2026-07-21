import insight_engine.sync as sync
from insight_engine.types import ExtractTarget, EngineConfig, InsightResult


def test_extract_one_returns_single_result():
    def fake(target, cfg, *, llm=None, creds=None):
        return InsightResult(target.key(), target.keyword,
                             {"strengths": ["빠름"]}, {"model": cfg.model}, cost_usd=0.002)
    r = sync.extract_one(ExtractTarget(keyword="나이키 페가수스", uid="p1"),
                         EngineConfig(), extract=fake)
    assert isinstance(r, InsightResult)
    assert r.keyword == "나이키 페가수스"
    assert r.block == {"strengths": ["빠름"]}
