import insight_engine.engine as engine
from insight_engine.types import ExtractTarget, EngineConfig


def test_extract_insight_stamps_run_meta_and_block(monkeypatch):
    monkeypatch.setattr(engine.run_batch, "collect",
                        lambda kw, nid, nsec, **k: [{"title": "좋아요", "desc": "발볼 넉넉"}])
    monkeypatch.setattr(engine.run_batch, "extract_full",
                        lambda kw, items, llm: {"faqs": [], "strengths": ["발볼 넉넉"]})
    monkeypatch.setattr(engine.run_batch, "usd", lambda: 0.0038)

    r = engine.extract_insight(
        ExtractTarget(keyword="아식스 젤카야노", uid="u1"),
        EngineConfig(model="gpt-4o-mini"),
        llm=object(), creds={"nid": "x", "nsec": "y"})

    assert r.target_uid == "u1"
    assert r.block == {"faqs": [], "strengths": ["발볼 넉넉"]}
    assert r.run_meta["model"] == "gpt-4o-mini"
    assert "prompt_version" in r.run_meta
    assert r.cost_usd == 0.0038
    assert r.error == ""


def test_extract_insight_empty_items_returns_block_none_with_run_meta(monkeypatch):
    monkeypatch.setattr(engine.run_batch, "collect", lambda kw, nid, nsec, **k: [])
    monkeypatch.setattr(engine.run_batch, "extract_full", lambda kw, items, llm: None)
    monkeypatch.setattr(engine.run_batch, "usd", lambda: 0.0)

    r = engine.extract_insight(ExtractTarget(keyword="없는상품", uid="u2"),
                               EngineConfig(), llm=object(), creds={"nid": "x", "nsec": "y"})
    assert r.block is None
    assert r.run_meta["engine_version"]
    assert r.error == ""


def test_extract_insight_quota_stop_sets_error(monkeypatch):
    def boom(kw, nid, nsec, **k):
        raise engine.run_batch.QuotaStop("quota")
    monkeypatch.setattr(engine.run_batch, "collect", boom)
    monkeypatch.setattr(engine.run_batch, "usd", lambda: 0.0)

    r = engine.extract_insight(ExtractTarget(keyword="x", uid="u3"),
                               EngineConfig(), llm=object(), creds={"nid": "a", "nsec": "b"})
    assert r.block is None
    assert r.error == "quota"
