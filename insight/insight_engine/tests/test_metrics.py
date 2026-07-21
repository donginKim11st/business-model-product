from insight_engine import metrics


def test_snapshot_aggregates_multiple_jobs():
    states = [
        {"total": 10, "done": 10, "empty": 1, "errors": 0, "cost_usd": 0.04, "quota_paused": False},
        {"total": 10, "done": 4, "empty": 0, "errors": 2, "cost_usd": 0.02, "quota_paused": True},
    ]
    snap = metrics.snapshot(states)
    assert snap["total"] == 20 and snap["done"] == 14 and snap["errors"] == 2
    assert abs(snap["cost_usd"] - 0.06) < 1e-9
    assert snap["quota_paused_jobs"] == 1
    assert abs(snap["error_rate"] - (2 / 14)) < 1e-9


def test_alerts_flags_high_error_rate_and_quota():
    snap = {"done": 10, "errors": 5, "error_rate": 0.5, "quota_paused_jobs": 1}
    a = metrics.alerts(snap, error_rate_max=0.2)
    assert any("실패율" in m for m in a)
    assert any("쿼터" in m for m in a)


def test_alerts_empty_when_healthy():
    snap = {"done": 100, "errors": 1, "error_rate": 0.01, "quota_paused_jobs": 0}
    assert metrics.alerts(snap) == []
