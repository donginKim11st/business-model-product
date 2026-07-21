import insight_engine.router as router
from insight_engine import jobs
from insight_engine.types import ExtractTarget, EngineConfig, InsightResult


def _fake_extract(t, c, *, llm=None, creds=None):
    return InsightResult(t.key(), t.keyword, {"strengths": []}, {"model": c.model}, cost_usd=0.0)


class _FakeFiles:
    def create(self, file, purpose):
        assert purpose == "batch"
        return type("F", (), {"id": "f1"})()


class _FakeBatches:
    def create(self, **k):
        return type("B", (), {"id": "b1"})()


class _FakeClient:
    files = _FakeFiles()
    batches = _FakeBatches()


def test_submit_sync_delegates_to_jobs(tmp_path):
    store = jobs.JobStore(str(tmp_path / "j.jsonl"))
    res = router.submit([ExtractTarget(keyword="a", uid="1")],
                        EngineConfig(execution="sync"),
                        sync_store=store, extract=_fake_extract)
    assert res["mode"] == "sync" and "job_id" in res
    assert res["state"]["done"] == 1


def test_submit_defaults_to_sync():
    # execution 기본값 확인(EngineConfig 기본 = sync)
    assert EngineConfig().execution == "sync"


def test_submit_batch_builds_and_submits(monkeypatch):
    monkeypatch.setattr(router.run_batch, "collect",
                        lambda kw, nid, ns, **k: [{"title": "좋아요", "desc": "쿠션 훌륭"}])
    res = router.submit([ExtractTarget(keyword="a", uid="C1")],
                        EngineConfig(execution="batch"),
                        client=_FakeClient(), creds={"nid": "x", "nsec": "y"})
    assert res["mode"] == "batch"
    assert res["batch_ids"] == ["b1"]
    assert res["request_count"] == 3          # 1 SKU × 3콜
    assert res["staging"][0]["ctlg_no"] == "C1"


def test_submit_batch_skips_empty_and_quota(monkeypatch):
    def collect(kw, nid, ns, **k):
        if kw == "q":
            raise router.run_batch.QuotaStop("quota")
        if kw == "empty":
            return []
        return [{"title": "x", "desc": "y"}]
    monkeypatch.setattr(router.run_batch, "collect", collect)
    res = router.submit([ExtractTarget(keyword="q", uid="1"),
                         ExtractTarget(keyword="empty", uid="2"),
                         ExtractTarget(keyword="a", uid="3")],
                        EngineConfig(execution="batch"),
                        client=_FakeClient(), creds={"nid": "x", "nsec": "y"})
    assert res["request_count"] == 3          # "a" 만
    assert len(res["staging"]) == 1
