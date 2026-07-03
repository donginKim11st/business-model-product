"""속성값 2차 검증(refine_attrs) — 추출된 옵션/속성값 안의 교차축 혼입·열거자 노이즈 정제.

사용자 요구(2026-07-03): 옵션 추출 후 속성값을 재검사해
  ① 한 값에 두 축이 든 경우 분리      ("60w 주광색" → watt=60W, cct=주광색)
  ② 열거자 노이즈 제거               ("A1. 원형방등 일반" → "원형방등 일반")
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from furniture_catalog import _clean_opt, _promote_option, refine_attrs


# ── _clean_opt: 열거자 프리픽스 ────────────────────────────────────────────────

def test_clean_opt_letter_digit_enum():
    assert _clean_opt("A1. 원형방등 일반") == "원형방등 일반"
    assert _clean_opt("D2. 사각 욕실등") == "사각 욕실등"


def test_clean_opt_digit_enum_kept_behavior():
    assert _clean_opt("1. 화이트") == "화이트"
    assert _clean_opt("1)흰색") == "흰색"


def test_clean_opt_decimal_not_enum():
    # 기존 버그: "1.5인용" → "5인용" 으로 소수점이 열거자로 오인 절단
    assert _clean_opt("1.5인용") == "1.5인용"


def test_clean_opt_single_letter_enum():
    assert _clean_opt("A. 방등") == "방등"


# ── _promote_option: 괄호 복합값 ──────────────────────────────────────────────

def test_promote_paren_composite():
    po = _promote_option("50W(주광색)")
    assert po["watt"] == "50W"
    assert po["cct"] == "주광색"
    assert not po["color"] and not po["option"]


# ── refine_attrs: 축 값 재검증 ────────────────────────────────────────────────

def test_refine_color_holding_watt_cct():
    assert refine_attrs({"color": "50W 주광색"}) == {"watt": "50W", "cct": "주광색"}


def test_refine_option_two_axes_and_axisword():
    d = refine_attrs({"option": "60w 주광색 색상"})
    assert d.get("watt") == "60W"
    assert d.get("cct") == "주광색"
    assert not d.get("option")  # 축명 잔여어 "색상"도 노이즈로 제거


def test_refine_option_enum_noise():
    assert refine_attrs({"option": "A1. 원형방등 일반"}) == {"option": "원형방등 일반"}


def test_refine_color_with_name_prefix():
    d = refine_attrs({"option": "모모소파 블랙 색상"})
    assert d.get("color") == "블랙"
    assert d.get("option") == "모모소파"


def test_refine_keeps_clean_attrs():
    d = {"color": "블랙", "size": "Q", "watt": "50W"}
    assert refine_attrs(dict(d)) == d


def test_refine_conservative_on_unsplittable():
    # 자기 축 값을 재확인 못 하고 잔여도 있으면 원값 유지(정보 파괴 금지)
    d = refine_attrs({"size": "싱글 저상형 일체형매트"})
    assert d.get("size") == "싱글 저상형 일체형매트"


def test_refine_does_not_clobber_existing_axis():
    # cct 가 이미 있으면 option 의 중복 색온도 토큰은 버리고 잔여만 남김
    d = refine_attrs({"cct": "주광색", "option": "일자등 600mm 주광색"})
    assert d.get("cct") == "주광색"
    assert "주광색" not in d.get("option", "")
