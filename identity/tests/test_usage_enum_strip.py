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
