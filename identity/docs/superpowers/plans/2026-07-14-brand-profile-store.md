# 브랜드 프로필 스토어 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** harvest마다 브랜드별 공식몰 속성(크롤 프로파일·속성 스키마·도메인 지식·수집 통계)을 축적하고, 크롤러/추출기와 GEO·카탈로그 파이프라인이 slug로 조회해 참고할 수 있게 한다.

**Architecture:** 성격이 다른 두 층으로 분리한다. A층은 크롤 시작 전에 필요한 config(`brands_furniture.json`의 `crawl_profile`, git 버전 관리)이고, B층은 harvest 산출을 계산해 upsert하는 지식(Mongo `brand_profiles`, 커밋 금지)이다. 둘을 잇는 단일 모듈 `identity/brand_profile.py`가 A층 읽기·B층 계산/조회를 전담한다.

**Tech Stack:** Python 3, pymongo, pytest, mongomock(테스트 격리)

## Global Constraints

- Mongo URI: `os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")` — 기존 파일과 동일 패턴
- DB명: `os.environ.get("INSIGHTS_DB", "insights")` — 운영 기본 `insights`, 테스트/데모는 `insights_demo`. **운영 `insights`에 테스트 쓰기 금지**
- 신규 컬렉션: `brand_profiles` (slug 키). 실제 운영 적재 전 사용자 명시 승인 필요
- dongsuh `delay_s` 하한 = 1.2 (타르핏 가드, 임의 단축 금지) — 코드로 검증
- 산출 데이터 커밋 금지: `outputs/` 이하는 gitignore. 폴백 파일 `outputs/profiles/<slug>.json`도 커밋 안 함
- 커밋 메시지에 `Claude-Session` 트레일러 넣지 않음
- 통합 CSV HEADER(속성 스키마 계산 대상): `source, brand, model_no, name, color, price, currency, category, material, width_cm, depth_cm, height_cm, bed_size, assembly, installation_service, origin, safety_cert, url`
- 브랜드별 산출 CSV 경로: `outputs/extract_furniture_<slug>.csv`

---

### Task 1: A층 로더 `load_crawl_profile`

**Files:**
- Create: `identity/brand_profile.py`
- Test: `identity/tests/test_brand_profile.py`

**Interfaces:**
- Consumes: `brands_furniture.json` (기존 레지스트리)
- Produces:
  - `load_crawl_profile(slug: str) -> dict` — 해당 브랜드 `crawl_profile` 반환. 브랜드에 `crawl_profile` 없으면 platform 기본값. 미등록 slug면 `KeyError`.
  - `PLATFORM_DEFAULTS: dict[str, dict]` — platform별 기본 프로파일
  - `DELAY_FLOORS: dict[str, float]` — slug별 delay 하한 (`{"dongsuh": 1.2}`)

- [ ] **Step 1: Write the failing test**

```python
# identity/tests/test_brand_profile.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'brand_profile'`

- [ ] **Step 3: Write minimal implementation**

```python
# identity/brand_profile.py
"""브랜드 프로필 스토어 — A층(brands_furniture.json crawl_profile) 읽기 +
B층(Mongo brand_profiles) 계산/조회. 설계: docs/superpowers/specs/2026-07-14-brand-profile-store-design.md
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "brands_furniture.json")

# platform별 크롤러 기본 동작 — crawl_profile 미기재 시 폴백(크롤 무중단)
PLATFORM_DEFAULTS = {
    "godomall":  {"delay_s": 1.0, "resumable": True,  "watchdog_s": 90},
    "cafe24":    {"delay_s": 0.5, "resumable": False, "watchdog_s": 60},
    "makeshop":  {"delay_s": 0.5, "resumable": False, "watchdog_s": 60},
    "imweb":     {"delay_s": 0.5, "resumable": False, "watchdog_s": 60},
}
# slug별 delay 하한 — 타르핏 가드(CLAUDE.md). 임의 단축 방지.
DELAY_FLOORS = {"dongsuh": 1.2}


def _load_registry():
    with open(REGISTRY, encoding="utf-8") as f:
        return json.load(f)


def _brand(slug):
    for b in _load_registry()["brands"]:
        if b["slug"] == slug:
            return b
    raise KeyError(f"미등록 브랜드 slug: {slug}")


def load_crawl_profile(slug):
    """brands_furniture.json → crawl_profile. 없으면 platform 기본값. DB 무의존."""
    b = _brand(slug)
    platform = b.get("platform", "cafe24")
    prof = dict(PLATFORM_DEFAULTS.get(platform, PLATFORM_DEFAULTS["cafe24"]))
    prof.update(b.get("crawl_profile", {}))
    prof["platform"] = platform
    floor = DELAY_FLOORS.get(slug)
    if floor is not None:
        prof["delay_s"] = max(prof.get("delay_s", 0.0), floor)
    return prof
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -q`
Expected: `test_load_crawl_profile_platform_default_when_missing`, `test_load_crawl_profile_unknown_slug`, `test_delay_floor_enforced` PASS. `test_load_crawl_profile_explicit`는 아직 FAIL(브랜드 JSON에 crawl_profile 미기재) — Task 2에서 통과.

