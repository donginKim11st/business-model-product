# 비정형 인사이트 검증·자동수정 파이프라인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `products.catalogs[].insight`의 오류를 규칙으로 감지하고 발견 즉시 자동 수정하는 확장 가능한 검증 파이프라인을 만든다.

**Architecture:** 단독 스크립트 `insight/db/validate_insights.py`가 선언형 규칙 레지스트리(`RULES`)를 `products.catalogs[]`에 순회 적용한다. 각 규칙은 `detect(ctx)`(순수 판정)와 `fix(ctx)`(mongo update 스펙 반환) 함수만 가지며, 프레임워크가 순회·리포트·`--dry-run`·`--rules` 필터를 공통 처리한다. detect + 무조건 autofix.

**Tech Stack:** Python 3, pymongo, pytest 8.3.5, mongomock 4.2.0, OpenAI `gpt-4o-mini`(R2 LLM 게이트), zsh(step 래퍼).

## Global Constraints

- 저장 위치: `products.catalogs[].insight` / `products.catalogs[].has_insight` (신규 컬렉션 없음).
- MongoDB 접근: `MONGO_URI`(기본 `mongodb://localhost:47017/?directConnection=true`), `INSIGHTS_DB`(기본 `insights`).
- LLM 모델: `gpt-4o-mini` (env `INSIGHT_MODEL`로 override).
- `validate_insights.py`는 `catalog_insight_backfill`/`run_batch`/`naver_review_geo`를 **import하지 않는다** (openai/naver 무거운 의존 회피 — 테스트 경량화). `now_iso` 등 사소한 유틸은 로컬 정의.
- R2(source_mismatch) autofix는 `catalog_insight_backfill.py --retry-empty`의 재큐 조건(`not dims and n_sources==0 and attempts<max`)을 충족하는 빈 상태로 무효화해야 한다.
- 파일 배치: `insight/db/` (기존 스크립트·테스트 관례). 테스트는 `insight/db/test_validate_insights.py`.
- LLM 게이트 호출 실패(429 등)는 **보수적으로 통과**(mismatch 아님)로 처리한다 — 멀쩡한 insight를 오판으로 무효화하지 않기 위함.

---

## File Structure

- **Create** `insight/db/validate_insights.py` — 파서 + 3개 규칙 + 프레임워크 + CLI (단일 파일, `catalog_insight_backfill.py`와 동일한 단일 스크립트 관례).
- **Create** `insight/db/step_validate.sh` — n8n/수동 1회 실행 래퍼 (`step_report.sh` 골격).
- **Create** `insight/db/test_validate_insights.py` — pytest 단위/통합 테스트 (`test_*.py` 관례, mongomock 사용).

---

## Task 1: 용량/개수 파서 (`parse_qty`, `catalog_qty`)

**Files:**
- Create: `insight/db/validate_insights.py`
- Test: `insight/db/test_validate_insights.py`

**Interfaces:**
- Produces:
  - `parse_qty(text: str) -> dict` — `{"mass": float|None, "vol": float|None, "count": int|None}`. 질량은 그램, 부피는 ml로 정규화.
  - `catalog_qty(catalog: dict) -> dict` — `catalog["size"]`/`catalog["count"]` 우선, 없으면 `catalog["disp"]`를 `parse_qty`로 파싱. 동일 dict 형태 반환.

- [ ] **Step 1: Write the failing test**

`insight/db/test_validate_insights.py`:
```python
#!/usr/bin/env python3
"""validate_insights 단위/통합 테스트."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import validate_insights as V


def test_parse_qty_grams():
    assert V.parse_qty("92g")["mass"] == 92.0
    assert V.parse_qty("쿡시 미역국 92g 12개") == {"mass": 92.0, "vol": None, "count": 12}


def test_parse_qty_kg_to_grams():
    assert V.parse_qty("1.5kg")["mass"] == 1500.0


def test_parse_qty_ml_and_liter():
    assert V.parse_qty("500ml")["vol"] == 500.0
    assert V.parse_qty("1.2L")["vol"] == 1200.0


def test_parse_qty_count_variants():
    assert V.parse_qty("24입")["count"] == 24
    assert V.parse_qty("x24")["count"] == 24
    assert V.parse_qty("리뷰 없음")["count"] is None


def test_catalog_qty_uses_structured_fields():
    cat = {"size": "92g", "count": "12개", "disp": "쿡시 미역국 96g 12개"}
    # 구조화된 size/count 우선 → disp의 96g에 오염되지 않아야 한다.
    assert V.catalog_qty(cat) == {"mass": 92.0, "vol": None, "count": 12}


def test_catalog_qty_fallback_to_disp():
    cat = {"size": None, "count": None, "disp": "쿡시 미역국 96g 12개"}
    assert V.catalog_qty(cat) == {"mass": 96.0, "vol": None, "count": 12}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'validate_insights'`

