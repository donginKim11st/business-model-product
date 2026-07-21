#!/usr/bin/env python3
"""카탈로그(ctlg_no=SKU)별 실제 비정형 인사이트 추출 — 네이버 리뷰 → LLM(실측 근거).

패키지(1차) 인사이트와 별개로, 각 카탈로그의 풀네임(disp)으로 네이버 블로그를 모아 LLM으로
그 카탈로그 고유 인사이트를 추출해 catalogs[].insight 에 저장한다(대표 dim별 best point + faq + 근거수).
모달에서 카탈로그 클릭 시 이걸 보여준다. 전부 실측 근거(합성/공유 없음).

재개 안전: insight 있으면 skip(--refresh). 우선순위 = 가격 보유 + 카탈로그 많은 패키지.

  set -a; eval "$(grep '^export ' run.sh)"; set +a
  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/catalog_insight_backfill.py --limit 200 --per-pkg 2
"""
import os
import re
import sys
import time
import threading
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("INSIGHT_MODEL", "gpt-4o-mini")

import run_batch
import naver_review_geo as nrg  # noqa
import load_mongo
from pymongo import MongoClient
from insight_engine.versioning import build_run_meta
from insight_engine.types import EngineConfig

_JUNK = re.compile(r"★[^★]*★|\[[^\]]*\]|[（(][^)）]*[)）]")


