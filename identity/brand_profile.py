"""브랜드 프로필 스토어 — A층(brands_furniture.json crawl_profile) 읽기 +
B층(Mongo brand_profiles) 계산/조회. 설계: docs/superpowers/specs/2026-07-14-brand-profile-store-design.md
"""
import csv
import json
import os
from collections import Counter

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
        entry = {"coverage": len(vals) / n}
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
    regression = bool(prev_count) and count <= prev_count * (1 - _REGRESSION_DROP)
    return {
        "count": count, "new": new, "dropped": dropped,
        "coverage_delta": coverage_delta,
        "failed_urls": run_log.get("failed_urls", 0),
        "poison_urls": run_log.get("poison_urls", []),
        "duration_s": run_log.get("duration_s", 0),
        "throttle_hits": run_log.get("throttle_hits", 0),
        "regression": regression,
    }


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
