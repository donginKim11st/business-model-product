# 비정형 인사이트 OpenAI Batch API 백엔드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 비정형 인사이트 추출을 OpenAI Batch API(비동기·50% 할인)로 실행하는 3단계 백엔드(SUBMIT/STATUS/FETCH)를 `insight_engine` 위에 추가한다.

**Architecture:** 순수 로직은 `insight_engine/batch_openai.py`(요청빌드·스키마변환·출력파싱·조립), 오케스트레이션은 `db/run_insight_batch_openai.py`(크롤·제출·폴링·Mongo적재). 기존 `naver_review_geo`/`run_batch`/`catalog_insight_backfill` 무수정, 함수 재사용. 상태는 로컬 staging jsonl + manifest.json(durable, 24h/재시작 재개).

**Tech Stack:** Python 3, openai SDK(Batch API + `type_to_response_format_param`), pydantic(기존 스키마 재사용), pymongo, pytest.

## Global Constraints

- 신규 서드파티 의존성 추가 금지 — openai·pydantic·pymongo(기존)만.
- `naver_review_geo.py`·`run_batch.py`·`catalog_insight_backfill.py`의 함수 시그니처·내부 변경 금지(호출·재사용만). 단 `to_insight`는 `catalog_insight_backfill`에서 import.
- 순수 모듈 `batch_openai.py`는 Mongo·OpenAI·파일 I/O·네트워크에 의존하지 않는다(요청 dict·파싱 결과만 다룸). I/O는 오케스트레이터에만.
- 추출 품질은 동기 경로와 동일 — `_build_sourced_snippets`·`build_sourced_block`·`to_insight`를 그대로 재사용.
- 대상 DB=운영 `insights`. FETCH의 Mongo 쓰기 전 대상 DB·컬렉션 확인·보고. SUBMIT 전 N·예상비용(원화) 보고·승인(CLAUDE.md).
- 커밋 메시지에 `Claude-Session` 트레일러 금지. `git add`는 정확한 경로만(작업트리에 무관한 dirty 파일 존재).
- 경로는 `insight/` 기준. 개발은 `insight/`에서, 순수 `insight_engine/` 변경분만 새 repo(github.com/10xtf/insight-engine)로 미러링.

## 재사용하는 기존 인터페이스(실측 시그니처)

- `naver_review_geo._build_sourced_snippets(items) -> (snippets:str, id_map:dict, dropped:int)` — 결정적.
- `naver_review_geo.build_sourced_block(si, ctx, av, id_map, kept) -> dict`.
- `naver_review_geo.SourcedInsights / SourcedContext / SourcedAspectVerdict` (pydantic).
- `naver_review_geo.EXTRACT_SOURCED_PROMPT / EXTRACT_CONTEXT_PROMPT / EXTRACT_ASPECT_VERDICT_PROMPT` — `.format(keyword=, snippets=)`.
- `run_batch.collect(kw, nid, nsecret, ytk=None, use_yt=False, raise_blog_quota=False) -> List[dict]`.
- `catalog_insight_backfill.to_insight(block, n_items, per_dim=3, max_dims=6) -> dict`, `.clean(disp)`, `.now_iso()`.
- `insight_engine.versioning.build_run_meta(cfg) -> dict`, `insight_engine.types.EngineConfig`.
- OpenAI: `client.files.create(file=, purpose="batch")`, `client.batches.create(input_file_id=, endpoint="/v1/chat/completions", completion_window="24h")`, `client.batches.retrieve(id)`, `client.files.content(output_file_id)`.
- 스키마 변환: `from openai.lib._parsing._completions import type_to_response_format_param`.

## 스키마 키 매핑(고정)

```
SCHEMAS = {
  "sourced": (SourcedInsights,     EXTRACT_SOURCED_PROMPT),
  "context": (SourcedContext,      EXTRACT_CONTEXT_PROMPT),
  "aspect":  (SourcedAspectVerdict, EXTRACT_ASPECT_VERDICT_PROMPT),
}
```

---

### Task 1: batch_openai.py — 요청 빌드 · 스키마 변환 · 청킹

**Files:**
- Create: `insight_engine/batch_openai.py`
- Test: `insight_engine/tests/test_batch_openai_build.py`