- [ ] **Step 3: Write minimal implementation**

`insight/db/validate_insights.py`:
```python
#!/usr/bin/env python3
"""비정형 인사이트(products.catalogs[].insight) 규칙 검증 + 무조건 autofix.

규칙(RULES)을 products.catalogs[] 전체에 순회 적용한다. 각 규칙은 detect(ctx)로
위반을 판정하고 fix(ctx)로 mongo update 스펙을 반환한다. 프레임워크가 순회·리포트·
--dry-run·--rules 필터를 공통 처리한다. catalog_insight_backfill 등 무거운 의존은 import하지 않는다.

  INSIGHTS_DB=insights_demo python3 db/validate_insights.py --limit 500 --dry-run
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime, timezone

_KG = re.compile(r'(\d+(?:\.\d+)?)\s*kg', re.I)
_G = re.compile(r'(\d+(?:\.\d+)?)\s*g(?![a-z])', re.I)
_L = re.compile(r'(\d+(?:\.\d+)?)\s*l(?![a-z])', re.I)
_ML = re.compile(r'(\d+(?:\.\d+)?)\s*ml', re.I)
_COUNT = re.compile(r'(?:[x×]\s*)?(\d+)\s*(?:개입|개|입|팩|포|매)', re.I)
_COUNT_X = re.compile(r'[x×]\s*(\d+)', re.I)


def parse_qty(text):
    """자유 텍스트에서 질량(g)·부피(ml)·개수를 정규화 추출."""
    t = text or ""
    mass = vol = count = None
    m = _KG.search(t)
    if m:
        mass = float(m.group(1)) * 1000.0
    else:
        m = _G.search(t)
        if m:
            mass = float(m.group(1))
    m = _ML.search(t)
    if m:
        vol = float(m.group(1))
    else:
        m = _L.search(t)
        if m:
            vol = float(m.group(1)) * 1000.0
    m = _COUNT.search(t) or _COUNT_X.search(t)
    if m:
        count = int(m.group(1))
    return {"mass": mass, "vol": vol, "count": count}


def catalog_qty(catalog):
    """구조화된 size/count 우선, 없으면 disp 파싱."""
    q = parse_qty((catalog.get("size") or "") + " " + (catalog.get("count") or ""))
    if q["mass"] is None and q["vol"] is None and q["count"] is None:
        q = parse_qty(catalog.get("disp") or "")
    return q
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add insight/db/validate_insights.py insight/db/test_validate_insights.py
git commit -m "feat(validate): qty parser for insight source-mismatch detection"
```

---

## Task 2: 규칙 프레임워크 + `flag_drift` 규칙

**Files:**
- Modify: `insight/db/validate_insights.py`
- Test: `insight/db/test_validate_insights.py`

**Interfaces:**
- Consumes: (none)
- Produces:
  - `class Rule` — 속성 `id`, `severity`, `detect`, `fix`. `detect(ctx) -> str|None` (위반 시 상세문자열), `fix(ctx) -> dict|None` (mongo update 스펙 `{"filter","update","array_filters"}` 또는 None).
  - `ctx` 형태: `{"db", "pkg_uid", "ctlg_no", "disp", "catalog", "insight", "opts"}`.
  - `detect_flag_drift(ctx) -> str|None`, `fix_flag_drift(ctx) -> dict`.
  - `_af(ctlg_no) -> list` — `array_filters` 헬퍼 `[{"c.ctlg_no": ctlg_no}]`.
  - `now_iso() -> str`.

