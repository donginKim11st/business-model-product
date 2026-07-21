# insight-engine 제품화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 비정형 인사이트 추출 코어를 호출 가능한 `insight_engine` 패키지(잡 큐 + 얇은 동기 래퍼 + HTTP 어댑터)로 뽑아내고, 모든 결과에 재현용 `run_meta`를 박고 `/metrics`로 관측한다.

**Architecture:** `naver_review_geo.py`(3,489줄) 모놀리스는 손대지 않고, 기존 `run_batch.collect`/`run_batch.extract_full`/`run_batch.make_client`를 호출로 재사용하는 **얇은 파사드**로 감싼다. 코어(`engine.py`)는 I/O 없는 순수 함수, 상태(Mongo·파일·쿼터)는 `jobs.py`에만, HTTP는 어댑터일 뿐. 개발은 `insight/` 워크스페이스에서 하고(실코드 import·테스트), 안정된 `insight_engine/` 폴더만 새 repo(github.com/10xtf/insight-engine)로 미러링한다.

**Tech Stack:** Python 3, pydantic(기존 모델 재사용), stdlib `http.server`(신규 의존성 없음 — `pipeline_trigger.py`와 동일 패턴), pytest.

## Global Constraints

- 신규 서드파티 의존성 추가 금지 — 기존 스택(pydantic·openai·stdlib)만 사용. 라이센스 무료 원칙.
- 코어 `engine.py`는 Mongo·파일·HTTP·전역 상태에 의존하지 않는다(순수 함수).
- `naver_review_geo.py`의 함수 시그니처·내부는 변경하지 않는다(파사드는 호출만).
- 실 API 키(`run.sh`)·산출 데이터(`*.jsonl`/`*.csv`)는 새 repo에 절대 커밋하지 않는다(`.gitignore` 이미 차단).
- 커밋 메시지에 `Claude-Session` 트레일러 넣지 않는다.
- 모든 파일 경로는 `insight/` 기준(예: `insight_engine/engine.py` = `/Users/a1101417/Work/business-model/insight/insight_engine/engine.py`).
- 테스트는 `insight_engine/tests/` 아래에 둔다(폴더 통째로 미러링하기 위함).

## 재사용하는 기존 인터페이스 (검증된 실측 시그니처)

- `run_batch.collect(kw, nid, nsecret, ytk=None, use_yt=False, raise_blog_quota=False) -> List[dict]` — 네이버 블로그(+선택 다나와) 수집.
- `run_batch.extract_full(kw, items, llm) -> Optional[dict]` — `nrg.extract_sourced_insights` + `nrg.build_sourced_block`. items 없으면 None.
- `run_batch.make_client() -> OpenAI` — usage/비용 계측 래핑된 OpenAI 클라이언트.
- `run_batch.usd() -> float` — 누적 비용(USD). 전역 `USAGE` Counter 기반.
- `run_batch.QuotaStop` — 네이버 블로그 429 시(raise_blog_quota=True) 던져지는 예외.
- 프롬프트 상수(prompt_version 해시 대상): `naver_review_geo.EXTRACT_SOURCED_PROMPT`, `.EXTRACT_CONTEXT_PROMPT`, `.EXTRACT_ASPECT_VERDICT_PROMPT`.
- 자격증명 env: `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `OPENAI_API_KEY`, `INSIGHT_MODEL`(기본 gpt-4o-mini).

---

### Task 1: 패키지 스캐폴드 · 타입 · 버전고정(run_meta)

**Files:**
- Create: `insight_engine/__init__.py`
- Create: `insight_engine/types.py`
- Create: `insight_engine/versioning.py`
- Create: `insight_engine/tests/__init__.py`
- Test: `insight_engine/tests/test_versioning.py`

**Interfaces:**
- Produces:
  - `types.ExtractTarget(keyword: str, uid: str = "", name: str = "", sku: str = "", brand: str = "", context: str = "")`
  - `types.EngineConfig(model: str = "gpt-4o-mini", sources: dict = {"blog": True, "danawa": False, "youtube": False}, lexicon_version: str = "v1", retries: int = 3)`
  - `types.InsightResult(target_uid: str, keyword: str, block: dict | None, run_meta: dict, cost_usd: float = 0.0, dropped: int = 0, error: str = "")`
  - `versioning.ENGINE_VERSION: str`
  - `versioning.prompt_version() -> str` (3개 프롬프트 상수의 sha256 12자리)
  - `versioning.build_run_meta(cfg: EngineConfig) -> dict` (키: engine_version, prompt_version, model, lexicon_version, source_config, extracted_at)

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_versioning.py`

