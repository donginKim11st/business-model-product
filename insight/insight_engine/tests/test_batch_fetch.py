import json, os
import sys; sys.path.insert(0, "db")
import run_insight_batch_openai as orch
import naver_review_geo as nrg


def _write(run_dir):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "staging.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"pkg_uid": "p1", "ctlg_no": "C1", "kw": "kw",
                            "items": [{"title": "좋아요", "desc": "쿠션 훌륭"}]}) + "\n")
    orch.write_manifest(run_dir, {"batch_run_id": os.path.basename(run_dir),
                                  "model": "gpt-4o-mini", "batch_ids": ["b1"],
                                  "staging_path": os.path.join(run_dir, "staging.jsonl")})


def _empty_instance(cls):
    # context/aspect는 모든 필드가 필수(List는 빈 배열, str은 "", bool은 False로 채운
    # "내용 없음" 유효 인스턴스) — test_batch_openai_assemble.py와 동일 패턴.
    kwargs = {}
    for name, info in cls.model_fields.items():
        if info.annotation is bool:
            kwargs[name] = False
        elif info.annotation is str:
            kwargs[name] = ""
        else:
            kwargs[name] = []
    return cls(**kwargs)


def _out_line(ctlg, key):
    cls = orch.bo.SCHEMAS[key][0]
    inst = cls(faqs=[]) if key == "sourced" else _empty_instance(cls)
    return {"custom_id": f"{ctlg}|{key}",
            "response": {"body": {"choices": [{"message": {"content": inst.model_dump_json()}}]}}}


def test_fetch_assembles_and_updates(tmp_path, monkeypatch):
    run_dir = str(tmp_path / "r1"); _write(run_dir)
    out_bytes = ("\n".join(json.dumps(_out_line("C1", k)) for k in ("sourced", "context", "aspect"))).encode()

    class FakeBatch:
        status = "completed"; output_file_id = "of1"
    class FakeBatches:
        def retrieve(self, i): return FakeBatch()
    class FakeContent:
        def __init__(self, b): self._b = b
        def read(self): return self._b
    class FakeFiles:
        def content(self, i): return FakeContent(out_bytes)
    class FakeClient:
        batches = FakeBatches(); files = FakeFiles()

    updated = {}
    class FakeCol:
        def find_one(self, q, *a, **k): return {"_id": "p1"}  # insight 없음(멱등 통과)
        def update_one(self, q, u, array_filters=None):
            updated["u"] = u
    class FakeDB: products = FakeCol()

    res = orch.fetch(FakeDB(), FakeClient(), run_dir)
    assert res["loaded"] == 1
    assert "catalogs.$[c].insight" in updated["u"]["$set"]
    ins = updated["u"]["$set"]["catalogs.$[c].insight"]
    assert ins["run_meta"]["execution"] == "openai_batch"


def test_fetch_int_ctlg_no_roundtrip(tmp_path, monkeypatch):
    # 실데이터 ctlg_no는 int. staging은 str키로 매칭되고, Mongo 업데이트는 원래 int로 나가야 함.
    run_dir = str(tmp_path / "rint"); os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "staging.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"pkg_uid": "p1", "ctlg_no": 12345, "kw": "kw",
                            "items": [{"title": "좋아요", "desc": "쿠션 훌륭"}]}) + "\n")
    orch.write_manifest(run_dir, {"batch_run_id": "rint", "model": "gpt-4o-mini",
                                  "batch_ids": ["b1"],
                                  "staging_path": os.path.join(run_dir, "staging.jsonl")})
    # custom_id는 str(12345)로 나감
    out_bytes = ("\n".join(json.dumps(_out_line("12345", k))
                           for k in ("sourced", "context", "aspect"))).encode()

    class FakeBatch:
        status = "completed"; output_file_id = "of1"
    class FakeBatches:
        def retrieve(self, i): return FakeBatch()
    class FakeContent:
        def __init__(self, b): self._b = b
        def read(self): return self._b
    class FakeFiles:
        def content(self, i): return FakeContent(out_bytes)
    class FakeClient:
        batches = FakeBatches(); files = FakeFiles()

    filters = {}
    class FakeCol:
        def find_one(self, q, *a, **k):
            filters["find"] = q.get("catalogs.ctlg_no")
            return {"_id": "p1"}
        def update_one(self, q, u, array_filters=None):
            filters["update"] = array_filters[0]["c.ctlg_no"]
    class FakeDB: products = FakeCol()

    res = orch.fetch(FakeDB(), FakeClient(), run_dir)
    assert res["loaded"] == 1
    # 원래 int 타입으로 Mongo 매칭돼야 함(str 아님)
    assert filters["find"] == 12345 and filters["update"] == 12345


def test_fetch_skips_existing_insight(tmp_path, monkeypatch):
    run_dir = str(tmp_path / "r2"); _write(run_dir)
    out_bytes = ("\n".join(json.dumps(_out_line("C1", k)) for k in ("sourced", "context", "aspect"))).encode()

    class FakeBatch:
        status = "completed"; output_file_id = "of1"
    class FakeBatches:
        def retrieve(self, i): return FakeBatch()
    class FakeContent:
        def __init__(self, b): self._b = b
        def read(self): return self._b
    class FakeFiles:
        def content(self, i): return FakeContent(out_bytes)
    class FakeClient:
        batches = FakeBatches(); files = FakeFiles()

    update_calls = []
    class FakeCol:
        def find_one(self, q, *a, **k):
            # 이미 insight 있는 카탈로그(멱등 skip 대상)
            return {"_id": "p1", "catalogs": [{"insight": {"dims": [1]}}]}
        def update_one(self, q, u, array_filters=None):
            update_calls.append((q, u))
    class FakeDB: products = FakeCol()

    res = orch.fetch(FakeDB(), FakeClient(), run_dir)
    assert res["skipped"] == 1
    assert res["loaded"] == 0
    assert update_calls == []
