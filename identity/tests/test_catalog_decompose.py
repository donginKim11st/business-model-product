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
    assert d["gender_code"] == "K"
    assert d["gender"] == "키즈"
    assert d["product_type"] == "축구화"
    assert "축구화" not in d["product_name"]
    assert "키즈" not in d["product_name"]
    assert d["catalog_name"].startswith("아디다스 F50 하이퍼패스트")
    assert "키즈" in d["catalog_name"] and "축구화" in d["catalog_name"]
    assert d["color"] == "Pink"

def test_leading_gender_blackyak():
    r = _row(source="blackyak", name="남성 아이스프레쉬 라운드 베이스레이어",
             gender="남성", category="상의", color="BLACK,NAVY,WHITE")
    d = cd.decompose_row(r)
    assert d["gender"] == "남성"
    assert d["product_type"] == "베이스레이어"
    assert d["product_name"] == "아이스프레쉬 라운드"
    assert d["catalog_name"].startswith("블랙야크 아이스프레쉬 라운드")
    assert "남성" in d["catalog_name"] and "베이스레이어" in d["catalog_name"]
    assert d["color"] == "BLACK,NAVY,WHITE"

def test_name_only_eider():
    r = _row(source="eider", name="ST 슬라이드 2", gender="공용",
             category="신발", color="Red")
    d = cd.decompose_row(r)
    assert d["gender"] == "공용"
    assert d["product_name"] == "ST 슬라이드 2"
    assert d["product_type"] == "신발"
    assert d["catalog_name"].startswith("아이더 ST 슬라이드 2")
    assert "공용" in d["catalog_name"] and "신발" in d["catalog_name"]

def test_trailing_color_jansport():
    r = _row(source="jansport", name="슈퍼브레이크 BLACK", gender="",
             category="백팩", color="BLACK")
    d = cd.decompose_row(r)
    assert d["product_type"] == "백팩"
    assert d["product_name"] == "슈퍼브레이크"
    assert "BLACK" not in d["product_name"].upper()
    assert d["catalog_name"].startswith("잔스포츠 슈퍼브레이크")
    assert "백팩" in d["catalog_name"]

def test_paren_gender_kolping():
    r = _row(source="kolping", name="국민바지2.5 210(남)", gender="MALE",
             category="여름 바지", color="BLACK|KHAKI|NAVY")
    d = cd.decompose_row(r)
    assert d["gender"] == "남성"
    assert "(남)" not in d["product_name"]
    assert d["catalog_name"].startswith("콜핑 국민바지2.5")
    assert "남성" in d["catalog_name"]

def test_bilingual_dup_flags_needs_llm():
    r = _row(source="puma", name="푸마 아반티 LS Puma Avanti LS", gender="남성",
             category="신발", color="PUMA Black-PUMA White")
    d = cd.decompose_row(r)
    # 브랜드/성별 제거 후에도 한글+트레일링 다단어 영문 잔존 → LLM 게이트 대상
    assert d["needs_llm"] == "1"

def test_out_cols_stable():
    assert cd.OUT_COLS[:5] == ["source", "brand_norm", "style_code", "catalog_name", "product_name"]
    assert "gender" in cd.OUT_COLS and "product_type" in cd.OUT_COLS
    assert "color" in cd.OUT_COLS and "size" in cd.OUT_COLS
    assert "needs_llm" in cd.OUT_COLS

def test_ascii_token_not_substring_clobbered():
    # 'men' in Cement, 'blue' in Blueprint must NOT be stripped/gendered
    r = _row(source="nike", name="Blueprint Cement Runner", gender="",
             category="신발", color="")
    d = cd.decompose_row(r)
    assert "Blueprint" in d["product_name"]
    assert "Cement" in d["product_name"]
    assert d["gender_code"] != "M"

def test_model_version_paren_preserved():
    r = _row(source="nike", name="에어 줌 페가수스 (40)", gender="남성",
             category="신발", color="")
    d = cd.decompose_row(r)
    assert "40" in d["product_name"]      # 모델 버전 괄호 보존
    assert "40" in d["catalog_name"]
    assert d["gender_code"] == "M"


import csv as _csv
import os as _os

def test_run_stage1_writes_output(tmp_path):
    src = tmp_path / "in.csv"
    with open(src, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["source", "brand", "style_code", "name",
            "color", "price", "currency", "category", "gender", "sizes",
            "origin", "material", "mfg_date", "url"])
        w.writeheader()
        w.writerow({"source": "eider", "style_code": "DUS26N77R2",
                    "name": "ST 슬라이드 2", "color": "Red", "gender": "공용",
                    "category": "신발", "price": "39000", "sizes": "250|260",
                    "url": "http://x"})
    out = tmp_path / "out.csv"
    summary = cd.run_stage1(str(src), str(out), limit=0)
    assert summary["rows"] == 1
    rows = list(_csv.DictReader(open(out, encoding="utf-8-sig")))
    assert rows[0]["catalog_name"] == "아이더 ST 슬라이드 2 공용 신발 Red"
    assert list(rows[0].keys()) == cd.OUT_COLS