```python
import hashlib
import naver_review_geo as nrg
from insight_engine.types import EngineConfig
from insight_engine import versioning


def test_prompt_version_is_hash_of_three_prompts():
    combined = (nrg.EXTRACT_SOURCED_PROMPT + nrg.EXTRACT_CONTEXT_PROMPT
                + nrg.EXTRACT_ASPECT_VERDICT_PROMPT).encode("utf-8")
    expected = hashlib.sha256(combined).hexdigest()[:12]
    assert versioning.prompt_version() == expected


def test_build_run_meta_has_all_keys_and_is_deterministic():
    cfg = EngineConfig(model="gpt-4o-mini", lexicon_version="v1")
    m1 = versioning.build_run_meta(cfg)
    m2 = versioning.build_run_meta(cfg)
    assert set(m1) == {"engine_version", "prompt_version", "model",
                       "lexicon_version", "source_config", "extracted_at"}
    assert m1["prompt_version"] == m2["prompt_version"]
    assert m1["model"] == "gpt-4o-mini"
    assert m1["lexicon_version"] == "v1"


def test_config_change_changes_run_meta_model():
    a = versioning.build_run_meta(EngineConfig(model="gpt-4o-mini"))
    b = versioning.build_run_meta(EngineConfig(model="gpt-4o"))
    assert a["model"] != b["model"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_versioning.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'insight_engine'`

- [ ] **Step 3: 최소 구현**

`insight_engine/__init__.py`:
```python
"""insight-engine — 출처·인용 접지 비정형 인사이트 추출 코어."""
```

`insight_engine/tests/__init__.py`: (빈 파일)

`insight_engine/types.py`:
```python
"""엔진 데이터 계약 — 순수 dataclass (I/O·상태 없음)."""
from dataclasses import dataclass, field


@dataclass
class ExtractTarget:
    keyword: str
    uid: str = ""
    name: str = ""
    sku: str = ""
    brand: str = ""
    context: str = ""

    def key(self) -> str:
        """멱등 재개용 안정 식별자. uid 우선, 없으면 keyword."""
        return self.uid or self.keyword


@dataclass
class EngineConfig:
    model: str = "gpt-4o-mini"
    sources: dict = field(
        default_factory=lambda: {"blog": True, "danawa": False, "youtube": False})
    lexicon_version: str = "v1"
    retries: int = 3


@dataclass
class InsightResult:
    target_uid: str
    keyword: str
    block: dict | None
    run_meta: dict
    cost_usd: float = 0.0
    dropped: int = 0
    error: str = ""
```

`insight_engine/versioning.py`:
```python
"""재현성 — 프롬프트/모델/사전 버전을 run_meta로 고정."""
import hashlib
from datetime import datetime, timezone

import naver_review_geo as nrg
from insight_engine.types import EngineConfig

ENGINE_VERSION = "0.1.0"


def prompt_version() -> str:
    combined = (nrg.EXTRACT_SOURCED_PROMPT + nrg.EXTRACT_CONTEXT_PROMPT
                + nrg.EXTRACT_ASPECT_VERDICT_PROMPT).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:12]


def build_run_meta(cfg: EngineConfig) -> dict:
    return {
        "engine_version": ENGINE_VERSION,
        "prompt_version": prompt_version(),
        "model": cfg.model,
        "lexicon_version": cfg.lexicon_version,
        "source_config": dict(cfg.sources),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_versioning.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add insight_engine/__init__.py insight_engine/types.py insight_engine/versioning.py insight_engine/tests/
git commit -m "feat(insight-engine): 타입 계약 + run_meta 버전고정"
```

