"""패턴 마이닝(2026-07-03)이 실측한 옵션 소비층 결함 4종 수정.

① _OPT_ADDON_RE의 '+N원|★'가 정당 변형(가격병기·프로모마커)을 통드롭
   — flora 옵션값 98.6%가 '( 사양 / +N,NNN원 )' 표기, ★특별가 11 PDP
② 가격델타 미분리 → 변형 가격 부정확 (~3.9만 옵션값)
③ 은행 결제 select가 옵션군으로 유입 (wooree 231상품, 값 4,620)
④ 모음전 합성행이 옵션군 전체를 폐기 → 순수 변형축 군(색상/와트)까지 소실
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from furniture_catalog import (_clean_opt, _group_combos, _pure_axis_groups,
                               _split_delta)


# ── ② 가격델타 분리 ──────────────────────────────────────────────────────────

def test_split_delta_colon():
    assert _split_delta("양면본넬 Q : +23,000원") == ("양면본넬 Q", 23000)


def test_split_delta_paren_slash():
    # flora 주류형: '값 ( 사양 / +N,NNN원 )' — 사양은 보존
    assert _split_delta("본품+설치포함 (+50,000원)") == ("본품+설치포함", 50000)
    t, d = _split_delta("Q 퀸 방수커버 ( 화이트 / +14,900원 )")
    assert d == 14900 and "화이트" in t and "원" not in t


def test_split_delta_no_won_and_negative():
    # vittz/prielle '원' 생략형, 음수(할인)
    assert _split_delta("잔광제거 콘덴서 2개(+4,000)") == ("잔광제거 콘덴서 2개", 4000)
    assert _split_delta("양면본넬 SS (-24,900원)")[1] == -24900


def test_split_delta_none():
    assert _split_delta("화이트") == ("화이트", None)
    # 상품명 속 일반 숫자는 델타 아님
    assert _split_delta("2인용 소파 1200")[1] is None


# ── ① ★/가격병기 통드롭 해제 ────────────────────────────────────────────────

def test_star_value_survives():
    # ★는 프로모 마커 — 값 자체는 정당 변형. 마커만 벗긴다(언더스코어는 _clean_val 소관).
    assert _clean_opt("★특별가_양면본넬 Q") == "특별가_양면본넬 Q"


def test_priced_variant_survives_in_groups():
    g = json.dumps([{"label": "사이즈", "values": ["S ( 화이트 / +0원 )", "Q ( 화이트 / +14,900원 )"]}])
    combos = _group_combos(g)
    assert len(combos) == 2
    deltas = sorted(c.get("_delta", 0) for c in combos)
    assert deltas == [0, 14900]


def test_addon_group_still_excluded():
    # 추가상품 군(라벨 신호)은 여전히 통째 제외
    g = json.dumps([{"label": "추가구매", "values": ["베개솜 (+5,000원)", "방수커버 (+14,900원)"]},
                    {"label": "색상", "values": ["화이트", "그레이"]}])
    combos = _group_combos(g)
    assert len(combos) == 2
    assert all(c.get("color") in ("화이트", "그레이") for c in combos)


# ── ③ 은행/결제 군 드롭 ─────────────────────────────────────────────────────

def test_bank_group_dropped():
    g = json.dumps([{"label": "", "values": ["인터넷뱅킹 바로가기", "국민은행", "NH 농협", "씨티은행"]},
                    {"label": "색상", "values": ["화이트", "블랙"]}])
    combos = _group_combos(g)
    assert len(combos) == 2
    assert all("은행" not in json.dumps(c, ensure_ascii=False) for c in combos)


# ── ④ 모음전 합성행 — 순수 변형축 군 승계 ────────────────────────────────────

def test_pure_axis_groups_filter():
    g = json.dumps([
        {"label": "종류", "values": ["A. 방등", "B. 주방등"]},                       # 부모 선택자(usage) — 제외
        {"label": "제품선택", "values": ["A1. 원형방등(일반)", "B1. 주방등(행복)"]},  # 모델열거 — 제외
        {"label": "W/색상선택", "values": ["50W(주광색)", "60W(주광색)"]},           # 순수 SKU축 — 승계
        {"label": "_cascade", "values": ["1"]},
    ])
    kept = json.loads(_pure_axis_groups(g))
    assert [k["label"] for k in kept] == ["W/색상선택"]


def test_pure_axis_groups_empty_when_none():
    g = json.dumps([{"label": "종류", "values": ["A. 방등", "B. 주방등"]}])
    assert _pure_axis_groups(g) == ""


# ── 모음전 판정: 가격델타 보유 값 제외 ───────────────────────────────────────
# 델타(+N원)는 "기준가 대비 구성/사양 차액" = 한 상품의 변형 신호. 모음전(서로 다른
# 상품 열거)의 증거로 쓰면 flora 구성옵션이 카탈로그로 과분해(실측 +5.2K 키).

def test_model_options_ignores_priced_values():
    from furniture_catalog import model_options
    priced = "|".join(["S/SS 단품 ( +0원 )", "Q/K 단품 ( +10,000원 )",
                       "S/SS 차렵패드세트 ( +45,000원 )", "Q/K 차렵매트커버세트 ( +60,000원 )"])
    assert model_options(priced) == []          # 전부 델타 보유 → 모음전 아님(변형 경로가 처리)
    plain = "|".join(["A1. 원형방등(일반)", "B1. 주방등(행복/시스템)", "C1. 거실등(행복/시스템)"])
    assert model_options(plain)                 # 무델타 유형 열거 — 모음전 유지


# ── 색온도 동의어·켈빈 토큰 (하얀불 238·노란불 253 실측 — 모음전 오판의 원인) ──

def test_cct_synonyms_and_kelvin():
    from furniture_catalog import _promote_option, _is_variantish
    assert _promote_option("레겐 램프 노란불")["cct"] == "전구색"
    assert _promote_option("A60 하얀불")["cct"] == "주광색"
    assert _promote_option("간접등 6500k")["cct"] == "6500K"
    # 변형성 판정에도 반영 — 색온도 옵션이 모델열거로 오판되지 않게
    assert _is_variantish("노란불")
    assert _is_variantish("6500K")
