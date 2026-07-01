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