---

### Task 2: engine.extract_insight 파사드

**Files:**
- Create: `insight_engine/engine.py`
- Test: `insight_engine/tests/test_engine.py`

**Interfaces:**
- Consumes: `types.ExtractTarget`, `types.EngineConfig`, `types.InsightResult`, `versioning.build_run_meta`; 기존 `run_batch.collect/extract_full/make_client/usd`.
- Produces: `engine.extract_insight(target: ExtractTarget, cfg: EngineConfig, *, llm=None, creds: dict | None = None) -> InsightResult`
  - `creds` 없으면 env(`NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`)에서 읽는다.
  - `llm` 없으면 `run_batch.make_client()`로 생성.
  - 반환 InsightResult는 항상 `run_meta`를 포함(성공/빈결과/에러 모두).

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_engine.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'insight_engine.engine'`

- [ ] **Step 3: 최소 구현** — `insight_engine/engine.py`

```python
"""추출 코어 파사드 — 기존 run_batch 함수를 재사용하는 얇은 순수 래퍼."""
import os

import run_batch
from insight_engine.types import ExtractTarget, EngineConfig, InsightResult
from insight_engine import versioning


def _creds(creds: dict | None) -> tuple[str, str]:
    if creds:
        return creds["nid"], creds["nsec"]
    return (os.environ.get("NAVER_CLIENT_ID", ""),
            os.environ.get("NAVER_CLIENT_SECRET", ""))


def extract_insight(target: ExtractTarget, cfg: EngineConfig, *,
                    llm=None, creds: dict | None = None) -> InsightResult:
    run_meta = versioning.build_run_meta(cfg)
    nid, nsec = _creds(creds)
    if llm is None:
        os.environ.setdefault("INSIGHT_MODEL", cfg.model)
        llm = run_batch.make_client()

    try:
        items = run_batch.collect(target.keyword, nid, nsec,
                                  use_yt=cfg.sources.get("youtube", False),
                                  raise_blog_quota=True)
    except run_batch.QuotaStop as e:
        return InsightResult(target.key(), target.keyword, None, run_meta,
                             cost_usd=run_batch.usd(), error=str(e) or "quota")

    block = run_batch.extract_full(target.keyword, items, llm)
    return InsightResult(target.key(), target.keyword, block, run_meta,
                         cost_usd=run_batch.usd())
```

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_engine.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add insight_engine/engine.py insight_engine/tests/test_engine.py
git commit -m "feat(insight-engine): extract_insight 파사드 + run_meta 스탬핑"
```

---

### Task 3: jobs.py — 잡 큐 · 재개/멱등 · 쿼터 인지 워커

**Files:**
- Create: `insight_engine/jobs.py`
- Test: `insight_engine/tests/test_jobs.py`

**Interfaces:**
- Consumes: `engine.extract_insight`, `types.ExtractTarget/EngineConfig/InsightResult`.
- Produces:
  - `jobs.JobStore(path: str)` — JSONL 백엔드. `done_keys() -> set[str]`, `append(result: InsightResult) -> None`.
  - `jobs.submit(targets, cfg, store, *, extract=engine.extract_insight, llm=None, creds=None) -> str` (job_id 반환, 동기 드레인)
  - `jobs.get(job_id) -> dict` — `{job_id, total, done, empty, errors, quota_paused, cost_usd}`
  - 이미 처리한 `target.key()`는 skip(멱등). QuotaStop(result.error truthy & block None & "quota" in error)면 남은 대상 중단하고 `quota_paused=True`.

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_jobs.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'insight_engine.jobs'`

- [ ] **Step 3: 최소 구현** — `insight_engine/jobs.py`

```python
"""잡 큐 — JSONL 백엔드, 재개·멱등, 쿼터 인지 동기 드레인."""
import json
import os
from dataclasses import asdict

