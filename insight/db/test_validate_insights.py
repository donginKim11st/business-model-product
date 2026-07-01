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


# --- Task 3: source_mismatch rule -------------------------------------------

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


# --- Task 4: stale_schema rule (감지만) -----------------------------------

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
