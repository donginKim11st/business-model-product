import json, os, sys

sys.path.insert(0, "db")
import run_insight_batch_openai as sub


def test_manifest_roundtrip(tmp_path):
    sub.write_manifest(str(tmp_path), {"batch_run_id": "r1", "batch_ids": ["b1"]})
    m = sub.read_manifest(str(tmp_path))
    assert m["batch_run_id"] == "r1" and m["batch_ids"] == ["b1"]


def test_build_queue_skips_existing_insight():
    class FakeCol:
        def find(self, *a, **k):
            return [{"_id": "p1", "catalogs": [
                {"ctlg_no": "C1", "disp": "상품1", "insight": {"dims": [1]}},   # 이미 있음 skip
                {"ctlg_no": "C2", "disp": "상품2"},                              # 미처리
                {"ctlg_no": None, "disp": "x"}]}]                                # ctlg 없음 skip
    class FakeDB:
        products = FakeCol()
    q = sub.build_queue(FakeDB(), limit=0)
    assert [(p, c) for p, c, _ in q] == [("p1", "C2")]


def test_submit_pipeline_mocked(tmp_path, monkeypatch):
    monkeypatch.setattr(sub.run_batch, "collect", lambda kw, nid, ns, **k: [{"title": "좋아요", "desc": "쿠션"}])
    class FakeFiles:
        def create(self, file, purpose): return type("F", (), {"id": "file-1"})()
    class FakeBatches:
        def create(self, **k): return type("B", (), {"id": "batch-1"})()
    class FakeClient:
        files = FakeFiles(); batches = FakeBatches()
    class FakeCol:
        def find(self, *a, **k): return [{"_id": "p1", "catalogs": [{"ctlg_no": "C2", "disp": "상품2"}]}]
    class FakeDB: products = FakeCol()
    res = sub.submit(FakeDB(), FakeClient(), str(tmp_path), "nid", "ns", "gpt-4o-mini", limit=0, max_per_batch=40000)
    assert res["batch_ids"] == ["batch-1"]
    assert res["request_count"] == 3           # SKU 1개 × 3스키마
    assert os.path.exists(os.path.join(str(tmp_path), "staging.jsonl"))
    m = sub.read_manifest(str(tmp_path))
    assert m["batch_ids"] == ["batch-1"] and m["model"] == "gpt-4o-mini"


def test_submit_from_staging_no_recrawl(tmp_path, monkeypatch):
    run_dir = str(tmp_path)
    with open(os.path.join(run_dir, "staging.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"pkg_uid": "p1", "ctlg_no": 12345, "kw": "kw",
                            "items": [{"title": "좋아요", "desc": "쿠션"}]}) + "\n")
    # 재크롤하면 안 됨
    def _boom(*a, **k):
        raise AssertionError("submit_from_staging 이 크롤을 호출하면 안 됨")
    monkeypatch.setattr(sub.run_batch, "collect", _boom)

    class FakeFiles:
        def create(self, file, purpose):
            assert purpose == "batch"
            return type("F", (), {"id": "file-1"})()
    class FakeBatches:
        def create(self, **k): return type("B", (), {"id": "batch-1"})()
    class FakeClient:
        files = FakeFiles(); batches = FakeBatches()

    m = sub.submit_from_staging(FakeClient(), run_dir, "gpt-4o-mini")
    assert m["batch_ids"] == ["batch-1"]
    assert m["request_count"] == 3          # 1 SKU × 3콜
    assert m["status"] == "submitted"
