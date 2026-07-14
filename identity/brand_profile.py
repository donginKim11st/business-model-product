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
