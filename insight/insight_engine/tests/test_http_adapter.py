import insight_engine.http_adapter as ha
from insight_engine.types import InsightResult


def test_post_extract_returns_result(monkeypatch):
    monkeypatch.setattr(ha.sync, "extract_one",
                        lambda t, c, **k: InsightResult(t.key(), t.keyword,
                                                        {"strengths": ["a"]}, {"model": c.model}))
    code, body = ha.route("POST", "/extract", {"keyword": "젤카야노", "uid": "u1"})
    assert code == 200
    assert body["result"]["keyword"] == "젤카야노"


def test_get_jobs_unknown_id_404():
    code, body = ha.route("GET", "/jobs/nope", {})
    assert code == 404


def test_get_metrics_shape(monkeypatch):
    monkeypatch.setattr(ha.jobs, "_JOBS",
                        {"j1": {"total": 2, "done": 2, "empty": 0, "errors": 0,
                                "cost_usd": 0.01, "quota_paused": False}}, raising=False)
    code, body = ha.route("GET", "/metrics", {})
    assert code == 200
    assert "snapshot" in body and "alerts" in body
    assert body["snapshot"]["done"] == 2


def test_resolve_bind_defaults_to_all_interfaces(monkeypatch):
    monkeypatch.delenv("INSIGHT_HTTP_HOST", raising=False)
    monkeypatch.delenv("INSIGHT_HTTP_PORT", raising=False)
    assert ha.resolve_bind() == ("0.0.0.0", 8770)


def test_resolve_bind_env_override(monkeypatch):
    monkeypatch.setenv("INSIGHT_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("INSIGHT_HTTP_PORT", "9001")
    assert ha.resolve_bind() == ("127.0.0.1", 9001)


def test_resolve_bind_explicit_args_win(monkeypatch):
    monkeypatch.setenv("INSIGHT_HTTP_HOST", "127.0.0.1")
    assert ha.resolve_bind("0.0.0.0", 8080) == ("0.0.0.0", 8080)
