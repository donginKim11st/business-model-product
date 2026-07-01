import json
import catalog_llm_gate as gate

def test_apply_stage1_uses_cache_no_network():
    rows = [
        {"needs_llm": "1", "name": "푸마 아반티 LS Puma Avanti LS", "brand_norm": "푸마",
         "product_name": "아반티 LS Puma Avanti LS", "product_type": "신발",
         "gender": "남성", "gender_code": "M", "color": "",
         "catalog_name": "푸마 아반티 LS Puma Avanti LS 남성 신발"},
        {"needs_llm": "0", "name": "아이더 ST 슬라이드 2", "brand_norm": "아이더",
         "product_name": "ST 슬라이드 2", "product_type": "신발",
         "gender": "공용", "gender_code": "U", "color": "",
         "catalog_name": "아이더 ST 슬라이드 2 공용 신발"},
    ]
    # 캐시 선주입 → 네트워크 호출 없이 처리
    cache = {gate._key1(rows[0]): {"product_name": "아반티 LS", "product_type": "신발", "gender": "M"}}
    n = gate.apply_stage1(rows, limit=0, api_key="TEST", cache=cache)
    assert n == 1
    assert rows[0]["product_name"] == "아반티 LS"
    assert rows[0]["catalog_name"] == "푸마 아반티 LS 남성 신발"
    assert rows[0]["needs_llm"] == "0"  # 보정됨
    # needs_llm=0 행은 건드리지 않음
    assert rows[1]["product_name"] == "ST 슬라이드 2"

def test_apply_stage1_limit_zero_when_no_candidates():
    rows = [{"needs_llm": "0", "name": "x", "brand_norm": "b", "product_name": "x",
             "product_type": "", "gender": "", "gender_code": "", "color": "",
             "catalog_name": "b x"}]
    n = gate.apply_stage1(rows, limit=0, api_key="TEST", cache={})
    assert n == 0

def test_limit_caps_and_reports(capsys):
    rows = [{"needs_llm": "1", "name": "a", "brand_norm": "b", "product_name": "a",
             "product_type": "", "gender": "", "gender_code": "", "color": "",
             "catalog_name": "b a"},
            {"needs_llm": "1", "name": "c", "brand_norm": "b", "product_name": "c",
             "product_type": "", "gender": "", "gender_code": "", "color": "",
             "catalog_name": "b c"}]
    # 캐시에 1건만 → limit=1 이면 1건 처리, 1건은 미처리(로그 보고)
    cache = {gate._key1(rows[0]): {"product_name": "a2", "product_type": "", "gender": ""}}
    n = gate.apply_stage1(rows, limit=1, api_key="TEST", cache=cache)
    assert n == 1
    out = capsys.readouterr().out
    assert "미보정" in out  # 무음 절단 금지