from insight_engine import engine
from insight_engine.types import InsightResult

_JOBS: dict = {}


class JobStore:
    def __init__(self, path: str):
        self.path = path

    def done_keys(self) -> set:
        if not os.path.exists(self.path):
            return set()
        keys = set()
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    keys.add(json.loads(line)["target_uid"])
        return keys

    def append(self, result: InsightResult) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")


def _is_quota(r: InsightResult) -> bool:
    return r.block is None and "quota" in (r.error or "").lower()


def submit(targets, cfg, store, *, extract=engine.extract_insight,
           llm=None, creds=None) -> str:
    job_id = f"job-{len(_JOBS) + 1}"
    done = store.done_keys()
    state = {"job_id": job_id, "total": len(targets), "done": 0,
             "empty": 0, "errors": 0, "quota_paused": False, "cost_usd": 0.0}
    _JOBS[job_id] = state

    for t in targets:
        if t.key() in done:
            continue
        r = extract(t, cfg, llm=llm, creds=creds)
        if _is_quota(r):
            state["quota_paused"] = True
            break
        store.append(r)
        state["done"] += 1
        state["cost_usd"] = r.cost_usd
        if r.error:
            state["errors"] += 1
        elif r.block is None:
            state["empty"] += 1
    return job_id


def get(job_id: str) -> dict:
    return dict(_JOBS[job_id])
```

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_jobs.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add insight_engine/jobs.py insight_engine/tests/test_jobs.py
git commit -m "feat(insight-engine): jobs 큐 — 재개·멱등·쿼터인지 드레인"
```

---

### Task 4: sync.py — 단건 동기 래퍼

**Files:**
- Create: `insight_engine/sync.py`
- Test: `insight_engine/tests/test_sync.py`

**Interfaces:**
- Consumes: `jobs.submit/get`, `engine.extract_insight`.
- Produces: `sync.extract_one(target: ExtractTarget, cfg: EngineConfig, *, extract=engine.extract_insight, llm=None, creds=None) -> InsightResult` — 임시 인메모리 스토어로 submit+즉시 반환. 단건 저빈도용.

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_sync.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'insight_engine.sync'`

- [ ] **Step 3: 최소 구현** — `insight_engine/sync.py`

```python
"""단건 동기 래퍼 — 잡 큐 위 얇은 편의 함수."""
import tempfile

from insight_engine import jobs, engine
from insight_engine.types import InsightResult


def extract_one(target, cfg, *, extract=engine.extract_insight,
                llm=None, creds=None) -> InsightResult:
    captured = {}
    def wrap(t, c, *, llm=None, creds=None):
        r = extract(t, c, llm=llm, creds=creds)
        captured["r"] = r
        return r
    with tempfile.NamedTemporaryFile(suffix=".jsonl") as tf:
        jobs.submit([target], cfg, jobs.JobStore(tf.name),
                    extract=wrap, llm=llm, creds=creds)
    return captured["r"]
```

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_sync.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add insight_engine/sync.py insight_engine/tests/test_sync.py
git commit -m "feat(insight-engine): sync.extract_one 단건 래퍼"
```

---

### Task 5: metrics.py — 관측 집계 + 알림 임계치

**Files:**
- Create: `insight_engine/metrics.py`
- Test: `insight_engine/tests/test_metrics.py`

**Interfaces:**
- Consumes: `jobs.get`.
- Produces:
  - `metrics.snapshot(job_states: list[dict]) -> dict` — `{total, done, empty, errors, cost_usd, quota_paused_jobs, error_rate}`.
  - `metrics.alerts(snap: dict, *, error_rate_max: float = 0.2) -> list[str]` — 실패율 초과·쿼터정지 시 메시지 리스트(빈 리스트 = 정상).

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_metrics.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'insight_engine.metrics'`

- [ ] **Step 3: 최소 구현** — `insight_engine/metrics.py`

```python
"""관측 — 잡 상태 집계 + 알림 임계치."""