**Interfaces:**
- Consumes: `naver_review_geo`(스키마·프롬프트·`_build_sourced_snippets`).
- Produces:
  - `SCHEMAS: dict[str, tuple[type, str]]` (위 매핑)
  - `response_format_for(schema_key: str) -> dict` (`type_to_response_format_param`)
  - `build_request_lines(ctlg_no: str, keyword: str, items: list, model: str) -> list[dict]` — SKU 1개 → 3개 요청 dict(custom_id/method/url/body). `|` 포함 ctlg_no는 `ValueError`.
  - `chunk_requests(lines: list[dict], max_per_batch: int = 40000) -> list[list[dict]]`

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_batch_openai_build.py`

```python
import json
import pytest
import naver_review_geo as nrg
from insight_engine import batch_openai as bo

ITEMS = [{"title": "발볼 넉넉하고 좋아요", "desc": "쿠션 훌륭"},
         {"title": "가볍고 편함", "desc": "장거리도 무난"}]


def test_build_request_lines_three_schemas_with_custom_ids():
    lines = bo.build_request_lines("CTLG123", "아식스 젤카야노", ITEMS, "gpt-4o-mini")
    assert len(lines) == 3
    cids = {l["custom_id"] for l in lines}
    assert cids == {"CTLG123|sourced", "CTLG123|context", "CTLG123|aspect"}
    for l in lines:
        assert l["method"] == "POST" and l["url"] == "/v1/chat/completions"
        b = l["body"]
        assert b["model"] == "gpt-4o-mini" and b["temperature"] == 0
        assert b["messages"][0]["role"] == "user"
        assert "아식스 젤카야노" in b["messages"][0]["content"]
        assert b["response_format"]["type"] == "json_schema"


def test_same_snippets_across_three_calls():
    lines = bo.build_request_lines("C1", "kw", ITEMS, "gpt-4o-mini")
    snips = {l["body"]["messages"][0]["content"].split("--- 수집 데이터 ---")[-1] for l in lines}
    # 세 콜의 snippets(수집데이터 부분)가 동일해야 id_map 일관
    assert len(snips) == 1


def test_pipe_in_ctlg_no_raises():
    with pytest.raises(ValueError):
        bo.build_request_lines("C|X", "kw", ITEMS, "gpt-4o-mini")


def test_chunk_requests_splits_over_max():
    lines = [{"custom_id": f"c{i}"} for i in range(95)]
    chunks = bo.chunk_requests(lines, max_per_batch=40)
    assert [len(c) for c in chunks] == [40, 40, 15]
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_batch_openai_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'insight_engine.batch_openai'`

- [ ] **Step 3: 최소 구현** — `insight_engine/batch_openai.py` (Task 1 부분)

```python
"""OpenAI Batch API 백엔드 — 순수 로직(요청빌드·스키마변환·출력파싱·조립). I/O 없음."""
from openai.lib._parsing._completions import type_to_response_format_param

import naver_review_geo as nrg

SCHEMAS = {
    "sourced": (nrg.SourcedInsights, nrg.EXTRACT_SOURCED_PROMPT),
    "context": (nrg.SourcedContext, nrg.EXTRACT_CONTEXT_PROMPT),
    "aspect": (nrg.SourcedAspectVerdict, nrg.EXTRACT_ASPECT_VERDICT_PROMPT),
}


def response_format_for(schema_key: str) -> dict:
    model_cls, _ = SCHEMAS[schema_key]
    return type_to_response_format_param(model_cls)


def build_request_lines(ctlg_no: str, keyword: str, items: list, model: str) -> list:
    if "|" in ctlg_no:
        raise ValueError(f"ctlg_no에 '|' 불가(custom_id 구분자 충돌): {ctlg_no}")
    snippets, _id_map, _dropped = nrg._build_sourced_snippets(items)
    out = []
    for key, (_cls, prompt) in SCHEMAS.items():
        out.append({
            "custom_id": f"{ctlg_no}|{key}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "temperature": 0,
                "messages": [{"role": "user",
                              "content": prompt.format(keyword=keyword, snippets=snippets)}],
                "response_format": response_format_for(key),
            },
        })
    return out


def chunk_requests(lines: list, max_per_batch: int = 40000) -> list:
    return [lines[i:i + max_per_batch] for i in range(0, len(lines), max_per_batch)]
