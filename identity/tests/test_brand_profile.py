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


def test_compute_stats_regression_exact_20pct():
    prev = {"stats": {"count": 10}, "schema": {}}
    rows = [{"source": "x", "name": f"n{i}"} for i in range(8)]  # 10 -> 8 = exactly 20% drop
    st = bp.compute_stats(rows, prev=prev, run_log=None)
    assert st["count"] == 8
    assert st["regression"] is True


def test_compute_stats_no_regression_below_threshold():
    prev = {"stats": {"count": 10}, "schema": {}}
    rows = [{"source": "x", "name": f"n{i}"} for i in range(9)]  # 10 -> 9 = 10% drop, below 20%
    st = bp.compute_stats(rows, prev=prev, run_log=None)
    assert st["regression"] is False


import csv as _csv


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=bp.HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in bp.HEADER})


def test_build_and_upsert_with_mongomock(tmp_path, monkeypatch):
    import mongomock
    client = mongomock.MongoClient()
    monkeypatch.setattr(bp, "_get_db", lambda: client["insights_demo"])

    csv_path = tmp_path / "extract_furniture_dongsuh.csv"
    _write_csv(csv_path, SAMPLE_ROWS)

    doc = bp.build_and_upsert("dongsuh", str(csv_path), run_log={"failed_urls": 1})
    assert doc["_id"] == "dongsuh"
    assert doc["stats"]["count"] == 3
    assert doc["stats"]["failed_urls"] == 1
    assert doc["schema"]["fields"]["material"]["coverage"] == 1.0
    assert doc["crawl_profile"]["delay_s"] == 1.2

    got = bp.get_profile("dongsuh")
    assert got["stats"]["count"] == 3


def test_history_ring_buffer(tmp_path, monkeypatch):
    import mongomock
    client = mongomock.MongoClient()
    monkeypatch.setattr(bp, "_get_db", lambda: client["insights_demo"])
    csv_path = tmp_path / "extract_furniture_flora.csv"
    _write_csv(csv_path, SAMPLE_ROWS)
    for i in range(bp.HISTORY_MAX + 5):
        bp.build_and_upsert("flora", str(csv_path), run_log={"harvest_id": f"h{i}"})
    doc = bp.get_profile("flora")
    assert len(doc["history"]) == bp.HISTORY_MAX  # 링버퍼 상한


def test_build_and_upsert_mongo_down_file_fallback(tmp_path, monkeypatch):
    def _boom():
        raise RuntimeError("mongo down")
    monkeypatch.setattr(bp, "_get_db", _boom)
    monkeypatch.setattr(bp, "OUT_DIR", str(tmp_path))
    csv_path = tmp_path / "extract_furniture_vittz.csv"
    _write_csv(csv_path, SAMPLE_ROWS)
    doc = bp.build_and_upsert("vittz", str(csv_path), run_log=None)
    assert doc["stats"]["count"] == 3
    assert os.path.exists(os.path.join(str(tmp_path), "profiles", "vittz.json"))


def test_profile_all_skips_missing_csv(tmp_path, monkeypatch):
    import mongomock
    client = mongomock.MongoClient()
    monkeypatch.setattr(bp, "_get_db", lambda: client["insights_demo"])
    monkeypatch.setattr(bp, "OUT_DIR", str(tmp_path))
    # dongsuh CSV만 존재
    csv_path = os.path.join(str(tmp_path), "extract_furniture_dongsuh.csv")
    _write_csv(csv_path, SAMPLE_ROWS)
    done = bp.profile_all(only={"dongsuh", "flora"})  # flora CSV 없음 → 스킵
    assert done == ["dongsuh"]