- [ ] **Step 1: Write the failing test**

`test_validate_insights.py`에 추가:
```python
def _ctx(catalog, opts=None):
    ins = catalog.get("insight")
    return {"db": None, "pkg_uid": "P1", "ctlg_no": catalog.get("ctlg_no"),
            "disp": catalog.get("disp"), "catalog": catalog, "insight": ins,
            "opts": opts or {}}


def test_flag_drift_insight_present_flag_false():
    cat = {"ctlg_no": 1, "has_insight": False, "insight": {"dims": [{"dim": "x"}]}}
    detail = V.detect_flag_drift(_ctx(cat))
    assert detail is not None
    spec = V.fix_flag_drift(_ctx(cat))
    assert spec["update"] == {"$set": {"catalogs.$[c].has_insight": True}}
    assert spec["array_filters"] == [{"c.ctlg_no": 1}]


def test_flag_drift_flag_true_no_insight():
    cat = {"ctlg_no": 2, "has_insight": True, "insight": None}
    assert V.detect_flag_drift(_ctx(cat)) is not None
    spec = V.fix_flag_drift(_ctx(cat))
    assert spec["update"] == {"$set": {"catalogs.$[c].has_insight": False}}


def test_flag_drift_consistent_is_noop():
    cat = {"ctlg_no": 3, "has_insight": True, "insight": {"dims": [{"dim": "x"}]}}
    assert V.detect_flag_drift(_ctx(cat)) is None
```

주의: `flag_drift`는 insight의 "존재"만 본다(빈 insight도 존재로 간주) — 빈 insight 문서도 `has_insight`가 그 유무를 정확히 반영해야 하기 때문. `has_insight`는 `bool(insight)`와 일치해야 한다.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -k flag_drift -v`
Expected: FAIL — `AttributeError: module 'validate_insights' has no attribute 'detect_flag_drift'`

- [ ] **Step 3: Write minimal implementation**

`validate_insights.py`의 import 아래에 추가:
```python
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _af(ctlg_no):
    return [{"c.ctlg_no": ctlg_no}]


class Rule:
    def __init__(self, id, severity, detect, fix=None):
        self.id = id
        self.severity = severity
        self.detect = detect
        self.fix = fix


# --- R1: flag_drift -------------------------------------------------------
def detect_flag_drift(ctx):
    actual = bool(ctx["catalog"].get("insight"))
    flag = bool(ctx["catalog"].get("has_insight"))
    if actual != flag:
        return f"has_insight={flag} but insight present={actual}"
    return None


def fix_flag_drift(ctx):
    actual = bool(ctx["catalog"].get("insight"))
    return {"filter": {"_id": ctx["pkg_uid"]},
            "update": {"$set": {"catalogs.$[c].has_insight": actual}},
            "array_filters": _af(ctx["ctlg_no"])}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -k flag_drift -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add insight/db/validate_insights.py insight/db/test_validate_insights.py
git commit -m "feat(validate): Rule framework + flag_drift rule"
```

---

## Task 3: `source_mismatch` 규칙 (휴리스틱 + LLM 게이트)

**Files:**
- Modify: `insight/db/validate_insights.py`
- Test: `insight/db/test_validate_insights.py`

**Interfaces:**
- Consumes: `parse_qty`, `catalog_qty`, `now_iso`, `_af`, `ctx` 형태.
- Produces:
  - `evidence_texts(insight) -> list[str]` — 모든 point/faq의 evidence·answer_evidence에서 `title`+" "+`quote` 수집.
  - `_dominant(values) -> tuple` — `(value, fraction)` 최빈값과 그 비율(빈 리스트면 `(None, 0.0)`).
  - `detect_source_mismatch(ctx) -> str|None` — 휴리스틱 우선, 애매하면 `ctx["opts"]["gate_fn"]` 호출. `gate_fn`은 `(disp, texts) -> bool`(같은 상품이면 True). `opts["llm_gate"]`가 False면 게이트 스킵(통과).
  - `fix_source_mismatch(ctx) -> dict` — insight를 재수집 가능한 빈 상태로 무효화 + `has_insight=False`.

- [ ] **Step 1: Write the failing test**

```python
def _insight_with_evidence(*texts):
    ev = [{"title": t, "quote": ""} for t in texts]
    return {"dims": [{"dim": "aspect.taste",
                      "points": [{"point": "p", "evidence": ev}]}],
            "faqs": [], "n_sources": len(texts)}