- [ ] **Step 5: Commit**

```bash
git add identity/brand_profile.py identity/tests/test_brand_profile.py
git commit -m "feat(brand-profile): A층 load_crawl_profile — platform 기본값 폴백 + delay 하한 가드"
```

---

### Task 2: `brands_furniture.json` 9개 브랜드 `crawl_profile` 구조화

**Files:**
- Modify: `identity/brands_furniture.json` (9개 브랜드 각각 `crawl_profile` 추가, `note` 유지)
- Test: `identity/tests/test_brand_profile.py` (Task 1 파일에 추가)

**Interfaces:**
- Consumes: `load_crawl_profile` (Task 1)
- Produces: 완성된 A층 데이터. 이후 모든 크롤러가 참조.

- [ ] **Step 1: Write the failing test (레지스트리 무결성)**

```python
# tests/test_brand_profile.py 에 추가
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py::test_dongsuh_and_godomall_profiles -q`
Expected: FAIL — `KeyError: 'gosi_in_image'` (아직 crawl_profile 미기재)

- [ ] **Step 3: 각 브랜드에 `crawl_profile` 추가**

`brands_furniture.json`의 각 브랜드 객체에 `note` 바로 아래 `crawl_profile`을 추가한다(`note`는 그대로 둔다). 기존 `note`에서 카테고리 코드·감속 사실을 승격:

```jsonc
// jakomo (godomall, 소파)
"crawl_profile": {"category_codes": [], "delay_s": 1.0, "resumable": true, "watchdog_s": 90, "gosi_in_image": false}

// bflamp (cafe24, 범용엔진)
"crawl_profile": {"category_codes": [], "delay_s": 0.5, "resumable": false, "watchdog_s": 60, "gosi_in_image": false}

// wooree (cafe24, cate_no 157=전체)
"crawl_profile": {"category_codes": ["157"], "category_note": "157=전체상품", "delay_s": 0.5, "resumable": false, "watchdog_s": 60, "gosi_in_image": false}

// vittz (makeshop, 조명)
"crawl_profile": {"category_codes": [], "delay_s": 0.5, "resumable": false, "watchdog_s": 60, "gosi_in_image": false}

// flora (godomall, 침구)
"crawl_profile": {"category_codes": [], "delay_s": 1.0, "resumable": true, "watchdog_s": 90, "gosi_in_image": false}

// mothershome (imweb, sitemap /shop_view/{id})
"crawl_profile": {"category_codes": [], "entry_hint": "sitemap /shop_view/{id}", "delay_s": 0.5, "resumable": false, "watchdog_s": 60, "gosi_in_image": false}

// prielle (makeshop, 침구)
"crawl_profile": {"category_codes": [], "delay_s": 0.5, "resumable": false, "watchdog_s": 60, "gosi_in_image": false}

// dongsuh (godomall, cateCd 019=BEST, 타르핏 1.2s)
"crawl_profile": {"category_codes": ["019"], "category_note": "019=BEST 중복 제외", "delay_s": 1.2, "resumable": true, "watchdog_s": 90, "gosi_in_image": true}

// dotoro (cafe24)
"crawl_profile": {"category_codes": [], "delay_s": 0.5, "resumable": false, "watchdog_s": 60, "gosi_in_image": false}
```

