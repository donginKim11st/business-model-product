import furniture_catalog as fc


def test_clean_title_strips_enum_stock_mojibake():
    assert "1-1." not in fc._clean_title("코이 매트리스 1-1. 스테이 코이 13 SS")
    assert fc._clean_title("코이 매트리스 1-1. 스테이 코이 13 SS").startswith("코이 매트리스 스테이")
    assert "품절" not in fc._clean_title("라움 침대 SS 오크 품절")
    assert "�" not in fc._clean_title("비츠온 �������� LED 조명")
    assert fc._clean_title("국민바지2.5 210") == "국민바지2.5 210"   # 소수점 보존


def test_parse_opt_composite():
    po = fc._promote_option("슬림형/아이보리/슈퍼싱글+슈퍼싱글")
    # 슬림형은 form 축으로 승격(2026-07-03), 잔여 없음
    assert (po["form"], po["color"], po["size"], po["option"]) == ("슬림형", "아이보리", "SS+SS", "")
    assert fc._parse_opt_composite("단품")[0] == "단품"          # 비복합·비축 값은 그대로
    assert fc._parse_opt_composite("화이트/Q") == ("", "화이트", "Q")


def test_marketing_tokens_stripped():
    assert "국내제작" not in fc._clean_title("라온 패밀리침대 국내제작 슬림형")


def test_paren_form_promoted_to_axis():
    n, info = fc.extract_parens("NR 천연 라텍스 베개(대형)", "bedding")
    assert info["form"] == "대형" and "대형" not in n
    n2, info2 = fc.extract_parens("NR 천연 라텍스 베개(땅콩형)", "bedding")
    assert info2["form"] == "땅콩형"


def test_promote_option_axes():
    po = fc._promote_option("라이트그레이")
    assert po["color"] == "라이트그레이" and po["option"] == ""
    po = fc._promote_option("약간하드")
    assert po["firm"] == "약간하드" and po["option"] == ""
    po = fc._promote_option("60W/주광색")
    assert po["watt"] == "60W" and po["cct"] == "주광색" and po["option"] == ""
    po = fc._promote_option("매트리스 방수커버 SS 화이트 102645")
    assert "102645" not in po["option"]   # 내부코드 제거