def test_source_mismatch_clear_size_diff():
    # catalog 92g, evidence 다수 96g → 휴리스틱만으로 mismatch.
    cat = {"ctlg_no": 1, "size": "92g", "count": "12개", "disp": "미역국 92g 12개",
           "has_insight": True,
           "insight": _insight_with_evidence("미역국 96g 12개", "미역국 96g 리뷰", "미역국 96g")}
    ctx = _ctx(cat, opts={"llm_gate": True, "gate_fn": lambda d, t: (_ for _ in ()).throw(
        AssertionError("휴리스틱으로 확정 시 LLM 호출 금지"))})
    assert V.detect_source_mismatch(ctx) is not None


def test_source_mismatch_match_is_noop():
    cat = {"ctlg_no": 2, "size": "92g", "count": "12개", "disp": "미역국 92g 12개",
           "has_insight": True,
           "insight": _insight_with_evidence("미역국 92g 12개", "미역국 92g 후기")}
    ctx = _ctx(cat, opts={"llm_gate": True, "gate_fn": lambda d, t: True})
    assert V.detect_source_mismatch(ctx) is None


def test_source_mismatch_ambiguous_uses_gate():
    # evidence에 용량 없음 → 애매 → 게이트가 False면 mismatch.
    cat = {"ctlg_no": 3, "size": "92g", "count": "12개", "disp": "미역국 92g 12개",
           "has_insight": True,
           "insight": _insight_with_evidence("맛있는 국수 후기", "국수 리뷰")}
    ctx_bad = _ctx(cat, opts={"llm_gate": True, "gate_fn": lambda d, t: False})
    assert V.detect_source_mismatch(ctx_bad) is not None
    ctx_ok = _ctx(cat, opts={"llm_gate": True, "gate_fn": lambda d, t: True})
    assert V.detect_source_mismatch(ctx_ok) is None


def test_source_mismatch_gate_disabled_passes_ambiguous():
    cat = {"ctlg_no": 4, "size": "92g", "count": "12개", "disp": "미역국 92g 12개",
           "has_insight": True,
           "insight": _insight_with_evidence("국수 후기")}
    ctx = _ctx(cat, opts={"llm_gate": False})
    assert V.detect_source_mismatch(ctx) is None


def test_source_mismatch_empty_insight_is_noop():
    # 빈 insight(dims 없음)는 검사 대상 아님.
    cat = {"ctlg_no": 5, "size": "92g", "insight": {"dims": [], "faqs": [], "n_sources": 0}}
    assert V.detect_source_mismatch(_ctx(cat, opts={"llm_gate": True})) is None


