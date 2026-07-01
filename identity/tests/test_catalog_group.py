import catalog_group as cg

def _d(**kw):
    base = {"source": "", "brand_norm": "", "style_code": "", "catalog_name": "",
            "product_name": "", "gender": "", "product_type": "", "color": "",
            "size": "", "material": "", "origin": "", "gender_code": "",
            "price": "", "url": ""}
    base.update(kw)
    return base

def test_base_style_code_rules():
    assert cg.base_style_code("nike", "IM5752-300") == "IM5752"
    assert cg.base_style_code("puma", "409960_01") == "409960"
    assert cg.base_style_code("arena", "A6BL1LO15WHT") == "A6BL1LO15"
    assert cg.base_style_code("k2", "KUF26C53HB") == "KUF26C53"
    assert cg.base_style_code("columbia", "C72YM3621346") == "C72YM3621"
    assert cg.base_style_code("adidas", "KK1334") is None  # 규칙 없음 → 폴백

def test_group_by_stylecode_base_merges_colorways():
    rows = [
        _d(source="nike", style_code="IM5752-300", brand_norm="나이키",
           product_name="에어 포스 1", gender="남성", gender_code="M",
           product_type="신발", color="퍼", price="159000", size="240|250", url="u1"),
        _d(source="nike", style_code="IM5752-100", brand_norm="나이키",
           product_name="에어 포스 1", gender="남성", gender_code="M",
           product_type="신발", color="화이트", price="159000", size="250|260", url="u2"),
    ]
    cats = cg.group(rows)
    assert len(cats) == 1
    c = cats[0]
    assert c["title_commerce"] == "나이키 에어 포스 1 남성 신발"
    assert c["title_geo"] == "나이키 에어 포스 1 신발"   # 브랜드+canonical+유형(색상·사이즈·성별 제외)
    assert c["product_name"] == "에어 포스 1"
    assert c["n_variants"] == "2"
    assert c["n_colors"] == "2"
    assert c["style_codes"] == "2"
    assert c["size_range"] == "240~260"

def test_group_by_name_fallback_adidas():
    rows = [
        _d(source="adidas", style_code="KK1334", brand_norm="아디다스",
           product_name="삼바", product_type="신발", gender="공용", gender_code="U", color="Pink"),
        _d(source="adidas", style_code="HQ2274", brand_norm="아디다스",
           product_name="삼바", product_type="신발", gender="공용", gender_code="U", color="Black"),
    ]
    cats = cg.group(rows)
    assert len(cats) == 1
    assert cats[0]["n_colors"] == "2"
    assert cats[0]["title_commerce"] == "아디다스 삼바 신발"   # '공용' 제외

def test_group_cols_stable():
    assert cg.GROUP_COLS[:6] == ["source", "brand_norm", "model_key", "title_geo", "title_commerce", "product_name"]
    assert "colors" in cg.GROUP_COLS and "size_range" in cg.GROUP_COLS


import csv as _csv

def test_run_stage2_writes_output(tmp_path):
    src = tmp_path / "dec.csv"
    import catalog_decompose as cd
    def _row(**kw):
        base = {c: "" for c in cd.OUT_COLS}
        base.update(kw)
        return base
    with open(src, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=cd.OUT_COLS)
        w.writeheader()
        w.writerow(_row(source="nike", brand_norm="나이키", style_code="IM5752-300",
            product_name="에어 포스 1",
            gender="남성", product_type="신발", color="퍼", size="240|250",
            gender_code="M", price="159000", url="u1", name="에어 포스 1", needs_llm="0"))
        w.writerow(_row(source="nike", brand_norm="나이키", style_code="IM5752-100",
            product_name="에어 포스 1",
            gender="남성", product_type="신발", color="화이트", size="250|260",
            gender_code="M", price="159000", url="u2", name="에어 포스 1", needs_llm="0"))
    out = tmp_path / "cat.csv"
    summary = cg.run_stage2(str(src), str(out))
    assert summary["catalogs"] == 1
    rows = list(_csv.DictReader(open(out, encoding="utf-8-sig")))
    assert rows[0]["n_variants"] == "2"
    assert list(rows[0].keys()) == cg.GROUP_COLS