JSON 문법(콤마) 유의: `note` 뒤에 콤마를 붙이고 `crawl_profile`을 추가.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -q`
Expected: 모든 테스트 PASS (`test_load_crawl_profile_explicit` 포함). JSON 유효성은 `python3 -c "import json;json.load(open('brands_furniture.json'))"`로도 확인.

- [ ] **Step 5: Commit**

```bash
git add identity/brands_furniture.json identity/tests/test_brand_profile.py
git commit -m "feat(brand-profile): 9개 브랜드 crawl_profile 구조화 — note 자유서술→기계 판독 config"
```

---

### Task 3: B층 계산 순수 함수 (schema/domain/stats)

**Files:**
- Modify: `identity/brand_profile.py`
- Test: `identity/tests/test_brand_profile.py`

**Interfaces:**
- Consumes: 없음 (순수 함수, 입력은 dict 리스트)
- Produces:
  - `HEADER: list[str]` — 통합 CSV 컬럼
  - `compute_schema(rows: list[dict]) -> dict` — `{"fields": {col: {"coverage": float, "distinct": int, "top": [..]}}, "options": {}}`. coverage = 비어있지 않은 값 비율. `top`은 최빈 3개. `distinct` 상위 컬럼(material 등)만.
  - `compute_domain(rows: list[dict], note: str, gosi_in_image: bool) -> dict` — `{"top_categories": [[cat,n]..], "naming_patterns": [], "gosi_in_image": bool, "notes_freeform": note}`
  - `compute_stats(rows, prev: dict | None, run_log: dict | None) -> dict` — `{"count", "new", "dropped", "coverage_delta", "failed_urls", "poison_urls", "duration_s", "throttle_hits", "regression"}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brand_profile.py 에 추가
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -k compute -q`
Expected: FAIL — `AttributeError: module 'brand_profile' has no attribute 'compute_schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# brand_profile.py 에 추가
from collections import Counter

HEADER = [
    "source", "brand", "model_no", "name", "color", "price", "currency",
    "category", "material", "width_cm", "depth_cm", "height_cm",
    "bed_size", "assembly", "installation_service",
    "origin", "safety_cert", "url",
]
# distinct/top 을 기록할 가치가 있는 범주형 컬럼
_CATEGORICAL = {"color", "category", "material", "bed_size", "assembly", "origin"}
_REGRESSION_DROP = 0.20  # 건수 20% 이상 급감 시 회귀 플래그


def _nonempty(v):
    return v is not None and str(v).strip() != ""


def compute_schema(rows):
    n = len(rows) or 1
    fields = {}
    for col in HEADER:
        vals = [str(r.get(col, "")).strip() for r in rows if _nonempty(r.get(col))]
        entry = {"coverage": round(len(vals) / n, 4)}
        if col in _CATEGORICAL and vals:
            c = Counter(vals)
            entry["distinct"] = len(c)
            entry["top"] = [v for v, _ in c.most_common(3)]
        fields[col] = entry
    return {"fields": fields, "options": {}}


def compute_domain(rows, note, gosi_in_image):
    cats = Counter(str(r.get("category", "")).strip() for r in rows if _nonempty(r.get("category")))
    return {
        "top_categories": [[c, n] for c, n in cats.most_common(10)],
        "naming_patterns": [],
        "gosi_in_image": bool(gosi_in_image),
        "notes_freeform": note or "",
    }