```

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_batch_openai_build.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add insight_engine/batch_openai.py insight_engine/tests/test_batch_openai_build.py
git commit -m "feat(insight-engine): batch_openai 요청빌드·스키마변환·청킹"
```

---

### Task 2: batch_openai.py — 출력 파싱 · SKU 재조립 · 인사이트 조립

**Files:**
- Modify: `insight_engine/batch_openai.py`
- Test: `insight_engine/tests/test_batch_openai_assemble.py`

**Interfaces:**
- Consumes: Task 1의 `SCHEMAS`; `naver_review_geo._build_sourced_snippets/build_sourced_block`; `catalog_insight_backfill.to_insight`; `insight_engine.versioning.build_run_meta`, `insight_engine.types.EngineConfig`.
- Produces:
  - `parse_output_line(line: dict) -> tuple[str, str, object]` — (ctlg_no, schema_key, parsed pydantic). custom_id/스키마로 역파싱.
  - `regroup_by_sku(parsed: list[tuple]) -> dict[str, dict]` — `{ctlg_no: {"sourced":.., "context":.., "aspect":..}}`, 3개 미만은 제외.
  - `assemble_insight(items: list, trio: dict, model: str) -> dict` — id_map 재구성→build_sourced_block→to_insight→run_meta(execution="openai_batch").

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_batch_openai_assemble.py`

```python
import naver_review_geo as nrg
from insight_engine import batch_openai as bo

ITEMS = [{"title": "발볼 넉넉하고 좋아요", "desc": "쿠션 훌륭합니다"},
         {"title": "가볍고 편함", "desc": "장거리도 무난했어요"}]


def _fake_output_line(ctlg, key, model_cls):
    # 스키마의 빈 인스턴스를 JSON으로 (실제 Batch output.body 형태 모사)
    inst = model_cls.model_construct() if hasattr(model_cls, "model_construct") else model_cls()
    return {"custom_id": f"{ctlg}|{key}",
            "response": {"body": {"choices": [{"message": {"content": inst.model_dump_json()}}]}}}


def test_parse_output_line_returns_ctlg_key_model():
    # sourced 스키마: faqs 필수 → 빈 배열로 유효 JSON 구성
    content = nrg.SourcedInsights(faqs=[]).model_dump_json()
    line = {"custom_id": "CTLG9|sourced",
            "response": {"body": {"choices": [{"message": {"content": content}}]}}}
    ctlg, key, parsed = bo.parse_output_line(line)
    assert ctlg == "CTLG9" and key == "sourced"
    assert isinstance(parsed, nrg.SourcedInsights)


def test_regroup_by_sku_drops_incomplete():
    parsed = [("A", "sourced", object()), ("A", "context", object()),
              ("A", "aspect", object()), ("B", "sourced", object())]
    grouped = bo.regroup_by_sku(parsed)
    assert set(grouped) == {"A"}  # B는 2/3 미만 → 제외
    assert set(grouped["A"]) == {"sourced", "context", "aspect"}


def test_assemble_insight_produces_insight_with_run_meta():
    trio = {"sourced": nrg.SourcedInsights(faqs=[]),
            "context": nrg.SourcedContext(),
            "aspect": nrg.SourcedAspectVerdict()}
    ins = bo.assemble_insight(ITEMS, trio, "gpt-4o-mini")
    assert "dims" in ins and "faqs" in ins
    assert ins["run_meta"]["execution"] == "openai_batch"
    assert ins["run_meta"]["model"] == "gpt-4o-mini"
```

Note: `SourcedContext()`/`SourcedAspectVerdict()`는 모든 dim 필드가 `List[SourcedInsight]` 기본 없음일 수 있으니, 실패 시 각 필드 기본값을 `Field(default_factory=list)`로 두는지 확인하고, 아니면 테스트에서 명시적으로 빈 리스트를 채운다(구현 Step에서 실제 스키마 확인 후 조정).

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_batch_openai_assemble.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'parse_output_line'`

- [ ] **Step 3: 최소 구현** — `insight_engine/batch_openai.py`에 추가

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "db"))
import catalog_insight_backfill as cib  # to_insight 재사용
from insight_engine.versioning import build_run_meta
from insight_engine.types import EngineConfig


