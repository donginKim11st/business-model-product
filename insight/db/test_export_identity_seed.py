#!/usr/bin/env python3
"""export_identity_seed.product_to_seed_rows 단위 테스트 (T3). Mongo 비의존.

실행: python3 insight/db/test_export_identity_seed.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from export_identity_seed import product_to_seed_rows, SEED_COLUMNS


def test_package_emits_row_per_catalog():
    p = {"_id": "P1", "keyword": "미역국", "category_l1": "국·탕·찌개",
         "catalogs": [{"ctlg_no": "C1", "disp": "미역국 490g"},
                      {"ctlg_no": "C2", "disp": "미역국 490g 3개"}]}
    rows = product_to_seed_rows(p)
    assert len(rows) == 2
    assert rows[0] == {"insight_uid": "P1", "ctlg_no": "C1", "keyword": "미역국",
                       "category_l1": "국·탕·찌개", "disp": "미역국 490g"}
    assert rows[1]["ctlg_no"] == "C2"
    assert set(rows[0].keys()) == set(SEED_COLUMNS)


def test_ctlg_no_coerced_to_str():
    p = {"_id": "P1", "keyword": "x", "category_l1": "c", "catalogs": [{"ctlg_no": 12345, "disp": "d"}]}
    assert product_to_seed_rows(p)[0]["ctlg_no"] == "12345"


def test_catalog_without_ctlg_skipped_then_product_level():
    p = {"_id": "P2", "keyword": "kw", "category_l1": "c",
         "catalogs": [{"ctlg_no": None, "disp": "no-sku"}]}
    rows = product_to_seed_rows(p)
    assert len(rows) == 1
    assert rows[0]["ctlg_no"] is None
    assert rows[0]["disp"] == "kw"           # 상품 레벨은 keyword 로 폴백


def test_no_catalogs_product_level_row():
    p = {"_id": "P3::대", "keyword": "변형상품", "category_l1": "면류·라면"}
    rows = product_to_seed_rows(p)
    assert rows == [{"insight_uid": "P3::대", "ctlg_no": None, "keyword": "변형상품",
                     "category_l1": "면류·라면", "disp": "변형상품"}]


def test_category_passthrough_any_value():
    # category-agnostic: 어떤 category_l1 이든 그대로 전달(분기 없음).
    for cat in ["스포츠의류", "가전", "주류", None]:
        p = {"_id": "P", "keyword": "k", "category_l1": cat, "catalogs": [{"ctlg_no": "C", "disp": "d"}]}
        assert product_to_seed_rows(p)[0]["category_l1"] == cat


def test_disp_falls_back_to_keyword():
    p = {"_id": "P", "keyword": "키워드", "category_l1": "c", "catalogs": [{"ctlg_no": "C"}]}
    assert product_to_seed_rows(p)[0]["disp"] == "키워드"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