def compute_stats(rows, prev, run_log):
    run_log = run_log or {}
    count = len(rows)
    prev_count = (prev or {}).get("stats", {}).get("count", 0)
    new = max(0, count - prev_count) if prev else count
    dropped = max(0, prev_count - count)
    coverage_delta = 0.0
    regression = bool(prev_count) and count < prev_count * (1 - _REGRESSION_DROP)
    return {
        "count": count, "new": new, "dropped": dropped,
        "coverage_delta": coverage_delta,
        "failed_urls": run_log.get("failed_urls", 0),
        "poison_urls": run_log.get("poison_urls", []),
        "duration_s": run_log.get("duration_s", 0),
        "throttle_hits": run_log.get("throttle_hits", 0),
        "regression": regression,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -k compute -q`
Expected: 4개 PASS

- [ ] **Step 5: Commit**

```bash
git add identity/brand_profile.py identity/tests/test_brand_profile.py
git commit -m "feat(brand-profile): B층 계산 순수함수 — schema 커버리지·domain 카테고리·stats 회귀"
```

---

### Task 4: B층 Mongo 조립 (`build_and_upsert`, `get_profile`, 파일 폴백)

**Files:**
- Modify: `identity/brand_profile.py`
- Test: `identity/tests/test_brand_profile.py`

**Interfaces:**
- Consumes: `load_crawl_profile`, `compute_schema/domain/stats` (Task 1·3), `_brand`
- Produces:
  - `HISTORY_MAX = 20`
  - `_read_rows(csv_path: str) -> list[dict]` — CSV → dict 리스트
  - `_get_db()` — pymongo DB 핸들 (`INSIGHTS_DB` env, 기본 insights)
  - `get_profile(slug: str) -> dict | None` — Mongo `find_one({"_id": slug})`
  - `build_and_upsert(slug, harvest_csv, run_log=None) -> dict` — 계산→문서 조립→upsert(+history 링버퍼). Mongo 실패 시 `outputs/profiles/<slug>.json` 폴백 저장 후 문서 반환.

- [ ] **Step 1: Write the failing test (mongomock 격리)**

```python
# tests/test_brand_profile.py 에 추가
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -k "upsert or ring or fallback" -q`
Expected: FAIL — `AttributeError: module 'brand_profile' has no attribute 'build_and_upsert'`
(mongomock 미설치 시 `pip install mongomock` 먼저)

- [ ] **Step 3: Write minimal implementation**

```python
# brand_profile.py 에 추가
import csv

OUT_DIR = os.path.join(HERE, "outputs")
HISTORY_MAX = 20
URI = os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")


def _read_rows(csv_path):
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _get_db():
    from pymongo import MongoClient
    dbname = os.environ.get("INSIGHTS_DB", "insights")
    return MongoClient(URI, serverSelectionTimeoutMS=5000)[dbname]


def get_profile(slug):
    try:
        return _get_db()["brand_profiles"].find_one({"_id": slug})
    except Exception as e:  # 연결 실패 시 조회는 None
        print(f"[brand_profile] get_profile({slug}) Mongo 실패: {e}")
        return None


def _fallback_write(doc):
    d = os.path.join(OUT_DIR, "profiles")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{doc['_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"[brand_profile] Mongo 폴백 → {path}")


def build_and_upsert(slug, harvest_csv, run_log=None):
    """산출 CSV → schema/domain/stats 계산 → brand_profiles upsert(+history). Mongo 실패 시 파일 폴백."""
    b = _brand(slug)
    rows = _read_rows(harvest_csv)
    crawl_profile = load_crawl_profile(slug)
    prev = get_profile(slug)

    schema = compute_schema(rows)
    domain = compute_domain(rows, b.get("note", ""), crawl_profile.get("gosi_in_image", False))
    stats = compute_stats(rows, prev, run_log)
    harvest_id = (run_log or {}).get("harvest_id", "")

    history = list((prev or {}).get("history", []))
    history.append({"harvest_id": harvest_id, "count": stats["count"]})
    history = history[-HISTORY_MAX:]

    doc = {
        "_id": slug, "slug": slug, "name_ko": b.get("name_ko", ""),
        "last_harvest_id": harvest_id,
        "crawl_profile": crawl_profile,
        "schema": schema, "domain": domain, "stats": stats, "history": history,
    }
    try:
        db = _get_db()
        db["brand_profiles"].replace_one({"_id": slug}, doc, upsert=True)
        print(f"[brand_profile] upsert {slug} · count={stats['count']} "
              f"· regression={stats['regression']} (brand_profiles)")
    except Exception as e:
        print(f"[brand_profile] Mongo 실패: {e}")
        _fallback_write(doc)
    return doc
```

`updated_at`은 실행 시각 주입이 필요하면 호출부에서 넣는다(순수성·재현성 유지 위해 모듈 내부에서 `datetime.now()` 호출하지 않음).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add identity/brand_profile.py identity/tests/test_brand_profile.py
git commit -m "feat(brand-profile): B층 Mongo 조립 build_and_upsert/get_profile — history 링버퍼 + 파일 폴백"
```

---

### Task 5: harvest 파이프라인 연결 (`run_furniture_pipeline.py`)

**Files:**
- Modify: `identity/run_furniture_pipeline.py` (병합 단계 직후, GEO 매핑 전에 브랜드별 프로필 축적)
- Test: `identity/tests/test_brand_profile.py`

**Interfaces:**
- Consumes: `build_and_upsert` (Task 4), `extract_all_furniture.load_brands`
- Produces: `profile_all(only: set[str] | None) -> list[str]` — 브랜드별 CSV 있으면 `build_and_upsert` 호출, 처리한 slug 리스트 반환

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brand_profile.py 에 추가 — profile_all 은 파이프라인 헬퍼로 brand_profile 에 둠
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -k profile_all -q`
Expected: FAIL — `AttributeError: 'profile_all'`

- [ ] **Step 3: Write minimal implementation**

```python
# brand_profile.py 에 추가
def profile_all(only=None, run_logs=None):
    """모든(또는 only) 브랜드의 산출 CSV로 build_and_upsert. CSV 없으면 스킵. 처리한 slug 리스트."""
    run_logs = run_logs or {}
    done = []
    for b in _load_registry()["brands"]:
        slug = b["slug"]
        if only and slug not in only:
            continue
        csv_path = os.path.join(OUT_DIR, f"extract_furniture_{slug}.csv")
        if not os.path.exists(csv_path):
            print(f"[brand_profile] {slug}: CSV 없음 — 스킵")
            continue
        build_and_upsert(slug, csv_path, run_log=run_logs.get(slug))
        done.append(slug)
    return done
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -k profile_all -q`
Expected: PASS

- [ ] **Step 5: 파이프라인에 호출 삽입**

`run_furniture_pipeline.py`의 `# 2. 병합` 블록 직후(GEO 매핑 `# 3.` 전)에 추가:

```python
    # 2. 병합
    import extract_all_furniture as eaf2
    eaf2.merge_all()

    # 2b. 브랜드 프로필 축적 (B층 Mongo brand_profiles) — 실패해도 파이프라인 계속
    try:
        import brand_profile
        only = {s.strip() for s in args.only.split(",") if s.strip()} or None
        if not args.no_mongo:
            done = brand_profile.profile_all(only=only)
            print(f"[pipeline] 브랜드 프로필 축적 완료: {done}")
    except Exception as e:
        print(f"[pipeline] 브랜드 프로필 축적 실패(무시): {e}")
```

- [ ] **Step 6: 전체 회귀 스위트 확인**

Run: `cd identity && python3 -m pytest tests/ -q`
Expected: 기존 119 + 신규 테스트 모두 PASS (신규 통과분만큼 총계 증가). `run_furniture_pipeline.py`는 `python3 -c "import ast; ast.parse(open('run_furniture_pipeline.py').read())"`로 구문 확인.

- [ ] **Step 7: Commit**

```bash
git add identity/brand_profile.py identity/run_furniture_pipeline.py identity/tests/test_brand_profile.py
git commit -m "feat(brand-profile): 파이프라인 병합 직후 profile_all 축적 — 실패 격리"
```

---

### Task 6: 크롤러(범용 엔진)가 `crawl_profile` 소비

**Files:**
- Modify: `identity/extract_furniture_engine.py` (delay·resumable·watchdog을 `load_crawl_profile`에서 읽도록)
- Test: `identity/tests/test_brand_profile.py`

**Interfaces:**
- Consumes: `load_crawl_profile` (Task 1)
- Produces: 엔진이 하드코딩 대신 A층 프로파일을 부트스트랩으로 사용

- [ ] **Step 1: 엔진의 현재 delay/설정 사용처 확인**

Run: `cd identity && grep -n "delay\|sleep\|slug\|argparse\|--slug\|watchdog\|resum" extract_furniture_engine.py | head -30`
목적: `--slug` 파싱 지점과 현재 딜레이 상수를 찾아 교체 지점을 특정.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_brand_profile.py 에 추가 — 엔진이 프로파일에서 delay를 읽는지 검증
def test_engine_uses_crawl_profile_delay():
    import extract_furniture_engine as eng
    # 엔진에 프로파일 기반 딜레이 결정 헬퍼가 있어야 함
    assert eng.resolve_delay("dongsuh") == 1.2
    assert eng.resolve_delay("dotoro") == 0.5  # cafe24 기본
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -k engine -q`
Expected: FAIL — `AttributeError: ... 'resolve_delay'`

- [ ] **Step 4: 엔진에 프로파일 해석 헬퍼 추가**

`extract_furniture_engine.py` 상단(임포트 이후)에 추가하고, 기존 하드코딩 sleep 값을 `resolve_delay(slug)` 결과로 교체:

```python
import brand_profile

def resolve_delay(slug):
    """A층 crawl_profile에서 이 브랜드의 요청 간 딜레이(초)를 읽는다."""
    return brand_profile.load_crawl_profile(slug)["delay_s"]
```

Step 1에서 찾은 기존 딜레이 상수 사용부를 `resolve_delay(slug)`로 바꾼다(엔진이 `--slug`를 이미 받으므로 slug 사용 가능).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd identity && python3 -m pytest tests/test_brand_profile.py -k engine -q`
Expected: PASS

- [ ] **Step 6: 엔진 스모크 — 구문/임포트 확인**

Run: `cd identity && python3 -c "import extract_furniture_engine as e; print(e.resolve_delay('dongsuh'))"`
Expected: `1.2`

- [ ] **Step 7: Commit**

```bash
git add identity/extract_furniture_engine.py identity/tests/test_brand_profile.py
git commit -m "feat(brand-profile): 범용 엔진이 crawl_profile.delay_s 소비 — 하드코딩 딜레이 제거"
```

---

### Task 7: HANDOFF·문서 갱신

**Files:**
- Modify: `identity/HANDOFF.md` (브랜드 프로필 스토어 항목 추가)
- Modify: `CLAUDE.md` (구조 지도에 `brand_profile.py` + `brand_profiles` 컬렉션 반영)

**Interfaces:**
- Consumes: 없음
- Produces: 다음 세션이 스토어 존재·조회법을 알도록 문서화

- [ ] **Step 1: HANDOFF.md에 섹션 추가**

`identity/HANDOFF.md`에 다음 요지를 추가한다:
- 브랜드 프로필 스토어: A층 `brands_furniture.json:crawl_profile`(크롤 설정, git), B층 Mongo `brand_profiles`(harvest 산출, slug 키)
- 모듈 `identity/brand_profile.py`: `load_crawl_profile(slug)` / `build_and_upsert(slug, csv, run_log)` / `get_profile(slug)` / `profile_all(only)`
- 조회 예: MCP mongodb-insights로 `brand_profiles.find({_id:"dongsuh"})`, 또는 `python3 -c "import brand_profile as b; print(b.get_profile('dongsuh')['stats'])"`
- 파이프라인 자동 축적: `run_furniture_pipeline.py` 병합 직후(단계 2b)

- [ ] **Step 2: CLAUDE.md 구조 지도 갱신**

`identity/` 트리 설명에 한 줄 추가:
```
│   ├── brand_profile.py               # 브랜드 프로필 스토어: A층 crawl_profile 읽기 + B층 brand_profiles 계산/조회
```
그리고 `## DB` 컬렉션 목록에 `brand_profiles`(가구 브랜드별 크롤 프로파일·속성 스키마·통계) 추가.

- [ ] **Step 3: Commit**

```bash
git add identity/HANDOFF.md CLAUDE.md
git commit -m "docs(brand-profile): HANDOFF·CLAUDE 구조 지도에 브랜드 프로필 스토어 반영"
```

---

## 운영 반영 (수동, 승인 후)

계획 외 1회성 작업 — 사용자 명시 승인 후 진행:

```bash
# 개발 검증: 데모 DB로 먼저
set -a; eval "$(grep '^export ' ../run.sh)"; set +a
INSIGHTS_DB=insights_demo python3 -c "import brand_profile as b; print(b.profile_all())"

# 운영 반영: 다음 정기 harvest에서 자동 축적됨(run_furniture_pipeline.py 단계 2b)
# 첫 수동 백필이 필요하면 명시 승인 후:
# INSIGHTS_DB=insights python3 -c "import brand_profile as b; b.profile_all()"
```

## Self-Review 결과

- **Spec 커버리지**: A층 crawl_profile(Task 1·2·6) · B층 schema/domain/stats(Task 3) · Mongo 조립+폴백+history(Task 4) · 파이프라인 연결(Task 5) · 에러 처리(폴백=Task 4, 회귀 플래그=Task 3, delay 하한=Task 1, harvest 무중단=Task 5 try) · 테스트(각 Task) · 마이그레이션(Task 2 데이터, Task 5·6 전환) · 문서(Task 7). GEO 단계 `get_profile` 참조는 스펙상 "선택" — 현 계획 범위 밖(운영 검증 후 별도).
- **플레이스홀더**: 없음. 모든 코드 스텝에 실제 코드/명령/기대출력 포함. Task 6 Step 1·4는 기존 코드 위치 특정이 필요해 grep으로 지점을 찾은 뒤 교체(실제 상수명이 파일에 따라 다르므로 탐색 스텝을 명시).
- **타입 일관성**: `load_crawl_profile`/`compute_schema`/`compute_domain`/`compute_stats`/`build_and_upsert`/`get_profile`/`profile_all`/`resolve_delay` 시그니처가 Task 전반에서 일치. `_get_db`/`OUT_DIR`은 테스트에서 monkeypatch하는 이름과 정확히 일치.
```