def clean(disp):
    return re.sub(r"\s+", " ", _JUNK.sub(" ", disp or "")).strip()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_insight(block, n_items, per_dim=3, max_dims=6):
    """추출 block → 카탈로그 insight(대표 dim별 best point + faq). representative 와 같은 shape."""
    tax = (block or {}).get("taxonomy") or {}
    dims = []
    for dim_path, pts in load_mongo.walk_points(tax):
        if not pts:
            continue
        best = sorted(pts, key=lambda p: -(p.get("cited_examples") or 0))[:per_dim]
        dims.append({"dim": dim_path, "label": load_mongo.dim_label(dim_path),
                     "points": [{"point": p.get("point"), "cited_examples": p.get("cited_examples") or 0,
                                 "evidence": p.get("evidence") or []} for p in best]})
    dims.sort(key=lambda d: -sum(p["cited_examples"] for p in d["points"]))
    return {"dims": dims[:max_dims], "faqs": (block or {}).get("faqs") or [],
            "n_sources": n_items, "fetched_at": now_iso(), "source": "naver_review"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16, help="동시 추출 워커 수(카탈로그당 대부분 LLM이라 네이버 QPS는 낮음)")
    ap.add_argument("--limit", type=int, default=0, help="처리 카탈로그 수(0=전체)")
    ap.add_argument("--priced-only", action="store_true", help="가격 보유 카탈로그만")
    ap.add_argument("--retries", type=int, default=2, help="실패 시 재시도(429 등 일시 오류)")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--retry-empty", action="store_true",
                    help="근거 0건으로 비어버린 insight(일시오류 의심)를 다시 큐에 넣어 재수집(attempts 한도까지)")
    ap.add_argument("--retry-empty-max", type=int, default=3,
                    help="빈 insight 재시도 상한 — 이만큼 시도해도 0건이면 '진짜 리뷰 없음'으로 확정(무한루프 방지)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    use_claude = os.environ.get("INSIGHT_LLM") == "claude"  # claude -p 헤드리스(구독) 어댑터
    nid = os.environ.get("NAVER_CLIENT_ID"); nsec = os.environ.get("NAVER_CLIENT_SECRET")
    if not args.dry_run and not (nid and nsec and (use_claude or os.environ.get("OPENAI_API_KEY"))):
        sys.exit("✗ NAVER_CLIENT_ID/SECRET, OPENAI_API_KEY 필요(INSIGHT_LLM=claude 면 OPENAI 불필요)")

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]

    # 전체 카탈로그(패키지에 매칭된 ctlg_no 전부). 카탈로그 많은 패키지(중요 상품) 먼저.
    pkgs = list(db.products.find({"type": "package"}, {"_id": 1, "catalogs": 1}))
    pkgs.sort(key=lambda p: -len(p.get("catalogs") or []))
    queue = []
    for p in pkgs:
        for c in p.get("catalogs") or []:
            if not c.get("ctlg_no"):
                continue
            if args.priced_only and not (c.get("price_summary") or {}).get("min"):
                continue
            ins = c.get("insight")
            if ins and not args.refresh:
                # 근거 0건 빈 insight는 일시오류로 비었을 수 있어 --retry-empty 시 재수집(attempts 한도 내).
                empty = not ins.get("dims") and (ins.get("n_sources") or 0) == 0
                retryable = empty and (ins.get("attempts") or 0) < args.retry_empty_max
                if not (args.retry_empty and retryable):
                    continue
            queue.append((p["_id"], c.get("ctlg_no"), c.get("disp"), (ins or {}).get("attempts") or 0))
    if args.limit:
        queue = queue[:args.limit]
    N = len(queue)
    llm_name = "claude(구독)" if use_claude else os.environ["INSIGHT_MODEL"]
    print(f"카탈로그 인사이트 추출 {N}개 · 병렬 워커 {args.workers} · {llm_name}")
    print("=" * 64)
    if args.dry_run:
        for pk, ct, dp in queue[:15]:
            print(f"  {ct}  '{clean(dp)[:44]}'")
        return

    if use_claude:
        import claude_llm
        llm = claude_llm.make_client()
        extract_fn = claude_llm.extract_full_combo  # 3콜→1콜 통합(스니펫 1회 전송, 구독 절약)
    else:
        llm = run_batch.make_client()
        extract_fn = run_batch.extract_full
    lock = threading.Lock()
    st = {"i": 0, "ok": 0, "empty": 0, "err": 0, "quota": 0}
    t0 = time.time()

    def work(task):
        pkg_uid, ctlg, disp, prev_attempts = task
        kw = clean(disp)
        items, block = None, None
        for attempt in range(args.retries + 1):
            try:
                items = run_batch.collect(kw, nid, nsec, raise_blog_quota=True)
                block = extract_fn(kw, items, llm) if items else None
                break
            except run_batch.QuotaStop:
                # 네이버 쿼터(429) — insight 미기록(큐 유지). 오염 없이 다음 패스가 이어받음.
                with lock:
                    st["quota"] += 1; st["i"] += 1
                return
            except Exception:
                if attempt >= args.retries:
                    with lock:
                        st["err"] += 1; st["i"] += 1
                    return
                time.sleep(1.5 * (attempt + 1))
        ins = (to_insight(block, len(items)) if block else
               {"dims": [], "faqs": [], "n_sources": len(items or []), "attempts": prev_attempts + 1,
                "fetched_at": now_iso(), "source": "naver_review"})
        # 재현성: 이 인사이트가 어떤 엔진·프롬프트·모델 버전으로 나왔는지 기록(정직하게 실제 모델명).
        real_model = "claude(subscription)" if use_claude else os.environ.get("INSIGHT_MODEL", "gpt-4o-mini")
        ins["run_meta"] = build_run_meta(EngineConfig(model=real_model))
        try:
            db.products.update_one({"_id": pkg_uid}, {"$set": {"catalogs.$[c].insight": ins}},
                                   array_filters=[{"c.ctlg_no": ctlg}])
        except Exception:
            pass
        with lock:
            st["i"] += 1
            st["ok" if ins["dims"] else "empty"] += 1
            i = st["i"]
        if i % 25 == 0 or i == N:
            el = time.time() - t0; rate = i / el * 60 if el else 0
            eta = (N - i) / (rate / 60) if rate else 0
            print(f"  [{i}/{N}] ok {st['ok']} empty {st['empty']} err {st['err']} · "
                  f"{rate:.0f}건/분 · 남은 ~{int(eta // 60)}분", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, queue))
    print("=" * 64)
    if use_claude:
        import claude_llm
        print(f"완료 · 인사이트 {st['ok']} · 빈것 {st['empty']} · 오류 {st['err']} · 쿼터미처리 {st['quota']} · "
              f"{time.time()-t0:.0f}s · {claude_llm.summary()}")
    else:
        cost = run_batch.usd()
        print(f"완료 · 인사이트 {st['ok']} · 빈것 {st['empty']} · 오류 {st['err']} · 쿼터미처리 {st['quota']} · "
              f"{time.time()-t0:.0f}s · LLM ≈ ${cost:.3f} (≈₩{cost*1380:.0f})")


if __name__ == "__main__":
    main()
