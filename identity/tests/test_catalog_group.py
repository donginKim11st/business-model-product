import catalog_group as cg

def _d(**kw):
    base = {"source": "", "brand_norm": "", "style_code": "", "catalog_name": "",
            "product_line": "", "product_type": "", "gender_norm": "",
            "colorway": "", "price": "", "sizes": "", "url": ""}
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
        _d(source="nike", style_code="IM5752-300", catalog_name="나이키 에어 포스 1",
           product_line="에어 포스 1", colorway="퍼", price="159000", sizes="240|250"),
        _d(source="nike", style_code="IM5752-100", catalog_name="나이키 에어 포스 1",
           product_line="에어 포스 1", colorway="화이트", price="159000", sizes="250|260"),
    ]
    cats = cg.group(rows)
    assert len(cats) == 1
    c = cats[0]
    assert c["catalog_name"] == "나이키 에어 포스 1"
    assert c["n_variants"] == "2"
    assert c["n_colorways"] == "2"
    assert c["style_codes"] == "2"
    assert c["size_range"] == "240~260"

def test_group_by_name_fallback_adidas():
    rows = [
        _d(source="adidas", style_code="KK1334", catalog_name="아디다스 삼바",
           product_line="삼바", product_type="신발", gender_norm="U", colorway="Pink"),
        _d(source="adidas", style_code="HQ2274", catalog_name="아디다스 삼바",
           product_line="삼바", product_type="신발", gender_norm="U", colorway="Black"),
    ]
    cats = cg.group(rows)
    assert len(cats) == 1  # style_code 규칙 없어도 이름으로 묶임
    assert cats[0]["n_colorways"] == "2"

def test_group_cols_stable():
    assert cg.GROUP_COLS[:4] == ["source", "brand_norm", "model_key", "catalog_name"]


import csv as _csv

def test_run_stage2_writes_output(tmp_path):
    src = tmp_path / "dec.csv"
    import catalog_decompose as cd
    with open(src, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=cd.OUT_COLS)
        w.writeheader()
        w.writerow({"source": "nike", "brand_norm": "나이키", "style_code": "IM5752-300",
                    "catalog_name": "나이키 에어 포스 1", "product_line": "에어 포스 1",
                    "product_type": "신발", "gender_norm": "M", "colorway": "퍼",
                    "price": "159000", "sizes": "240|250", "url": "http://x",
                    "name": "에어 포스 1", "needs_llm": "0"})
        w.writerow({"source": "nike", "brand_norm": "나이키", "style_code": "IM5752-100",
                    "catalog_name": "나이키 에어 포스 1", "product_line": "에어 포스 1",
                    "product_type": "신발", "gender_norm": "M", "colorway": "화이트",
                    "price": "159000", "sizes": "250|260", "url": "http://y",
                    "name": "에어 포스 1", "needs_llm": "0"})
    out = tmp_path / "cat.csv"
    summary = cg.run_stage2(str(src), str(out))
    assert summary["catalogs"] == 1
    rows = list(_csv.DictReader(open(out, encoding="utf-8-sig")))
    assert rows[0]["n_variants"] == "2"
    assert list(rows[0].keys()) == cg.GROUP_COLS