def parse_output_line(line: dict):
    cid = line["custom_id"]
    ctlg, key = cid.rsplit("|", 1)
    model_cls, _ = SCHEMAS[key]
    content = line["response"]["body"]["choices"][0]["message"]["content"]
    return ctlg, key, model_cls.model_validate_json(content)


def regroup_by_sku(parsed: list) -> dict:
    groups: dict = {}
    for ctlg, key, model in parsed:
        groups.setdefault(ctlg, {})[key] = model
    return {c: t for c, t in groups.items() if set(t) == set(SCHEMAS)}


def assemble_insight(items: list, trio: dict, model: str) -> dict:
    snippets, id_map, _dropped = nrg._build_sourced_snippets(items)
    block = nrg.build_sourced_block(trio["sourced"], trio["context"],
                                    trio["aspect"], id_map, items)
    ins = cib.to_insight(block, len(items))
    ins["run_meta"] = build_run_meta(EngineConfig(model=model))
    ins["run_meta"]["execution"] = "openai_batch"
    return ins
```

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_batch_openai_assemble.py -v`
Expected: PASS (3 passed). 실패 시 Note대로 스키마 기본값 확인 후 테스트의 trio 구성만 조정(구현 코드는 유지).

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add insight_engine/batch_openai.py insight_engine/tests/test_batch_openai_assemble.py
git commit -m "feat(insight-engine): batch_openai 출력파싱·SKU재조립·인사이트조립"
```

---

### Task 3: 오케스트레이터 — SUBMIT (큐·크롤·staging·제출·manifest)

**Files:**
- Create: `db/run_insight_batch_openai.py`
- Test: `insight_engine/tests/test_batch_submit.py`

**Interfaces:**
- Consumes: Task 1 `build_request_lines`/`chunk_requests`; `run_batch.collect`; `catalog_insight_backfill.clean`; OpenAI files/batches; pymongo.
- Produces:
  - `build_queue(db, limit=0) -> list[tuple]` — 미처리 SKU `(pkg_uid, ctlg_no, disp)`. (catalog_insight_backfill 로직 이식)
  - `write_manifest(run_dir, data) / read_manifest(run_dir)`
  - `submit(db, client, run_dir, nid, nsec, model, limit, max_per_batch) -> dict` — 크롤→staging→jsonl→청킹→files/batches.create→manifest.
  - CLI: `--submit --limit N --model gpt-4o-mini --run-dir PATH`

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_batch_submit.py`

핵심 순수 조각만 테스트(크롤·OpenAI는 모킹). manifest 왕복 + 큐 필터 + 제출 파이프라인(모킹).

```python
import json, os
import db_run_insight_batch_openai as sub  # sys.path 조정은 conftest 없이 아래 import 훅으로

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
```

(import 훅: 테스트 상단에 `import sys, os; sys.path.insert(0, "db")` 후 `import run_insight_batch_openai as sub` — 파일명 하이픈 없음 확인. 위 예시의 `db_run_insight_batch_openai`는 실제 모듈명 `run_insight_batch_openai`로 교체.)

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_batch_submit.py -v`
Expected: FAIL — 모듈 없음.

- [ ] **Step 3: 최소 구현** — `db/run_insight_batch_openai.py`

```python
#!/usr/bin/env python3
"""비정형 인사이트 OpenAI Batch API 백엔드 — SUBMIT/STATUS/FETCH 오케스트레이터.
staging.jsonl + manifest.json 로 24h/재시작 재개. 크롤=동기(네이버 쿼터), LLM=Batch(50% 할인)."""
import os, sys, json, argparse, tempfile
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
os.environ.setdefault("INSIGHT_MODEL", "gpt-4o-mini")

import run_batch
import catalog_insight_backfill as cib
from insight_engine import batch_openai as bo
from pymongo import MongoClient


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_queue(db, limit=0):
    pkgs = list(db.products.find({"type": "package"}, {"_id": 1, "catalogs": 1}))
    pkgs.sort(key=lambda p: -len(p.get("catalogs") or []))
    q = []
    for p in pkgs:
        for c in p.get("catalogs") or []:
            if not c.get("ctlg_no"):
                continue
            if c.get("insight"):
                continue
            q.append((p["_id"], c["ctlg_no"], c.get("disp")))
    return q[:limit] if limit else q


