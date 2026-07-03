"""용도/등류 슬래시 열거군 제거(_strip_type_enum) — 상품명 정리.

사용자 요구(2026-07-03): "번개표 LED 주택등 모음 방등/주방등/거실등/욕실등/직부등/센서등"
→ "번개표 LED 주택등". 열거는 옵션 나열(변형은 옵션군·용도축이 담당)이므로
과반이 용도·등류 어휘인 열거군은 사전 밖 멤버(직부등/센서등)까지 통째 제거한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from furniture_catalog import _strip_type_enum


def test_usage_enum_fully_removed_including_oov():
    # 방등~욕실등은 용도 사전, 직부등/센서등은 사전 밖 — 과반 매칭이면 전체 제거
    assert _strip_type_enum(
        "LED 주택등 방등/주방등/거실등/욕실등/직부등/센서등") == "LED 주택등"


def test_light_type_only_enum_removed():
    assert _strip_type_enum("LED 조명 직부등/센서등") == "LED 조명"


def test_non_usage_enum_preserved():
    # 과반이 비용도(구성품 열거) — 보존
    assert _strip_type_enum("책상/의자 세트") == "책상/의자 세트"


def test_size_enum_preserved():
    assert _strip_type_enum("수납침대 SS/Q 프레임") == "수납침대 SS/Q 프레임"


def test_single_usage_untouched():
    # 열거가 아닌 단독 용도는 이 헬퍼 소관 아님(기존 첫개유지 로직 소관)
    assert _strip_type_enum("LED 주방등 50W") == "LED 주방등 50W"


def test_unit_enum_removed():
    # 같은 단위 반복 슬래시 런("3인치/4인치/…")은 모음 열거 — 변형은 옵션군이 보유
    assert _strip_type_enum("LED 다운라이트 3인치/4인치/6인치/8인치 회전매입등") == \
        "LED 다운라이트 회전매입등"
    assert _strip_type_enum("그레이스 25mm/35mm 브라켓세트") == "그레이스 브라켓세트"
    assert _strip_type_enum("고급 PE 전나무 120cm/150cm") == "고급 PE 전나무"


def test_unit_enum_attached_to_korean():
    # 한글에 붙은 열거("전나무120cm/150cm")도 제거 — 숫자/영문 선행(1100x2000mm)은 보존
    assert _strip_type_enum("최고급 전나무120cm/150cm 세트") == "최고급 전나무 세트"


def test_unit_single_and_mixed_preserved():
    assert _strip_type_enum("실링팬 135cm") == "실링팬 135cm"   # 단독 치수는 축 추출 소관
    # 단위가 섞인 표기(치수 스펙 "1100x2000mm")는 열거가 아님 — 보존
    assert "1100x2000mm" in _strip_type_enum("침대 1100x2000mm 프레임")
