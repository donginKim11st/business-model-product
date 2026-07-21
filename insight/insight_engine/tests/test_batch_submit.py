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