def write_manifest(run_dir, data):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_manifest(run_dir):
    with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def submit(db, client, run_dir, nid, nsec, model, limit=0, max_per_batch=40000, workers=16):
    os.makedirs(run_dir, exist_ok=True)
    queue = build_queue(db, limit)
    staging_path = os.path.join(run_dir, "staging.jsonl")
    all_lines = []
    lock_lines = []

    def work(task):
        pkg_uid, ctlg, disp = task
        kw = cib.clean(disp)
        try:
            items = run_batch.collect(kw, nid, nsec, raise_blog_quota=True)
        except run_batch.QuotaStop:
            return None
        except Exception:
            return None
        rec = {"pkg_uid": pkg_uid, "ctlg_no": ctlg, "kw": kw, "items": items}
        lines = bo.build_request_lines(ctlg, kw, items, model) if items else []
        return rec, lines

    with open(staging_path, "w", encoding="utf-8") as sf, ThreadPoolExecutor(max_workers=workers) as ex:
        for out in ex.map(work, queue):
            if not out:
                continue
            rec, lines = out
            sf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            all_lines.extend(lines)

    chunks = bo.chunk_requests(all_lines, max_per_batch)
    batch_ids, chunk_meta = [], []
    for i, chunk in enumerate(chunks):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        for l in chunk:
            tmp.write(json.dumps(l, ensure_ascii=False) + "\n")
        tmp.close()
        with open(tmp.name, "rb") as fh:
            up = client.files.create(file=fh, purpose="batch")
        b = client.batches.create(input_file_id=up.id, endpoint="/v1/chat/completions",
                                  completion_window="24h")
        os.unlink(tmp.name)
        batch_ids.append(b.id)
        chunk_meta.append({"batch_id": b.id, "file_id": up.id, "n": len(chunk)})

    manifest = {"batch_run_id": os.path.basename(run_dir), "created_at": now_iso(),
                "model": model, "batch_ids": batch_ids, "staging_path": staging_path,
                "request_count": len(all_lines), "chunks": chunk_meta, "status": "submitted"}
    write_manifest(run_dir, manifest)
    return manifest
```

(CLI 진입점은 Task 5에서 완성. Task 3에서는 위 함수까지.)

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_batch_submit.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add db/run_insight_batch_openai.py insight_engine/tests/test_batch_submit.py
git commit -m "feat(insight-engine): batch SUBMIT — 큐·크롤·staging·제출·manifest"
```

---

### Task 4: 오케스트레이터 — STATUS · FETCH (폴링·회수·조립·Mongo적재)

**Files:**
- Modify: `db/run_insight_batch_openai.py`
- Test: `insight_engine/tests/test_batch_fetch.py`

**Interfaces:**
- Consumes: Task 2 `parse_output_line`/`regroup_by_sku`/`assemble_insight`; manifest/staging; OpenAI batches/files; pymongo.
- Produces:
  - `status(client, run_dir) -> dict` — 배치별 상태 집계.
  - `load_staging(run_dir) -> dict[str, dict]` — `{ctlg_no: rec}`.
  - `fetch(db, client, run_dir) -> dict` — 완료배치 output 파싱→SKU재조립→assemble→`products.update_one`(멱등: insight 있으면 skip). 처리·skip·미완 카운트 반환.

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_batch_fetch.py`

```python
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


def _out_line(ctlg, key):
    cls = orch.bo.SCHEMAS[key][0]
    inst = cls(faqs=[]) if key == "sourced" else cls()
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_batch_fetch.py -v`
Expected: FAIL — `AttributeError: 'fetch'`

- [ ] **Step 3: 최소 구현** — `db/run_insight_batch_openai.py`에 추가

```python
def status(client, run_dir):
    m = read_manifest(run_dir)
    rows = []
    for bid in m["batch_ids"]:
        b = client.batches.retrieve(bid)
        rows.append({"batch_id": bid, "status": b.status})
    return {"batch_run_id": m["batch_run_id"], "batches": rows}


