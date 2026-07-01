import catalog_decompose as cd

def _row(**kw):
    base = {"source": "", "brand": "", "style_code": "", "name": "", "color": "",
            "price": "", "currency": "KRW", "category": "", "gender": "",
            "sizes": "", "origin": "", "material": "", "mfg_date": "", "url": ""}
    base.update(kw)
    return base

def test_trailing_type_and_gender_adidas():
    r = _row(source="adidas", name="F50 하이퍼패스트 클럽 벨크로 아스트로 터프 축구화 키즈",
             gender="KIDS", category="신발", color="Pink")
    d = cd.decompose_row(r)
    assert d["brand_norm"] == "아디다스"
    assert d["gender_norm"] == "K"
    assert d["product_type"] == "축구화"
    assert "키즈" not in d["product_line"]
    assert d["catalog_name"].startswith("아디다스 F50 하이퍼패스트")
    assert d["catalog_name"].endswith("축구화")

def test_leading_gender_blackyak():
    r = _row(source="blackyak", name="남성 아이스프레쉬 라운드 베이스레이어",
             gender="남성", category="상의", color="BLACK,NAVY,WHITE")
    d = cd.decompose_row(r)
    assert d["gender_norm"] == "M"
    assert d["product_type"] == "베이스레이어"
    assert not d["product_line"].startswith("남성")
    assert d["catalog_name"] == "블랙야크 아이스프레쉬 라운드 베이스레이어"

def test_name_only_eider():
    r = _row(source="eider", name="ST 슬라이드 2", gender="공용",
             category="신발", color="Red")
    d = cd.decompose_row(r)
    assert d["gender_norm"] == "U"
    assert d["product_line"] == "ST 슬라이드 2"
    assert d["catalog_name"] == "아이더 ST 슬라이드 2"

def test_trailing_color_jansport():
    r = _row(source="jansport", name="슈퍼브레이크 BLACK", gender="",
             category="백팩", color="BLACK")
    d = cd.decompose_row(r)
    assert d["product_type"] == "백팩"
    assert "BLACK" not in d["product_line"].upper()
    assert d["catalog_name"] == "잔스포츠 슈퍼브레이크"

def test_paren_gender_kolping():
    r = _row(source="kolping", name="국민바지2.5 210(남)", gender="MALE",
             category="여름 바지", color="BLACK|KHAKI|NAVY")
    d = cd.decompose_row(r)
    assert d["gender_norm"] == "M"
    assert "(남)" not in d["product_line"]
    assert d["catalog_name"].startswith("콜핑 국민바지2.5")

def test_bilingual_dup_flags_needs_llm():
    r = _row(source="puma", name="푸마 아반티 LS Puma Avanti LS", gender="남성",
             category="신발", color="PUMA Black-PUMA White")
    d = cd.decompose_row(r)
    # 브랜드/성별 제거 후에도 한글+트레일링 다단어 영문 잔존 → LLM 게이트 대상
    assert d["needs_llm"] == "1"

def test_out_cols_stable():
    assert cd.OUT_COLS[:4] == ["source", "brand_norm", "style_code", "catalog_name"]
    assert "needs_llm" in cd.OUT_COLS

def test_ascii_token_not_substring_clobbered():
    # 'men' in Cement, 'blue' in Blueprint must NOT be stripped/gendered
    r = _row(source="nike", name="Blueprint Cement Runner", gender="",
             category="신발", color="")
    d = cd.decompose_row(r)
    assert "Blueprint" in d["product_line"]
    assert "Cement" in d["product_line"]
    assert d["gender_norm"] != "M"  # 'Cement' contains 'men' but is not gender

def test_model_version_paren_preserved():
    r = _row(source="nike", name="에어 줌 페가수스 (40)", gender="남성",
             category="신발", color="")
    d = cd.decompose_row(r)
    assert "40" in d["product_line"]      # 모델 버전 괄호 보존
    assert "40" in d["catalog_name"]
    assert d["gender_norm"] == "M"
