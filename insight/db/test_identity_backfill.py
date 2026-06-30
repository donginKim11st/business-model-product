#!/usr/bin/env python3
"""identity_backfill.build_identity_update 단위 테스트 (T2, category-agnostic).

어떤 카테고리의 정형 CSV 든 컬럼 그대로 수용 + per-SKU/상품레벨 합류 + status enum +
기존 catalog 필드(price_summary) 보존을 검증. Mongo 비의존.

실행: python3 insight/db/test_identity_backfill.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from identity_backfill import build_identity_update, _row_sku_identity

FA = "2026-06-30T00:00:00Z"


def test_apparel_row_matched_by_ctlg():
    cats = [{"ctlg_no": "C1", "disp": "신발", "price_summary": {"min": 1000}}]
    rows = [{"insight_uid": "P1", "ctlg_no": "C1", "brand": "나이키",
             "style_code": "KK1334", "color": "검정",
             "origin": "베트남", "material": "가죽", "mfg_date": "2026-01"}]
    cats2, ident = build_identity_update(cats, rows, fetched_at=FA)
    c = cats2[0]
    assert c["identity"]["style_code"] == "KK1334"
    assert c["identity"]["color"] == "검정"
    assert c["identity"]["gosi"] == {"origin": "베트남", "material": "가죽", "mfg_date": "2026-01"}
    assert c["price_summary"] == {"min": 1000}      # 기존 필드 보존(키 단위 set)
    assert ident == {"brand": "나이키", "status": "done", "n_facts": 1, "fetched_at": FA}


def test_food_category_columns_accommodated():
    # 식품: 다른 고시 컬럼(food_type/ingredients/expiry) — 하드코딩 없이 수용.
    cats = [{"ctlg_no": "F9", "disp": "미역국"}]
    rows = [{"insight_uid": "P2", "ctlg_no": "F9", "brand": "쿡시",
             "food_type": "즉석국", "ingredients": "미역,소고기", "expiry": "제조일+12개월"}]
    cats2, ident = build_identity_update(cats, rows, fetched_at=FA)
    g = cats2[0]["identity"]["gosi"]
    assert g == {"food_type": "즉석국", "ingredients": "미역,소고기", "expiry": "제조일+12개월"}
    assert ident["brand"] == "쿡시" and ident["status"] == "done"


def test_unknown_category_column_passthrough():
    # 미지 카테고리 컬럼(주류 abv 등)은 GOSI_HINT 에 없어도 top-level 로 통과.
    d = _row_sku_identity({"insight_uid": "P", "ctlg_no": "X", "abv": "4.5%", "style_code": "S1"}, FA)
    assert d["abv"] == "4.5%"
    assert d["style_code"] == "S1"
    assert "gosi" not in d                            # abv 는 힌트 밖 → top-level


def test_empty_rows_status_empty():
    cats = [{"ctlg_no": "C1"}]
    cats2, ident = build_identity_update(cats, [], fetched_at=FA)
    assert ident == {"brand": None, "status": "empty", "n_facts": 0, "fetched_at": FA}
    assert "identity" not in cats2[0]                 # 빈 산출 → catalog 미변경


def test_non_matching_ctlg_is_product_level():
    cats = [{"ctlg_no": "C1"}]
    rows = [{"insight_uid": "P1", "ctlg_no": "ZZZ", "brand": "B"}]   # 매칭 catalog 없음
    cats2, ident = build_identity_update(cats, rows, fetched_at=FA)
    assert "identity" not in cats2[0]                 # SKU 미부착
    assert ident["brand"] == "B" and ident["n_facts"] == 1 and ident["status"] == "done"


def test_original_catalogs_not_mutated():
    cats = [{"ctlg_no": "C1", "price_summary": {"min": 5}}]
    build_identity_update(cats, [{"insight_uid": "P", "ctlg_no": "C1", "brand": "B", "color": "x"}], FA)
    assert "identity" not in cats[0]                  # 원본 in-place 변이 금지(복사본 반환)


def test_brand_from_first_nonempty():
    rows = [{"ctlg_no": "C1", "brand": ""}, {"ctlg_no": "C2", "brand": "리얼브랜드"}]
    cats = [{"ctlg_no": "C1"}, {"ctlg_no": "C2"}]
    _, ident = build_identity_update(cats, rows, FA)
    assert ident["brand"] == "리얼브랜드"


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