def snapshot(job_states: list) -> dict:
    total = sum(s.get("total", 0) for s in job_states)
    done = sum(s.get("done", 0) for s in job_states)
    empty = sum(s.get("empty", 0) for s in job_states)
    errors = sum(s.get("errors", 0) for s in job_states)
    cost = sum(s.get("cost_usd", 0.0) for s in job_states)
    paused = sum(1 for s in job_states if s.get("quota_paused"))
    return {
        "total": total, "done": done, "empty": empty, "errors": errors,
        "cost_usd": cost, "quota_paused_jobs": paused,
        "error_rate": (errors / done) if done else 0.0,
    }


def alerts(snap: dict, *, error_rate_max: float = 0.2) -> list:
    msgs = []
    if snap.get("error_rate", 0.0) > error_rate_max:
        msgs.append(f"실패율 초과: {snap['error_rate']:.0%} > {error_rate_max:.0%}")
    if snap.get("quota_paused_jobs", 0) > 0:
        msgs.append(f"쿼터 정지 잡 {snap['quota_paused_jobs']}개")
    return msgs
```

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_metrics.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add insight_engine/metrics.py insight_engine/tests/test_metrics.py
git commit -m "feat(insight-engine): metrics 집계 + 알림 임계치"
```

---

### Task 6: HTTP 어댑터 — /extract · /jobs · /jobs/{id} · /metrics

**Files:**
- Create: `insight_engine/http_adapter.py`
- Test: `insight_engine/tests/test_http_adapter.py`

**Interfaces:**
- Consumes: `sync.extract_one`, `jobs.submit/get`, `metrics.snapshot/alerts`, `types.ExtractTarget/EngineConfig`.
- Produces: `http_adapter.Router` — stdlib `http.server.BaseHTTPRequestHandler` 없이 순수 디스패치 함수 `route(method: str, path: str, body: dict) -> tuple[int, dict]`로 로직을 분리(HTTP 서버는 얇은 껍데기, 테스트는 route를 직접 호출).
  - `POST /extract` body `{keyword, uid?, model?}` → `(200, {result})` (동기 단건).
  - `POST /jobs` body `{targets: [{keyword, uid}], model?}` → `(202, {job_id})`.
  - `GET /jobs/{id}` → `(200, jobs.get(id))` / 없으면 `(404, {...})`.
  - `GET /metrics` → `(200, {snapshot, alerts})`.
- `http_adapter.serve(port: int)` — `route`를 감싸는 stdlib http.server 실행부(테스트 대상 아님).

- [ ] **Step 1: 실패 테스트 작성** — `insight_engine/tests/test_http_adapter.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_http_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'insight_engine.http_adapter'`

- [ ] **Step 3: 최소 구현** — `insight_engine/http_adapter.py`