def test_fix_source_mismatch_invalidates_for_requeue():
    cat = {"ctlg_no": 6, "insight": {"dims": [{"dim": "x"}], "attempts": 1}}
    spec = V.fix_source_mismatch(_ctx(cat))
    up = spec["update"]["$set"]
    ins = up["catalogs.$[c].insight"]
    assert ins["dims"] == [] and ins["faqs"] == [] and ins["n_sources"] == 0
    assert ins["attempts"] == 2           # prev(1) + 1
    assert ins["invalidated"] == "source_mismatch"
    assert up["catalogs.$[c].has_insight"] is False
    assert spec["array_filters"] == [{"c.ctlg_no": 6}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -k source_mismatch -v`
Expected: FAIL — `AttributeError: ... has no attribute 'detect_source_mismatch'`

- [ ] **Step 3: Write minimal implementation**

`validate_insights.py`에 추가:
```python
from collections import Counter

_TOL = 1e-9  # 질량/부피 동일성은 정규화 후 사실상 정확 비교(92 != 96 → mismatch)


def evidence_texts(insight):
    out = []
    for d in insight.get("dims") or []:
        for p in d.get("points") or []:
            for e in p.get("evidence") or []:
                out.append(f"{e.get('title', '')} {e.get('quote', '')}")
    for f in insight.get("faqs") or []:
        for e in (f.get("answer_evidence") or []) + (f.get("question_evidence") or []):
            out.append(f"{e.get('title', '')} {e.get('quote', '')}")
    return out


def _dominant(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None, 0.0
    val, n = Counter(vals).most_common(1)[0]
    return val, n / len(vals)


def detect_source_mismatch(ctx):
    ins = ctx["insight"] or {}
    if not (ins.get("dims")):          # 빈/부재 insight는 대상 아님
        return None
    cat = catalog_qty(ctx["catalog"])
    texts = evidence_texts(ins)
    if not texts:
        return None
    evq = [parse_qty(t) for t in texts]
    # 1) 휴리스틱: catalog에 값이 있고 evidence 다수(과반)가 명확히 다른 값이면 mismatch.
    for dim in ("mass", "vol", "count"):
        cv = cat.get(dim)
        if cv is None:
            continue
        dom, frac = _dominant([q[dim] for q in evq])
        if dom is not None and frac >= 0.5 and abs(dom - cv) > _TOL:
            return f"{dim}: catalog={cv} vs evidence dominant={dom} ({frac:.0%})"
    # 2) 애매(catalog 값에 대응하는 evidence 숫자가 전무) → LLM 게이트.
    has_evidence_qty = any(q["mass"] or q["vol"] or q["count"] for q in evq)
    if not has_evidence_qty and ctx["opts"].get("llm_gate"):
        gate = ctx["opts"].get("gate_fn")
        if gate is not None:
            try:
                if gate(ctx["disp"], texts) is False:
                    return "llm-gate: different product"
            except Exception:
                return None            # 게이트 실패 → 보수적 통과
    return None


def fix_source_mismatch(ctx):
    prev = (ctx["insight"] or {}).get("attempts") or 0
    empty = {"dims": [], "faqs": [], "n_sources": 0, "attempts": prev + 1,
             "invalidated": "source_mismatch", "fetched_at": now_iso(),
             "source": "naver_review"}
    return {"filter": {"_id": ctx["pkg_uid"]},
            "update": {"$set": {"catalogs.$[c].insight": empty,
                                "catalogs.$[c].has_insight": False}},
            "array_filters": _af(ctx["ctlg_no"])}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -k source_mismatch -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add insight/db/validate_insights.py insight/db/test_validate_insights.py
git commit -m "feat(validate): source_mismatch rule (heuristic + LLM gate)"
```

---

## Task 4: `stale_schema` 규칙 (감지만)

**Files:**
- Modify: `insight/db/validate_insights.py`
- Test: `insight/db/test_validate_insights.py`

**Interfaces:**
- Consumes: `ctx` 형태.
- Produces: `detect_stale_schema(ctx) -> str|None` (fix 없음).

- [ ] **Step 1: Write the failing test**

```python
def test_stale_schema_missing_fetched_at():
    cat = {"ctlg_no": 1, "insight": {"dims": [{"dim": "x"}], "source": "naver_review"}}
    assert V.detect_stale_schema(_ctx(cat)) is not None


def test_stale_schema_missing_source():
    cat = {"ctlg_no": 2, "insight": {"dims": [{"dim": "x"}], "fetched_at": "2026-06-25T00:00:00+00:00"}}
    assert V.detect_stale_schema(_ctx(cat)) is not None


def test_stale_schema_complete_is_noop():
    cat = {"ctlg_no": 3, "insight": {"dims": [{"dim": "x"}],
                                      "fetched_at": "2026-06-25T00:00:00+00:00",
                                      "source": "naver_review"}}
    assert V.detect_stale_schema(_ctx(cat)) is None


def test_stale_schema_empty_insight_is_noop():
    cat = {"ctlg_no": 4, "insight": {"dims": [], "faqs": []}}
    assert V.detect_stale_schema(_ctx(cat)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -k stale_schema -v`
Expected: FAIL — `AttributeError: ... has no attribute 'detect_stale_schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# --- R3: stale_schema (감지만, autofix 없음) ------------------------------
def detect_stale_schema(ctx):
    ins = ctx["insight"] or {}
    if not ins.get("dims"):            # 비어있지 않은 insight만 대상
        return None
    missing = [k for k in ("fetched_at", "source") if not ins.get(k)]
    if missing:
        return f"missing fields: {','.join(missing)}"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -k stale_schema -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add insight/db/validate_insights.py insight/db/test_validate_insights.py
git commit -m "feat(validate): stale_schema rule (report-only)"
```

---

## Task 5: 레지스트리 + 러너 + LLM 게이트 + CLI

**Files:**
- Modify: `insight/db/validate_insights.py`
- Test: `insight/db/test_validate_insights.py`

**Interfaces:**
- Consumes: 세 규칙의 detect/fix, `Rule`.
- Produces:
  - `RULES: list[Rule]` — flag_drift, source_mismatch, stale_schema.
  - `iter_contexts(db) -> generator` — `products.find({"type":"package"})`의 각 catalog(ctlg_no 있는 것)마다 ctx dict(단 `opts`는 러너가 주입).
  - `run(db, opts) -> dict` — 순회·규칙 적용·리포트. 반환 `{"summary": {...}, "violations": [...]}`.
  - `make_gate(model) -> callable` — `gpt-4o-mini` LLM 게이트 `(disp, texts)->bool`. openai 실패 시 예외를 올림(detect가 보수적 통과 처리).
  - `main()` — CLI.

- [ ] **Step 1: Write the failing test (mongomock 통합)**

```python
import mongomock


def _seed_db():
    db = mongomock.MongoClient().db
    db.products.insert_one({
        "_id": "P1", "type": "package",
        "catalogs": [
            # flag_drift: insight 있는데 has_insight=False
            {"ctlg_no": 100, "has_insight": False, "size": "92g", "count": "12개",
             "disp": "미역국 92g 12개",
             "insight": {"dims": [{"dim": "aspect.taste",
                                   "points": [{"point": "p",
                                               "evidence": [{"title": "미역국 92g", "quote": ""}]}]}],
                         "faqs": [], "n_sources": 1,
                         "fetched_at": "2026-06-25T00:00:00+00:00", "source": "naver_review"}},
            # source_mismatch: 92g인데 evidence 96g
            {"ctlg_no": 200, "has_insight": True, "size": "92g", "count": "12개",
             "disp": "미역국 92g 12개",
             "insight": {"dims": [{"dim": "aspect.taste",
                                   "points": [{"point": "p", "evidence": [
                                       {"title": "미역국 96g", "quote": ""},
                                       {"title": "미역국 96g 후기", "quote": ""}]}]}],
                         "faqs": [], "n_sources": 2,
                         "fetched_at": "2026-06-25T00:00:00+00:00", "source": "naver_review"}},
        ],
    })
    return db


def test_run_detects_and_fixes():
    db = _seed_db()
    rep = V.run(db, {"limit": 0, "dry_run": False, "rules": None, "llm_gate": False})
    ids = {(v["rule_id"], v["ctlg_no"]) for v in rep["violations"]}
    assert ("flag_drift", 100) in ids
    assert ("source_mismatch", 200) in ids
    doc = db.products.find_one({"_id": "P1"})
    c100 = next(c for c in doc["catalogs"] if c["ctlg_no"] == 100)
    c200 = next(c for c in doc["catalogs"] if c["ctlg_no"] == 200)
    assert c100["has_insight"] is True                      # flag 수정됨
    assert c200["insight"]["invalidated"] == "source_mismatch"  # 무효화됨
    assert c200["has_insight"] is False


def test_run_dry_run_makes_no_writes():
    db = _seed_db()
    V.run(db, {"limit": 0, "dry_run": True, "rules": None, "llm_gate": False})
    doc = db.products.find_one({"_id": "P1"})
    c100 = next(c for c in doc["catalogs"] if c["ctlg_no"] == 100)
    assert c100["has_insight"] is False                    # 변경 없음


def test_run_rules_filter():
    db = _seed_db()
    rep = V.run(db, {"limit": 0, "dry_run": True, "rules": ["flag_drift"],
                     "llm_gate": False})
    assert {v["rule_id"] for v in rep["violations"]} == {"flag_drift"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -k run_ -v`
Expected: FAIL — `AttributeError: ... has no attribute 'run'`

- [ ] **Step 3: Write minimal implementation**

`validate_insights.py`에 추가 (규칙 정의들 뒤):
```python
RULES = [
    Rule("flag_drift", "low", detect_flag_drift, fix_flag_drift),
    Rule("source_mismatch", "high", detect_source_mismatch, fix_source_mismatch),
    Rule("stale_schema", "low", detect_stale_schema, None),
]


def iter_contexts(db):
    for p in db.products.find({"type": "package"}, {"_id": 1, "catalogs": 1}):
        for c in p.get("catalogs") or []:
            if not c.get("ctlg_no"):
                continue
            yield {"db": db, "pkg_uid": p["_id"], "ctlg_no": c.get("ctlg_no"),
                   "disp": c.get("disp"), "catalog": c, "insight": c.get("insight"),
                   "opts": None}


def make_gate(model):
    """gpt-4o-mini 게이트 (disp, texts)->bool. 실패 시 예외를 올린다."""
    from openai import OpenAI
    client = OpenAI()

    def gate(disp, texts):
        joined = "\n".join(f"- {t[:200]}" for t in texts[:8])
        r = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "system",
                       "content": "제품명과 리뷰 근거가 같은 상품인지 판정. yes 또는 no만 답해라."},
                      {"role": "user",
                       "content": f"제품명: {disp}\n근거:\n{joined}\n\n같은 상품인가? yes/no"}])
        return r.choices[0].message.content.strip().lower().startswith("y")
    return gate


def run(db, opts):
    enabled = [r for r in RULES if not opts.get("rules") or r.id in opts["rules"]]
    limit = opts.get("limit") or 0
    violations = []
    summary = Counter()
    n = 0
    for ctx in iter_contexts(db):
        if limit and n >= limit:
            break
        n += 1
        ctx["opts"] = opts
        for rule in enabled:
            detail = rule.detect(ctx)
            if not detail:
                continue
            fixed = False
            if rule.fix and not opts.get("dry_run"):
                spec = rule.fix(ctx)
                db.products.update_one(spec["filter"], spec["update"],
                                       array_filters=spec["array_filters"])
                fixed = True
            summary[rule.id] += 1
            violations.append({"rule_id": rule.id, "severity": rule.severity,
                               "pkg_uid": ctx["pkg_uid"], "ctlg_no": ctx["ctlg_no"],
                               "disp": ctx["disp"], "detail": detail, "fixed": fixed})
    return {"summary": {"scanned": n, "by_rule": dict(summary),
                        "total": len(violations)},
            "violations": violations}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="처리 카탈로그 수(0=전체)")
    ap.add_argument("--dry-run", action="store_true", help="감지만, 수정 안 함")
    ap.add_argument("--rules", default="", help="쉼표구분 규칙 id (기본: 전체)")
    ap.add_argument("--llm-gate", dest="llm_gate", action="store_true", default=True)
    ap.add_argument("--no-llm-gate", dest="llm_gate", action="store_false")
    args = ap.parse_args()

    from pymongo import MongoClient
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]

    opts = {"limit": args.limit, "dry_run": args.dry_run,
            "rules": [r for r in args.rules.split(",") if r] or None,
            "llm_gate": args.llm_gate}
    if args.llm_gate and not args.dry_run and os.environ.get("OPENAI_API_KEY"):
        opts["gate_fn"] = make_gate(os.environ.get("INSIGHT_MODEL", "gpt-4o-mini"))

    rep = run(db, opts)

    here = os.path.dirname(os.path.abspath(__file__))
    exdir = os.path.join(here, "exports")
    os.makedirs(exdir, exist_ok=True)
    ts = now_iso().replace(":", "").replace("-", "")[:15]
    out = os.path.join(exdir, f"validation_report_{ts}.json")
    with open(out, "w", encoding="utf-8") as w:
        json.dump(rep, w, ensure_ascii=False, indent=2)
    rep["report"] = out
    print(json.dumps(rep["summary"] | {"report": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run full test suite**

Run: `cd insight/db && python3 -m pytest test_validate_insights.py -v`
Expected: PASS (전체 22 passed)

- [ ] **Step 5: Commit**

```bash
git add insight/db/validate_insights.py insight/db/test_validate_insights.py
git commit -m "feat(validate): rule registry, runner, LLM gate, CLI + report"
```

---

## Task 6: `step_validate.sh` 래퍼 + 스모크 실행

**Files:**
- Create: `insight/db/step_validate.sh`

**Interfaces:**
- Consumes: `validate_insights.py` CLI.
- Produces: n8n/수동 1회 실행 진입점. JSON 요약을 stdout으로.

- [ ] **Step 1: Write the script**

`insight/db/step_validate.sh`:
```zsh
#!/bin/zsh
# 인사이트 검증+autofix 1회 — n8n 버튼/수동용. 규칙(flag_drift/source_mismatch/stale_schema)을
# products.catalogs[].insight 에 적용하고 JSON 요약 반환. 리포트: exports/validation_report_*.json
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT" || exit 1
export MONGO_URI="${MONGO_URI:-mongodb://localhost:47017/?directConnection=true}"
export INSIGHTS_DB="${INSIGHTS_DB:-insights_demo}"
PY=${PY:-/usr/bin/python3}
# OPENAI 키 로드(LLM 게이트용) — 없으면 게이트는 자동 비활성(휴리스틱만).
set -a; eval "$(grep -E '^export OPENAI_' run.sh 2>/dev/null)"; set +a
mkdir -p exports
RES="$("$PY" db/validate_insights.py "$@" 2>>exports/validate.log)"
[ -z "$RES" ] && RES='{"stage":"validate","error":"fail"}'
echo "$RES"
```

- [ ] **Step 2: chmod + dry-run 스모크 (실 DB)**

Run:
```bash
cd insight/db && chmod +x step_validate.sh && \
INSIGHTS_DB=insights_demo ./step_validate.sh --dry-run --limit 200
```
Expected: `{"scanned": <N>, "by_rule": {...}, "total": <M>, "report": ".../validation_report_*.json"}` 형태 JSON. dry-run이라 DB 변경 없음. (실데이터에서 앞서 발견한 P7863 ctlg 529046533의 flag_drift가 리포트에 잡히는지 확인.)

- [ ] **Step 3: 리포트 내용 확인**

Run: `cat insight/db/exports/validation_report_*.json | python3 -m json.tool | head -40`
Expected: `violations[]`에 `flag_drift`/`source_mismatch` 항목, 각 `fixed: false`(dry-run).

- [ ] **Step 4: Commit**

```bash
git add insight/db/step_validate.sh
git commit -m "feat(validate): step_validate.sh one-shot wrapper"
```

---

## Self-Review (완료됨)

- **Spec coverage:** flag_drift(R1)=Task2, source_mismatch(R2, 휴리스틱+LLM게이트+재수집 무효화)=Task3, stale_schema(R3)=Task4, 프레임워크/CLI/리포트/`--dry-run`/`--rules`/`--no-llm-gate`=Task5, step 래퍼=Task6, 파서=Task1. 스펙의 에러 처리(게이트 실패 보수적 통과)=Task3 detect. ✓ 전 항목 커버.
- **Placeholder scan:** 모든 코드 스텝에 실제 코드 포함, TBD/TODO 없음. ✓
- **Type consistency:** `ctx` 키(`db/pkg_uid/ctlg_no/disp/catalog/insight/opts`)는 Task2에서 정의 후 Task3/4/5에서 동일 사용. `fix`는 `{"filter","update","array_filters"}` dict 반환으로 Task2/3와 Task5 러너의 `update_one(spec["filter"], spec["update"], array_filters=...)` 호출이 일치. `detect`는 `str|None` 반환으로 통일. R2 무효화 상태가 `catalog_insight_backfill` 재큐 조건(`not dims and n_sources==0`)과 일치. ✓
- **B의 정직한 한계**(같은 키워드 재수집 → attempts 한도 후 empty 수렴)는 스펙에 문서화됨, 계획은 무효화까지만 담당(재수집은 기존 루프 소관). ✓
