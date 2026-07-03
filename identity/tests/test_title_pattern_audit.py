"""전수 패턴 감사(2026-07-03) 교정 — title_geo 노이즈 5종.

감사 실측: 브랜드중복 21 · 열거자(N1.) 8 · form 슬래시열거 771 중 ~형 열거 ·
실링팬 치수 628 · 수량/행사(개입·1+1, LLM canonical 캐시 잔존 포함) 148.
비조명 와트(충전기 65W)·시리즈명(베스트)은 정당 스펙이라 보존.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from furniture_catalog import _clean_title, extract_variants, title_geo


def test_title_brand_not_duplicated():
    # canonical 이 이미 브랜드로 시작하면 중복 부착 금지
    assert title_geo("도토로", "도토로 크리스마스 트리", "") == "도토로 크리스마스 트리"


def test_title_collab_preserved():
    # 콜라보("잠자리X동서가구")는 브랜드 선두가 아니므로 기존 조립 유지
    t = title_geo("동서가구", "잠자리X동서가구 천연라텍스 베개", "")
    assert t.startswith("동서가구 잠자리X동서가구")


def test_clean_title_letter_enum():
    assert _clean_title("N1. 스카이 알루미늄") == "스카이 알루미늄"
    # 다문자 영문+숫자(모델명 PAR30)는 보존
    assert "PAR30" in _clean_title("PAR30 램프")


def test_title_geo_pack_scrubbed():
    # LLM canonical 캐시에 남은 수량/행사 표기는 title_geo 조립에서 최종 제거
    assert title_geo("테스트상표", "알카라인 건전지 12개입", "") == "테스트상표 알카라인 건전지"
    assert title_geo("테스트상표", "계란말이 주걱 1+1", "") == "테스트상표 계란말이 주걱"


def test_form_slash_enum_to_axis():
    n, va = extract_variants("크렌시아 저상형/일반형 일체형 매트리스", "bed", {})
    assert va["form"] == "저상형/일반형"   # "/" = 변형 전개 단위
    assert n == "크렌시아 일체형 매트리스"


def test_form_slash_requires_all_hyeong():
    # "멀바우/본넬"(소재)·"라온/리노"(모델 열거)는 form 아님 — 보존
    n, va = extract_variants("바오트 멀바우/본넬 매트리스", "bed", {})
    assert "form" not in va
    assert "멀바우/본넬" in n


def test_ceiling_fan_cm_extracted():
    n, va = extract_variants("실링팬 BLDC 135cm 52인치 저소음", "lighting", {})
    assert va["cm"] == "135"
    assert "cm" not in n and "인치" not in n  # cm 추출 시 중복 인치 표기도 제거
    assert "실링팬 BLDC" in n


def test_ceiling_fan_cm_category_agnostic():
    # 몰 카테고리 미분류(l1 공백 → cc=etc)여도 실링팬 키워드로 추출
    n, va = extract_variants("실링팬 BLDC 135cm 52인치 저소음 거실 천장형 선풍기", "etc", {})
    assert va["cm"] == "135"
    assert "인치" not in n


def test_ceiling_fan_decimal_cm():
    n, va = extract_variants("에어룩스 울트라 실링팬 136.9cm", "etc", {})
    assert va["cm"] == "136.9"
    assert "cm" not in n


def test_offer_one_plus_one():
    n, va = extract_variants("계란말이 주걱 1+1", "etc", {})
    assert va["offer"] == "1+1"
    assert n == "계란말이 주걱"
