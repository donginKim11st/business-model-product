import furniture_catalog as fc


def test_clean_title_strips_enum_stock_mojibake():
    assert "1-1." not in fc._clean_title("코이 매트리스 1-1. 스테이 코이 13 SS")
    assert fc._clean_title("코이 매트리스 1-1. 스테이 코이 13 SS").startswith("코이 매트리스 스테이")
    assert "품절" not in fc._clean_title("라움 침대 SS 오크 품절")
    assert "�" not in fc._clean_title("비츠온 �������� LED 조명")
    assert fc._clean_title("국민바지2.5 210") == "국민바지2.5 210"   # 소수점 보존
