#!/usr/bin/env python3
"""load_mongo._preserve_async_fields 단위 테스트 (reload 보존).

재적재(ReplaceOne) 시 backfill·주간배치 산출(youtube/representative/identity)이
새 인사이트 문서에 보존 머지되는지 검증. T1(reload 보존)의 회귀 가드.

실행: python3 insight/db/test_preserve_async_fields.py   (또는 pytest)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from load_mongo import _preserve_async_fields


def _fresh():
    """build_for_product가 만드는 새 product 문서의 관련 부분(placeholder youtube)."""
    return {"_id": "P1", "keyword": "x", "youtube": {"status": "pending", "attempts": 0}}


def test_empty_existing_is_noop():
    p = _fresh()
    _preserve_async_fields(p, {})
    assert p["youtube"] == {"status": "pending", "attempts": 0}
    assert "identity" not in p
    assert "representative" not in p


def test_identity_preserved():
    p = _fresh()
    ident = {"brand": "나이키", "status": "done", "fetched_at": "2026-06-30"}
    _preserve_async_fields(p, {"identity": ident})
    assert p["identity"] == ident


def test_identity_empty_status_preserved():
    # 식품 과도기: 산출 없음을 status:empty 로 마킹 → 보존되어 재씨앗 안 됨.
    p = _fresh()
    _preserve_async_fields(p, {"identity": {"status": "empty"}})
    assert p["identity"] == {"status": "empty"}


def test_identity_absent_not_injected():
    p = _fresh()
    _preserve_async_fields(p, {"youtube": {"status": "done"}})
    assert "identity" not in p


def test_representative_preserved():
    p = _fresh()
    _preserve_async_fields(p, {"representative": {"rank": 3}})
    assert p["representative"] == {"rank": 3}


def test_youtube_done_preserved_whole():
    p = _fresh()
    yt = {"status": "done", "taxonomy": {"a": 1}, "n_videos": 5}
    _preserve_async_fields(p, {"youtube": yt})
    assert p["youtube"] == yt


def test_youtube_pending_with_attempts_carried():
    p = _fresh()
    _preserve_async_fields(p, {"youtube": {"status": "pending", "attempts": 2, "last_error": "rate"}})
    assert p["youtube"]["status"] == "pending"
    assert p["youtube"]["attempts"] == 2
    assert p["youtube"]["last_error"] == "rate"


def test_youtube_pending_no_attempts_keeps_new():
    p = _fresh()
    _preserve_async_fields(p, {"youtube": {"status": "pending", "attempts": 0}})
    assert p["youtube"] == {"status": "pending", "attempts": 0}


def test_coexistence_identity_and_youtube():
    # 같은 reload에서 youtube(보존) + identity(보존) 공존 — 상호 미간섭.
    p = _fresh()
    _preserve_async_fields(p, {"youtube": {"status": "done"}, "identity": {"brand": "B", "status": "done"}})
    assert p["youtube"] == {"status": "done"}
    assert p["identity"] == {"brand": "B", "status": "done"}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