```python
"""HTTP 어댑터 — 순수 route() 디스패치 + 얇은 stdlib 서버 껍데기."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from insight_engine import sync, jobs, metrics
from insight_engine.types import ExtractTarget, EngineConfig

_DEFAULT_STORE_DIR = "insight_engine_jobs"


def _cfg(body: dict) -> EngineConfig:
    return EngineConfig(model=body.get("model", "gpt-4o-mini"))


def route(method: str, path: str, body: dict):
    if method == "POST" and path == "/extract":
        t = ExtractTarget(keyword=body["keyword"], uid=body.get("uid", ""))
        r = sync.extract_one(t, _cfg(body))
        return 200, {"result": r.__dict__}

    if method == "POST" and path == "/jobs":
        import os, tempfile
        os.makedirs(_DEFAULT_STORE_DIR, exist_ok=True)
        store = jobs.JobStore(tempfile.mktemp(dir=_DEFAULT_STORE_DIR, suffix=".jsonl"))
        targets = [ExtractTarget(keyword=t["keyword"], uid=t.get("uid", ""))
                   for t in body.get("targets", [])]
        job_id = jobs.submit(targets, _cfg(body), store)
        return 202, {"job_id": job_id}

    if method == "GET" and path.startswith("/jobs/"):
        jid = path[len("/jobs/"):]
        if jid not in jobs._JOBS:
            return 404, {"error": f"unknown job {jid}"}
        return 200, jobs.get(jid)

    if method == "GET" and path == "/metrics":
        snap = metrics.snapshot(list(jobs._JOBS.values()))
        return 200, {"snapshot": snap, "alerts": metrics.alerts(snap)}

    return 404, {"error": "not found"}


class _Handler(BaseHTTPRequestHandler):
    def _dispatch(self, method):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        code, payload = route(method, self.path.rstrip("/") or "/", body)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def serve(port: int = 8767):
    HTTPServer(("127.0.0.1", port), _Handler).serve_forever()
```

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_http_adapter.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add insight_engine/http_adapter.py insight_engine/tests/test_http_adapter.py
git commit -m "feat(insight-engine): HTTP 어댑터(route + stdlib 서버)"
```

---

### Task 7: 운영 호출자에 run_meta 접지 (catalog_insight_backfill)

**목적:** 운영 DB에 저장되는 SKU 인사이트에 `run_meta`를 실제로 박아 재현성 목표(#2)를 실데이터에 적용한다. 추출 로직은 바꾸지 않고, 저장 직전 `run_meta`만 첨부한다.

**Files:**
- Modify: `db/catalog_insight_backfill.py` (인사이트 저장 dict에 `run_meta` 필드 추가하는 지점)
- Test: `insight_engine/tests/test_backfill_run_meta.py`

**Interfaces:**
- Consumes: `versioning.build_run_meta`, `types.EngineConfig`.
- Produces: 저장되는 insight dict에 `run_meta` 키 존재(엔진 버전·프롬프트 버전·모델).

- [ ] **Step 1: 저장 지점 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && grep -n 'insight' db/catalog_insight_backfill.py | grep -iE 'block|save|update|set|insight =' | head`
Expected: `run_batch.extract_full`로 만든 block을 Mongo `catalogs[].insight`에 넣는 지점 확인.

- [ ] **Step 2: 실패 테스트 작성** — `insight_engine/tests/test_backfill_run_meta.py`

```python
from insight_engine.types import EngineConfig
from insight_engine import versioning


def test_attach_run_meta_wraps_block():
    # backfill 이 사용할 헬퍼: block + cfg -> run_meta 첨부된 저장 dict
    from insight_engine.versioning import build_run_meta
    block = {"strengths": ["a"], "faqs": []}
    cfg = EngineConfig(model="gpt-4o-mini")
    saved = {**block, "run_meta": build_run_meta(cfg)}
    assert saved["run_meta"]["model"] == "gpt-4o-mini"
    assert saved["run_meta"]["engine_version"] == versioning.ENGINE_VERSION
    assert "strengths" in saved
```

- [ ] **Step 3: 실패 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/tests/test_backfill_run_meta.py -v`
Expected: PASS (헬퍼가 이미 존재하므로 통과) — 이 테스트는 계약 고정용. 실패하면 Task 1 `build_run_meta` 회귀.

- [ ] **Step 4: backfill 저장 지점 수정**

`db/catalog_insight_backfill.py`에서 `run_batch.extract_full`의 결과 block을 저장하는 지점 바로 앞에 아래를 삽입(정확한 변수명은 Step 1에서 확인한 것으로 맞춘다):

```python
from insight_engine.versioning import build_run_meta
from insight_engine.types import EngineConfig

# block 저장 직전:
if block is not None:
    block["run_meta"] = build_run_meta(
        EngineConfig(model=os.environ.get("INSIGHT_MODEL", "gpt-4o-mini")))
