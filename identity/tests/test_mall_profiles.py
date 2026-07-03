"""브랜드(몰) 프로파일 계층 + 동서가구 침대 결함 2종 (2026-07-03 사용자 리포트).

실측: 동서 침대 70 카탈로그 — 원본명 사이즈 열거("SS/Q")가 옵션 경로(프레임+매트 구성
팬아웃)로 빠지면 ① 옵션값 융착 사이즈("SS침대 프레임")를 못 뽑고 ② va의 "SS/Q" 통짜가
전 변형에 복사(교차 전개 생략) — 결과: 형태/구성은 갈라져도 사이즈는 안 갈라짐.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from furniture_catalog import (_apply_mall_profile, _explode_axis_multi,
                               _promote_option)


# ── 융착 사이즈코드 분리 ("SS침대 프레임+양면매트") ──────────────────────────

def test_fused_bed_size_extracted():
    po = _promote_option("SS침대 프레임+양면매트")
    assert po["size"] == "SS"
    assert "침대 프레임" in po["option"] and "SS" not in po["option"]


def test_fused_size_storage_bed():
    po = _promote_option("Q수납침대 프레임")
    assert po["size"] == "Q"


def test_s_shape_spring_not_size():
    # "S자형"의 S는 사이즈 아님 — 침대 문맥어 가드
    po = _promote_option("S자형 스프링 매트리스")
    assert po["size"] == ""


# ── 사이즈 열거 교차 전개 (옵션/옵션군 경로) ─────────────────────────────────

def test_explode_bed_size_enum():
    out = _explode_axis_multi({"size": "SS/Q", "color": "그레이"}, "bed")
    assert [d["size"] for d in out] == ["SS", "Q"]
    assert all(d["color"] == "그레이" for d in out)


def test_explode_noop_single_or_combo():
    assert _explode_axis_multi({"size": "Q"}, "bed") == [{"size": "Q"}]
    # "SS+Q"(패밀리 세트)는 한 상품 구성 — 전개 금지
    assert len(_explode_axis_multi({"size": "SS+Q"}, "bed")) == 1
    # 침대/침구 외 카테고리는 소관 아님
    assert len(_explode_axis_multi({"size": "SS/Q"}, "lighting")) == 1


# ── 몰 프로파일 전처리 ───────────────────────────────────────────────────────

def test_dongsuh_bare_enum_prefix():
    r = {"source": {"mall": "dongsuh"}, "name": "소이 일체형 매트리스",
         "raw_options": "2-1 싱글 독립포켓|3-2 슈퍼싱글 케미컬폼20T",
         "raw_option_groups": json.dumps(
             [{"label": "옵션", "values": ["2-1 싱글 독립포켓"]}], ensure_ascii=False)}
    out = _apply_mall_profile(r)
    assert out["raw_options"] == "싱글 독립포켓|슈퍼싱글 케미컬폼20T"
    assert json.loads(out["raw_option_groups"])[0]["values"] == ["싱글 독립포켓"]
    # 4자리 수치("1200 거실장")는 순번 아님 — 보존
    r2 = dict(r, raw_options="1200 거실장")
    assert _apply_mall_profile(r2)["raw_options"] == "1200 거실장"


def test_profile_noop_for_unlisted_mall():
    r = {"source": {"mall": "jakomo"}, "name": "아그네스 소파", "raw_options": "2-1 값"}
    assert _apply_mall_profile(r)["raw_options"] == "2-1 값"
