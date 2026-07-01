import catalog_lexicon as lex

def test_all_30_brand_slugs_present():
    expected = {
        "adidas", "kolping", "natgeo", "nepa", "nike", "montbell", "arena",
        "skechers", "northface", "eider", "proworldcup", "mizuno", "k2",
        "millet", "nb", "underarmour", "blackyak", "outdoorproducts", "vans",
        "worldcup", "prospecs", "starsports", "columbia", "crocs", "redface",
        "puma", "westwood", "jansport", "fila", "lecaf",
    }
    assert expected <= set(lex.BRAND_KO)
    assert lex.BRAND_KO["nike"] == "나이키"
    assert lex.BRAND_KO["fila"] == "휠라"  # brand 컬럼엔 KIDS 오염값

def test_gender_map_core():
    assert lex.GENDER_MAP["남성"] == "M"
    assert lex.GENDER_MAP["women"] == "W"
    assert lex.GENDER_MAP["공용"] == "U"
    assert lex.GENDER_MAP["키즈"] == "K"
    assert "outlet" not in lex.GENDER_MAP  # 성별 아닌 값은 매핑 안 함

def test_product_types_longest_first():
    # 부분매칭 오검출 방지: '축구화'가 '신발'보다 앞
    assert lex.PRODUCT_TYPES.index("축구화") < lex.PRODUCT_TYPES.index("신발")
    assert lex.PRODUCT_TYPES.index("다운재킷") < lex.PRODUCT_TYPES.index("재킷")

def test_stylecode_suffix_rules():
    assert lex.STYLECODE_SUFFIX["nike"] == {"sep": "-"}
    assert lex.STYLECODE_SUFFIX["arena"] == {"tail_alpha": 3}
    assert lex.STYLECODE_SUFFIX["columbia"] == {"tail_digit": 3}
    assert "adidas" not in lex.STYLECODE_SUFFIX  # 폴백 대상