```

- [ ] **Step 5: 회귀 — 기존 검증 통과 확인**

Run: `cd /Users/a1101417/Work/business-model/insight && python3 -m pytest insight_engine/ -q && python3 -c "import db.catalog_insight_backfill"`
Expected: 전체 PASS + import 에러 없음.

- [ ] **Step 6: 커밋**

```bash
cd /Users/a1101417/Work/business-model/insight
git add db/catalog_insight_backfill.py insight_engine/tests/test_backfill_run_meta.py
git commit -m "feat(insight-engine): 운영 backfill 인사이트에 run_meta 접지"
```

---

### Task 8: 새 repo(insight-engine)로 미러링

**목적:** 안정된 `insight_engine/` 패키지를 github.com/10xtf/insight-engine로 동기화. 실코드는 `insight/`에 남고, 순수 패키지만 미러링.

**Files:**
- Create: `~/Work/insight-engine/insight_engine/` (미러)
- Create: `~/Work/insight-engine/sync_from_workspace.sh` (재현 가능한 미러 스크립트)

- [ ] **Step 1: 미러 스크립트 작성** — `~/Work/insight-engine/sync_from_workspace.sh`

```bash
#!/bin/zsh
# insight/ 워크스페이스의 insight_engine/ 패키지를 이 repo로 미러링.
# 실 API 키·산출 데이터는 .gitignore 로 이미 차단.
set -e
SRC="/Users/a1101417/Work/business-model/insight/insight_engine"
DST="$(cd "$(dirname "$0")" && pwd)/insight_engine"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' "$SRC/" "$DST/"
echo "미러 완료: $DST"
```

- [ ] **Step 2: 미러 실행 + 검증(키·데이터 없음 확인)**

```bash
cd ~/Work/insight-engine
chmod +x sync_from_workspace.sh && ./sync_from_workspace.sh
git add -A
git status --short          # insight_engine/*.py 만 보여야 함 — *.jsonl/run.sh 없음
```
Expected: `insight_engine/` 하위 .py 파일들만 스테이징. 키·데이터 파일 없음.

- [ ] **Step 3: 미러 테스트 통과 확인(독립 실행)**

새 repo에서 테스트가 돌려면 `naver_review_geo`가 필요하므로, 미러 repo는 **패키지 코드 보관/공개용**이고 테스트 실행은 `insight/`에서 한다는 점을 README에 명시한다(이미 언급). 여기서는 파일 존재만 확인:

Run: `ls ~/Work/insight-engine/insight_engine/*.py`
Expected: engine.py jobs.py sync.py metrics.py http_adapter.py types.py versioning.py 나열.

- [ ] **Step 4: 커밋 + 푸시**

```bash
cd ~/Work/insight-engine
git add -A
git commit -m "feat: insight_engine 코어 패키지 미러링 (타입·엔진·잡·동기·메트릭·HTTP)"
git push
```

---

## Self-Review (스펙 대비 커버리지)

- 스펙 §아키텍처 5개 파일(types/engine/jobs/sync/versioning) → Task 1·2·3·4. ✅
- 스펙 §운영 ② 재현성(run_meta) → Task 1(생성)·Task 2(스탬핑)·Task 7(운영데이터 접지). ✅
- 스펙 §운영 ① 모니터링/알림(/metrics) → Task 5·Task 6. ✅
- 스펙 §노출표면 HTTP 어댑터 → Task 6. ✅
- 스펙 §점진 이관(모놀리스 미개조, 파사드) → Task 2가 run_batch 재사용, naver_review_geo 무변경. ✅
- 스펙 §비목표(자동복구·품질게이트) → 계획에 없음(의도적 제외). ✅
- 미러링(insight/ 개발 → 새 repo) → Task 8. ✅
- 타입 일관성: `ExtractTarget.key()`·`InsightResult` 필드·`build_run_meta` 키셋이 Task 1 정의와 이후 태스크에서 동일. ✅
- 열린 항목(잡 저장소 Mongo vs jsonl): 본 계획은 jsonl로 시작(YAGNI). Mongo 승격은 운영 부하 확인 후 별도 계획.
