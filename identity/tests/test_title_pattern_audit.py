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


def test_l2_tail_requires_name_evidence():
    # LLM 심사(980표본) 문제 24건 전원인: 몰카테고리 근거 오분류 → 이름에 없는 유형꼬리
    # 부착. 유형보강은 원본명에 해당 l2 토큰이 실제로 있을 때만.
    # (브랜드는 가짜 — 실브랜드는 LLM canonical 스토어 캐시에 걸려 테스트가 비결정적)
    assert title_geo("테스트상표", "도아르 아일랜드식탁", "요/토퍼",
                     name="도아르 인조대리석 아일랜드식탁 홈바 조리대") == "테스트상표 도아르 아일랜드식탁"
    assert title_geo("테스트상표", "디오프 복합식 가습기 6L", "이불",
                     name="디오프 복합식 타워 가습기 6L") == "테스트상표 디오프 복합식 가습기 6L"


def test_l2_tail_appends_evidenced_segment():
    # 슬래시 l2는 통짜가 아니라 이름에 있는 세그먼트만
    # (canonical에 유형어 substring 없어야 함 — '트리플'은 '트리'에 걸림)
    t = title_geo("테스트상표", "베른 시리즈", "커튼/블라인드", name="베른 시리즈 블라인드 창")
    assert t == "테스트상표 베른 시리즈 블라인드"


def test_l2_tail_legacy_without_name():
    # name 미전달(구 호출부) — 기존 동작 유지
    assert title_geo("테스트상표", "베른 시리즈", "블라인드") == "테스트상표 베른 시리즈 블라인드"


def test_llm_canonical_distrust_on_zero_overlap(monkeypatch):
    # LLM 환각 가드(실측 133건 — 도토로 장식볼→'크리스마스 트리' 105): 원문과 공유
    # 토큰이 전무한 LLM canonical은 불신하고 규칙값 유지. 접점이 있으면 신뢰.
    import furniture_catalog as fc
    monkeypatch.setattr(fc, "_FCANON", {
        "테스트상표|로열골드 글리터 장식볼 세트": "크리스마스 트리",
        "테스트상표|베른 안락 소파": "베른 소파",
    })
    assert fc.title_geo("테스트상표", "로열골드 글리터 장식볼 세트", "") == \
        "테스트상표 로열골드 글리터 장식볼 세트"
    assert fc.title_geo("테스트상표", "베른 안락 소파", "") == "테스트상표 베른 소파"
