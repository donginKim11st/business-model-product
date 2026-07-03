"""와트 열거 추출 — 공백 열거("10W 15W 20W")도 슬래시 런과 동일하게 전량 추출.

사용자 요구(2026-07-03): "번개표 LED 컴팩트 램프 10W 15W 20W" → title_geo "번개표 LED 컴팩트 램프".
첫 개만 추출하면 잔여("15W 20W")가 모델명·카탈로그키·title_geo 에 박힌다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from furniture_catalog import extract_variants


def test_watt_space_enum_fully_extracted():
    n, va = extract_variants("LED 컴팩트 램프 10W 15W 20W", "lighting", {})
    assert va["watt"] == "10W/15W/20W"  # "/" 조인 → _split_axis 가 변형 전개
    assert n == "LED 컴팩트 램프"


def test_watt_slash_enum_unchanged():
    n, va = extract_variants("LED 몬드 방등 60W/80W", "lighting", {})
    assert va["watt"] == "60W/80W"
    assert "W" not in n


def test_watt_single():
    n, va = extract_variants("LED 방등 50W", "lighting", {})
    assert va["watt"] == "50W"
    assert n == "LED 방등"


def test_watt_lowercase_normalized():
    _, va = extract_variants("LED 방등 50w 60w", "lighting", {})
    assert va["watt"] == "50W/60W"


def test_title_geo_lamp_is_type():
    # "램프"는 유형 — l2("전구") 중복 보강 금지. 목표: 브랜드+canonical 그대로.
    from furniture_catalog import title_geo
    assert title_geo("테스트브랜드", "LED 컴팩트 램프", "전구") == "테스트브랜드 LED 컴팩트 램프"
