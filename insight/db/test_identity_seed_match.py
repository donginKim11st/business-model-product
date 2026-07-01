#!/usr/bin/env python3
"""identity_seed_match.match_seed_to_extracted 단위 테스트 (T4). Mongo/파일 비의존.

강키 우선 + 이름 폴백 + 미달 unmatched + uid 스탬프를 검증.
실행: python3 insight/db/test_identity_seed_match.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from identity_seed_match import (match_seed_to_extracted, resolve_thresh, DEFAULT_NAME_THRESH,
                                 domain_of, _content_recall)

_DMAP = {"식품": ["음료", "라면"], "의류·신발": ["의류", "신발"]}


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


def test_resolve_thresh_category_specific():
    m = {"default": 0.4, "식품": 0.5, "신발": 0.35}
    assert resolve_thresh("식품", m) == 0.5
    assert resolve_thresh("신발", m) == 0.35
    assert resolve_thresh("미등록카테고리", m) == 0.4      # map default
    assert resolve_thresh("식품", None) == DEFAULT_NAME_THRESH  # map 없으면 인자 default
    assert resolve_thresh("x", {}, 0.6) == 0.6              # 빈 map → 인자 default


def test_per_category_threshold_changes_match():
    # 동일 disp↔name(중간 점수)이 카테고리별 임계에 따라 매칭/미매칭 갈림.
    seed_strict = [{"insight_uid": "P1", "ctlg_no": "C1", "category_l1": "식품", "disp": "미역국 사골"}]
    seed_loose = [{"insight_uid": "P2", "ctlg_no": "C2", "category_l1": "신발", "disp": "미역국 사골"}]
    ext = [{"name": "미역국 진한맛", "brand": "B"}]   # disp 와 일부만 겹침(중간 recall)
    # 같은 후보에 대해 엄격(0.9)이면 미매칭, 느슨(0.1)이면 매칭.
    tmap = {"식품": 0.9, "신발": 0.1}
    assert match_seed_to_extracted(seed_strict, ext, thresh_map=tmap) == []
    out = match_seed_to_extracted(seed_loose, ext, thresh_map=tmap)
    assert len(out) == 1 and out[0]["insight_uid"] == "P2"


def test_method_tag_records_threshold():
    seed = [{"insight_uid": "P", "ctlg_no": "C", "category_l1": "신발", "disp": "쿡시 미역국"}]
    ext = [{"name": "쿡시 미역국 490g", "brand": "쿡시"}]
    out = match_seed_to_extracted(seed, ext, thresh_map={"신발": 0.2})
    assert out[0]["_match"].startswith("name:") and "@0.2" in out[0]["_match"]


def test_domain_of():
    assert domain_of("음료", _DMAP) == "식품"
    assert domain_of("스포츠의류", _DMAP) == "의류·신발"
    assert domain_of("가전", _DMAP) is None          # 미등록 → None(게이트 통과)
    assert domain_of("음료", None) is None            # 맵 없음 → None


def test_category_gate_blocks_cross_domain():
    # 음료(식품) 씨앗이 이름이 겹쳐도 의류 산출과 매칭 안 됨(게이트).
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "category_l1": "음료", "disp": "스파클 생수"}]
    ext = [{"name": "스파클 조개 슬리퍼", "brand": "크록스"}]
    out = match_seed_to_extracted(seed, ext, domain_map=_DMAP, extracted_domain="의류·신발")
    assert out == []                                  # 도메인 불일치 → 차단


def test_category_gate_permissive_on_unknown():
    # 미등록 카테고리(가전)는 도메인 None → 게이트 통과(이름 매칭 시도).
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "category_l1": "가전", "disp": "쿡시 미역국"}]
    ext = [{"name": "쿡시 미역국 490g", "brand": "쿡시"}]
    out = match_seed_to_extracted(seed, ext, domain_map=_DMAP, extracted_domain="의류·신발")
    assert len(out) == 1                              # 미등록 → 통과 → 이름 매칭


def test_category_gate_allows_same_domain():
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "category_l1": "스포츠의류", "disp": "나이키 운동화"}]
    ext = [{"name": "나이키 운동화 에어", "brand": "나이키"}]
    out = match_seed_to_extracted(seed, ext, domain_map=_DMAP, extracted_domain="의류·신발")
    assert len(out) == 1                              # 같은 도메인 → 매칭


def test_color_guard_color_only_seed_blocked():
    # 색상어뿐인 씨앗은 변별 토큰 없음 → 매칭 안 함.
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "category_l1": "가전", "disp": "그린"}]
    ext = [{"name": "스프레드 로고 슬리브리스 그린", "brand": "아웃도어"}]
    assert match_seed_to_extracted(seed, ext) == []


def test_color_word_not_match_basis():
    # 색상만 겹치는 교차상품은 content recall 0 → 매칭 안 됨(그린 제외).
    assert _content_recall("글로벌심층수 딥스 그린", "스프레드 로고 슬리브리스 그린") == 0.0
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "category_l1": "가전", "disp": "글로벌심층수 딥스 그린"}]
    ext = [{"name": "스프레드 로고 슬리브리스 그린", "brand": "아웃도어"}]
    assert match_seed_to_extracted(seed, ext) == []


def test_color_tiebreak_resolves_variant():
    # 같은 이름 다른 style_code(색 변형). 씨앗 color 로 올바른 변형 선택(C1).
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "disp": "에어맥스 270", "color": "블랙"}]
    ext = [{"name": "에어맥스 270", "style_code": "AM-WHT", "color": "화이트"},
           {"name": "에어맥스 270", "style_code": "AM-BLK", "color": "블랙"}]
    out = match_seed_to_extracted(seed, ext)
    assert out[0]["style_code"] == "AM-BLK"           # 색 일치 변형 선택
    assert "+color" in out[0]["_match"]


def test_size_tiebreak_resolves_variant():
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "disp": "에어맥스", "size": "270"}]
    ext = [{"name": "에어맥스", "style_code": "S-260", "sizes": "250|260"},
           {"name": "에어맥스", "style_code": "S-270", "sizes": "270|280"}]
    out = match_seed_to_extracted(seed, ext)
    assert out[0]["style_code"] == "S-270" and "+size" in out[0]["_match"]


def test_recall_dominates_over_color():
    # 더 높은 recall(이름 일치)이 색 일치보다 우선 — 색은 동률 tie-break 만.
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "disp": "쿡시 미역국", "color": "블랙"}]
    ext = [{"name": "쿡시 미역국 490g", "style_code": "A", "color": "화이트"},   # recall 높음, 색 불일치
           {"name": "전혀 다른 상품", "style_code": "B", "color": "블랙"}]        # recall 낮음, 색 일치
    out = match_seed_to_extracted(seed, ext)
    assert out[0]["style_code"] == "A"                # recall 지배


def test_no_color_falls_back_to_name():
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "disp": "쿡시 미역국"}]   # color 없음
    ext = [{"name": "쿡시 미역국 490g", "style_code": "A"}]
    out = match_seed_to_extracted(seed, ext)
    assert len(out) == 1 and out[0]["_match"].startswith("name:")   # +color/+size 없음


def test_strong_key_bypasses_gates():
    # 강키 매칭은 도메인 게이트/색상 가드 우회(권위).
    seed = [{"insight_uid": "P1", "ctlg_no": "C1", "category_l1": "음료", "style_code": "S1", "disp": "그린"}]
    ext = [{"style_code": "S1", "name": "전혀 다른 의류", "brand": "A"}]
    out = match_seed_to_extracted(seed, ext, domain_map=_DMAP, extracted_domain="의류·신발")
    assert len(out) == 1 and out[0]["_match"] == "key:style_code"


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
