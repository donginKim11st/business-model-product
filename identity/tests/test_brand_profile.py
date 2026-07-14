import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import brand_profile as bp


def test_load_crawl_profile_explicit():
    prof = bp.load_crawl_profile("dongsuh")
    assert prof["delay_s"] == 1.2
    assert prof["resumable"] is True
    assert prof["category_codes"] == ["019"]


def test_load_crawl_profile_platform_default_when_missing():
    # crawl_profile 없는 브랜드는 platform 기본값 폴백 — 크롤 안 멈춤
    prof = bp.load_crawl_profile("dotoro")  # cafe24, 현재 crawl_profile 없음
    assert prof["platform"] == "cafe24"
    assert "delay_s" in prof  # 기본값 존재


def test_load_crawl_profile_unknown_slug():
    with pytest.raises(KeyError):
        bp.load_crawl_profile("nonexistent_brand")


def test_delay_floor_enforced():
    # dongsuh delay가 하한(1.2) 미만으로 등록돼 있으면 하한으로 끌어올림
    assert bp.DELAY_FLOORS["dongsuh"] == 1.2


def test_registry_integrity():
    reg = bp._load_registry()
    slugs = {b["slug"] for b in reg["brands"]}
    assert {"jakomo", "dongsuh", "flora", "mothershome"} <= slugs
    for b in reg["brands"]:
        prof = bp.load_crawl_profile(b["slug"])
        assert prof["delay_s"] > 0
        assert prof["platform"] in {"godomall", "cafe24", "makeshop", "imweb"}


def test_dongsuh_and_godomall_profiles():
    d = bp.load_crawl_profile("dongsuh")
    assert d["delay_s"] == 1.2 and d["resumable"] is True and d["gosi_in_image"] is True
    j = bp.load_crawl_profile("jakomo")  # godomall
    assert j["resumable"] is True


SAMPLE_ROWS = [
    {"source": "dongsuh", "name": "침대 A", "category": "침대", "material": "MDF",
     "width_cm": "120", "bed_size": "", "price": "100000"},
    {"source": "dongsuh", "name": "침대 B", "category": "침대", "material": "원목",
     "width_cm": "140", "bed_size": "", "price": "200000"},
    {"source": "dongsuh", "name": "소파 C", "category": "소파", "material": "MDF",
     "width_cm": "", "bed_size": "", "price": "300000"},
]


def test_compute_schema_coverage():
    sch = bp.compute_schema(SAMPLE_ROWS)
    assert sch["fields"]["material"]["coverage"] == 1.0
    assert sch["fields"]["width_cm"]["coverage"] == pytest.approx(2 / 3)
    assert sch["fields"]["bed_size"]["coverage"] == 0.0
    assert "MDF" in sch["fields"]["material"]["top"]


def test_compute_domain_top_categories():
    dom = bp.compute_domain(SAMPLE_ROWS, note="cateCd 019=BEST", gosi_in_image=True)
    assert dom["top_categories"][0] == ["침대", 2]
    assert dom["notes_freeform"] == "cateCd 019=BEST"
    assert dom["gosi_in_image"] is True


def test_compute_stats_delta_and_regression():
    prev = {"stats": {"count": 5}, "schema": {}}
    st = bp.compute_stats(SAMPLE_ROWS, prev=prev, run_log=None)
    assert st["count"] == 3
    assert st["dropped"] == 2  # 5 → 3
    assert st["regression"] is True  # 건수 급감


def test_compute_stats_no_prev():
    st = bp.compute_stats(SAMPLE_ROWS, prev=None, run_log=None)
    assert st["count"] == 3 and st["new"] == 3 and st["regression"] is False
