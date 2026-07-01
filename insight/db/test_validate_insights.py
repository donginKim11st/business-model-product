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


# --- Task 5: registry + runner integration ------------------------------------
import re as _re
import mongomock


def _patch_mongomock_array_filters():
    """Translate $[alias] positional-filtered paths to indexed paths for mongomock."""
    import mongomock.collection as _mc
    _orig = _mc.Collection.update_one

    def _patched(self, filter_doc, update_doc, array_filters=None, **kw):
        if not array_filters:
            return _orig(self, filter_doc, update_doc, **kw)
        doc = self.find_one(filter_doc)
        if doc is None:
            return _orig(self, filter_doc, update_doc, **kw)
        alias_map = {}
        for af in array_filters:
            for k, v in af.items():
                parts = k.split('.', 1)
                if len(parts) == 2:
                    alias_map[parts[0]] = (parts[1], v)
        new_set = {}
        for path, value in (update_doc.get('$set') or {}).items():
            m = _re.match(r'^([\w]+)\.\$\[(\w+)\]\.(.+)$', path)
            if m and m.group(2) in alias_map:
                arr_name, alias, sub = m.group(1), m.group(2), m.group(3)
                mf, mv = alias_map[alias]
                for i, item in enumerate(doc.get(arr_name) or []):
                    if item.get(mf) == mv:
                        new_set[f'{arr_name}.{i}.{sub}'] = value
                        break
            else:
                new_set[path] = value
        new_upd = dict(update_doc)
        if new_set:
            new_upd['$set'] = new_set
        return _orig(self, filter_doc, new_upd, **kw)

    _mc.Collection.update_one = _patched


_patch_mongomock_array_filters()


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
