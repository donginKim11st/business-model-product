"""브랜드 프로필 스토어 — A층(brands_furniture.json crawl_profile) 읽기 +
B층(Mongo brand_profiles) 계산/조회. 설계: docs/superpowers/specs/2026-07-14-brand-profile-store-design.md
"""
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
