#!/usr/bin/env python3
"""identity_seed_match.match_seed_to_extracted 단위 테스트 (T4). Mongo/파일 비의존.

강키 우선 + 이름 폴백 + 미달 unmatched + uid 스탬프를 검증.
실행: python3 insight/db/test_identity_seed_match.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from identity_seed_match import match_seed_to_extracted


def test_strong_key_match_beats_name():
    # style_code 가 양쪽에 있으면 이름이 달라도 강키로 확정.
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "style_code": "KK1334", "disp": "전혀 다른 이름"}]
    ext = [{"style_code": "KK1334", "name": "나이키 에어맥스", "brand": "나이키"}]
    out = match_seed_to_extracted(seed, ext)
    assert len(out) == 1
    assert out[0]["insight_uid"] == "P1" and out[0]["ctlg_no"] == "C1"
    assert out[0]["brand"] == "나이키"
    assert out[0]["_match"] == "key:style_code"


def test_name_fallback_when_no_strong_key():
    seed = [{"insight_uid": "P2", "ctlg_no": "C9", "disp": "쿡시 미역국"}]
    ext = [{"name": "쿡시 미역국 490g", "brand": "쿡시", "style_code": ""}]
    out = match_seed_to_extracted(seed, ext, name_thresh=0.4)
    assert len(out) == 1
    assert out[0]["insight_uid"] == "P2"
    assert out[0]["_match"].startswith("name:")


def test_below_threshold_unmatched():
    seed = [{"insight_uid": "P3", "ctlg_no": "C1", "disp": "완전히 무관한 상품명 XYZ"}]
    ext = [{"name": "나이키 운동화", "brand": "나이키"}]
    out = match_seed_to_extracted(seed, ext, name_thresh=0.4)
    assert out == []                         # 미매칭 → 출력 없음(→ backfill status:empty)


def test_strong_key_priority_over_name():
    # 이름은 ext[1] 에 가깝지만 style_code 는 ext[0] → 강키 승.
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "style_code": "S1", "disp": "비슷한 이름 알파"}]
    ext = [{"style_code": "S1", "name": "전혀 다른 베타", "brand": "A"},
           {"style_code": "S2", "name": "비슷한 이름 알파", "brand": "B"}]
    out = match_seed_to_extracted(seed, ext)
    assert out[0]["brand"] == "A" and out[0]["_match"] == "key:style_code"


def test_barcode_strong_key():
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "barcode": "8801234567890", "disp": "x"}]
    ext = [{"barcode": "8801234567890", "name": "어떤상품", "brand": "B"}]
    out = match_seed_to_extracted(seed, ext)
    assert out[0]["_match"] == "key:barcode" and out[0]["insight_uid"] == "P1"


def test_multiple_seeds_multiple_rows():
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "disp": "쿡시 미역국"},
            {"insight_uid": "P2", "ctlg_no": "C2", "disp": "오뚜기 진라면"}]
    ext = [{"name": "쿡시 미역국 490g", "brand": "쿡시"},
           {"name": "오뚜기 진라면 매운맛", "brand": "오뚜기"}]
    out = match_seed_to_extracted(seed, ext)
    assert len(out) == 2
    assert {r["insight_uid"] for r in out} == {"P1", "P2"}


def test_category_columns_preserved_in_stamp():
    # 산출의 카테고리별 컬럼(어떤 것이든)이 스탬프 후에도 보존(category-agnostic).
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "style_code": "S1", "disp": "x"}]
    ext = [{"style_code": "S1", "name": "n", "origin": "베트남", "abv": "4.5%"}]
    out = match_seed_to_extracted(seed, ext)
    assert out[0]["origin"] == "베트남" and out[0]["abv"] == "4.5%"


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