def load_staging(run_dir):
    m = read_manifest(run_dir)
    out = {}
    with open(m["staging_path"], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                out[rec["ctlg_no"]] = rec
    return out


def fetch(db, client, run_dir):
    m = read_manifest(run_dir)
    staging = load_staging(run_dir)
    parsed = []
    pending = 0
    for bid in m["batch_ids"]:
        b = client.batches.retrieve(bid)
        if b.status != "completed":
            pending += 1
            continue
        raw = client.files.content(b.output_file_id).read()
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                parsed.append(bo.parse_output_line(json.loads(line)))
    grouped = bo.regroup_by_sku(parsed)
    loaded = skipped = 0
    for ctlg, trio in grouped.items():
        rec = staging.get(ctlg)
        if not rec:
            continue
        # 멱등: 이미 insight 있으면 skip
        doc = db.products.find_one({"_id": rec["pkg_uid"], "catalogs.ctlg_no": ctlg},
                                   {"catalogs.$": 1})
        existing = ((doc or {}).get("catalogs") or [{}])[0].get("insight") if doc else None
        if existing:
            skipped += 1
            continue
        ins = bo.assemble_insight(rec["items"], trio, m["model"])
        db.products.update_one({"_id": rec["pkg_uid"]},
                               {"$set": {"catalogs.$[c].insight": ins}},
                               array_filters=[{"c.ctlg_no": ctlg}])
        loaded += 1
    return {"loaded": loaded, "skipped": skipped, "pending_batches": pending}
```

Note: 테스트의 FakeCol.find_one은 `{"_id":"p1"}`만 반환(insight 키 없음)→existing=None→적재. 구현의 find_one projection과 테스트 반환형이 맞물리게 확인.

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_batch_fetch.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add db/run_insight_batch_openai.py insight_engine/tests/test_batch_fetch.py
git commit -m "feat(insight-engine): batch STATUS·FETCH — 폴링·회수·조립·Mongo적재(멱등)"
```

---

### Task 5: CLI 진입점 · 비용 사전보고 · 미러링

**Files:**
- Modify: `db/run_insight_batch_openai.py` (CLI `main()`)
- Test: `insight_engine/tests/test_batch_cli.py`

**Interfaces:**
- Produces: `estimate_cost(request_count, model) -> dict`(입력토큰 추정·mini batch 단가), `main()` argparse(`--submit/--status/--fetch`, `--limit/--model/--run-dir`, `--yes`). SUBMIT는 `estimate_cost`를 출력하고 `--yes` 없으면 확인 프롬프트.

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_batch_cli.py`

```python
import sys; sys.path.insert(0, "db")
import run_insight_batch_openai as orch


def test_estimate_cost_batch_halves_price():
    est = orch.estimate_cost(request_count=3000, model="gpt-4o-mini")
    # batch 단가는 동기의 50%. 최소한 정수 요청수·양수 비용·통화 필드 존재
    assert est["request_count"] == 3000
    assert est["usd"] > 0 and est["krw"] > 0
    assert est["discounted"] is True
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_batch_cli.py -v`
Expected: FAIL — `AttributeError: 'estimate_cost'`

- [ ] **Step 3: 최소 구현** — `db/run_insight_batch_openai.py`에 추가

```python
# mini 정가(1M): in $0.15 / out $0.60. batch = 50%. 요청당 평균 토큰은 보수적 추정.
_AVG_IN_TOK = 4000   # snippets 포함 프롬프트 평균(보수적)
_AVG_OUT_TOK = 900


def estimate_cost(request_count, model="gpt-4o-mini"):
    price = {"gpt-4o-mini": (0.15, 0.60)}.get(model, (0.15, 0.60))
    usd_full = request_count * (_AVG_IN_TOK / 1e6 * price[0] + _AVG_OUT_TOK / 1e6 * price[1])
    usd = usd_full * 0.5  # Batch API 50% 할인
    return {"request_count": request_count, "model": model, "usd": round(usd, 2),
            "krw": round(usd * 1380), "discounted": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default=os.environ.get("INSIGHT_MODEL", "gpt-4o-mini"))
    ap.add_argument("--run-dir", default=os.path.join(HERE, "insight_engine_batch", "run"))
    ap.add_argument("--yes", action="store_true", help="비용 확인 프롬프트 생략")
    args = ap.parse_args()

    from openai import OpenAI
    client = OpenAI()
    dbname = os.environ.get("INSIGHTS_DB", "insights")
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[dbname]

    if args.submit:
        nid = os.environ["NAVER_CLIENT_ID"]; nsec = os.environ["NAVER_CLIENT_SECRET"]
        q = build_queue(db, args.limit)
        est = estimate_cost(len(q) * 3, args.model)
        print(f"[대상 DB={dbname}] 미처리 SKU {len(q)} × 3콜 = {est['request_count']}요청 · "
              f"예상 ≈ ${est['usd']} (≈₩{est['krw']}, Batch 50%할인 반영)")
        if not args.yes and input("진행? [y/N] ").strip().lower() != "y":
            print("취소"); return
        m = submit(db, client, args.run_dir, nid, nsec, args.model, args.limit)
        print(f"제출완료 · 배치 {len(m['batch_ids'])}개 · 요청 {m['request_count']} · run_dir={args.run_dir}")
    elif args.status:
        print(json.dumps(status(client, args.run_dir), ensure_ascii=False, indent=2))
    elif args.fetch:
        print(f"[대상 DB={dbname}] FETCH")
        print(json.dumps(fetch(db, client, args.run_dir), ensure_ascii=False))
    else:
        ap.error("--submit | --status | --fetch 중 하나")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 통과 확인 + 전체 스위트**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/ -q`
Expected: 기존 18 + 신규(build 4·assemble 3·submit 3·fetch 1·cli 1 = 12) → 30 passed.

- [ ] **Step 5: 커밋 + 미러링**

```bash
cd /Users/a1101417/Work/business-model/insight
git add db/run_insight_batch_openai.py insight_engine/tests/test_batch_cli.py
git commit -m "feat(insight-engine): batch CLI 진입점 + 비용 사전보고"
cd ~/Work/insight-engine && ./sync_from_workspace.sh
git add -A && git status --short   # insight_engine/batch_openai.py + 테스트만
git commit -m "feat: batch_openai 백엔드 미러링" && git push
```

Note: `db/run_insight_batch_openai.py`는 오케스트레이터라 새 repo에 미러하지 않는다(순수 `insight_engine/`만 미러). batch_openai.py는 `catalog_insight_backfill` import 때문에 새 repo 단독 실행은 안 되지만, 코드 보관/공개 목적이므로 기존 README 정책과 동일.

---

## 실행(구현 후 · 별도 승인)

구현 완료 후 실제 배치 실행은 다음 순서, **각 단계 사용자 승인**:
1. Mongo 기동(`open -a Docker` → `docker start pig-mongo`), 키 로드(`set -a; eval "$(grep '^export ' run.sh)"; set +a`).
2. `INSIGHTS_DB=insights python3 db/run_insight_batch_openai.py --submit --limit 0` → 비용 프롬프트에서 N·예상비용 확인 후 y.
3. 수시간~24h 후 `--status`로 완료 확인.
4. `INSIGHTS_DB=insights python3 db/run_insight_batch_openai.py --fetch` (완료까지 반복).

## Self-Review (스펙 대비 커버리지)
- 스펙 ① SUBMIT(큐·크롤·staging·jsonl·청킹·제출·manifest) → Task 1(빌드/청킹)·Task 3(오케스트레이션). ✅
- 스펙 ② STATUS → Task 4 `status`. ✅
- 스펙 ③ FETCH(회수·되매핑·조립·Mongo적재·멱등) → Task 2(파싱/조립)·Task 4(fetch). ✅
- 스펙 데이터계약(custom_id `|`·manifest·staging·run_meta.execution) → Task 1·3·4에서 사용·검증. ✅
- 스펙 재현성 run_meta.execution="openai_batch" → Task 2 assemble_insight. ✅
- 스펙 비용 사전보고 → Task 5 estimate_cost + main. ✅
- 스펙 품질 동일(_build_sourced_snippets/build_sourced_block/to_insight 재사용) → Task 2. ✅
- 기존 파일 무수정(재사용 import만) → 전 태스크. ✅
- 타입 일관성: `SCHEMAS` 키(sourced/context/aspect)가 build·parse·regroup·assemble 전반 동일. custom_id `rsplit("|",1)`가 build의 `"{ctlg}|{key}"`와 대칭. ✅
- 열린 항목(type_to_response_format_param 내부 API): Task 1에서 import, 실패 시 openai 버전 확인 — 구현 시 검증.
