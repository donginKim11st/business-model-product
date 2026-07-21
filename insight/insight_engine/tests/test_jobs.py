import insight_engine.jobs as jobs
from insight_engine.types import ExtractTarget, EngineConfig, InsightResult


def _fake_extract_ok(target, cfg, *, llm=None, creds=None):
    return InsightResult(target.key(), target.keyword,
                         {"strengths": ["x"]}, {"model": cfg.model}, cost_usd=0.001)


def test_submit_processes_all_and_get_reports(tmp_path):
    store = jobs.JobStore(str(tmp_path / "j.jsonl"))
    targets = [ExtractTarget(keyword="a", uid="1"), ExtractTarget(keyword="b", uid="2")]
    jid = jobs.submit(targets, EngineConfig(), store, extract=_fake_extract_ok)
    st = jobs.get(jid)
    assert st["total"] == 2 and st["done"] == 2 and st["errors"] == 0
    assert st["quota_paused"] is False


def test_submit_skips_already_done(tmp_path):
    store = jobs.JobStore(str(tmp_path / "j.jsonl"))
    store.append(InsightResult("1", "a", {"strengths": []}, {}, cost_usd=0.0))
    calls = []
    def spy(target, cfg, *, llm=None, creds=None):
        calls.append(target.key())
        return _fake_extract_ok(target, cfg)
    jobs.submit([ExtractTarget(keyword="a", uid="1"),
                 ExtractTarget(keyword="b", uid="2")],
                EngineConfig(), store, extract=spy)
    assert calls == ["2"]  # uid=1 은 이미 done → skip


def test_submit_quota_pause_stops_remaining(tmp_path):
    store = jobs.JobStore(str(tmp_path / "j.jsonl"))
    seq = iter([
        InsightResult("1", "a", {"s": []}, {}, cost_usd=0.001),
        InsightResult("2", "b", None, {}, error="quota exceeded"),
    ])
    def flaky(target, cfg, *, llm=None, creds=None):
        return next(seq)
    jid = jobs.submit([ExtractTarget(keyword="a", uid="1"),
                       ExtractTarget(keyword="b", uid="2"),
                       ExtractTarget(keyword="c", uid="3")],
                      EngineConfig(), store, extract=flaky)
    st = jobs.get(jid)
    assert st["quota_paused"] is True
    assert st["done"] == 1  # uid=3 은 미처리(중단)


def test_submit_cost_is_per_job_delta(tmp_path, monkeypatch):
    # run_batch.usd()는 프로세스 누적치 — jobs.submit은 baseline을 빼서 잡별 델타로 저장해야 함
    monkeypatch.setattr(jobs.run_batch, "usd", lambda: 5.0)
    store = jobs.JobStore(str(tmp_path / "j.jsonl"))
    seq = iter([
        InsightResult("1", "a", {"s": []}, {}, cost_usd=5.4),
        InsightResult("2", "b", {"s": []}, {}, cost_usd=6.0),
    ])
    def cumulative(target, cfg, *, llm=None, creds=None):
        return next(seq)
    jid = jobs.submit([ExtractTarget(keyword="a", uid="1"),
                       ExtractTarget(keyword="b", uid="2")],
                      EngineConfig(), store, extract=cumulative)
    st = jobs.get(jid)
    assert st["cost_usd"] == 1.0  # 6.0 - baseline(5.0), 누적치 6.0이 아님
