#!/usr/bin/env python3
"""가구/인테리어 identity 파이프라인 — 온보딩→추출→병합→매핑→QA→리포트 원샷.

  python3 run_furniture_pipeline.py                        # 전체 (신규 추출만)
  python3 run_furniture_pipeline.py --force                # 전량 재추출
  python3 run_furniture_pipeline.py --only jakomo,flora    # 특정 브랜드만
  python3 run_furniture_pipeline.py --skip-extract         # 추출 생략(매핑부터)
  python3 run_furniture_pipeline.py --parallel 4           # 브랜드 병렬 추출
  python3 run_furniture_pipeline.py --onboard "브랜드명" [--url https://…]  # 신규 브랜드 먼저 등록

단계:
  0. (옵션) onboard_brand.py — 브랜드 온보딩
  1. 추출 — 전용 스크립트 > 범용 엔진 (extract_all_furniture.load_brands 규칙)
  2. 병합 — outputs/furniture_all_brands.csv
  3. GEO 매핑 — map_geo_furniture.py → furniture_geo_mapped.jsonl + 리포트
  4. QA 게이트 — qa_geo_mapping.py (적대 검증에서 도출된 회귀 규칙)
     실패 시 exit 1 — 규칙 수정 후 재실행 (매핑 규칙 개선 → 3부터)
  5. 리포트 — outputs/furniture_geo_report.md

주기 실행: cron 또는 n8n. 예) 매주 재추출 후 리포트 갱신:
  0 6 * * 1 cd ~/Work/business-model/identity && python3 run_furniture_pipeline.py --force
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, HERE)


def sh(cmd, check=True):
    print(f"\n$ {' '.join(cmd)}")
    ret = subprocess.run([PY] + cmd, cwd=HERE)
    if check and ret.returncode != 0:
        print(f"[pipeline] 단계 실패 (rc={ret.returncode}) — 중단")
        sys.exit(ret.returncode)
    return ret.returncode


def extract_parallel(slugs_cmds, jobs):
    """브랜드 추출 병렬 실행 (프로세스 N개)."""
    running = []  # (slug, Popen)
    queue = list(slugs_cmds)
    log_dir = os.path.join(HERE, "outputs")
    while queue or running:
        while queue and len(running) < jobs:
            slug, cmd = queue.pop(0)
            log = open(os.path.join(log_dir, f"_run_{slug}.log"), "w")
            p = subprocess.Popen([PY, "-u"] + cmd, cwd=HERE, stdout=log, stderr=log)
            running.append((slug, p))
            print(f"[extract] {slug} 시작 (동시 {len(running)})")
        for slug, p in running[:]:
            if p.poll() is not None:
                running.remove((slug, p))
                print(f"[extract] {slug} 종료 (rc={p.returncode})")
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--skip-extract", action="store_true")
    ap.add_argument("--parallel", type=int, default=1, help="브랜드 동시 추출 수")
    ap.add_argument("--onboard", default="", help="신규 브랜드명 (탐색→등록 후 진행)")
    ap.add_argument("--url", default="", help="--onboard 시 공식몰 URL 직접 지정")
    ap.add_argument("--strict-qa", action="store_true")
    ap.add_argument("--no-mongo", action="store_true", help="몽고 적재 생략")
    args = ap.parse_args()

    # 0. 온보딩
    if args.onboard:
        cmd = ["onboard_brand.py", args.onboard]
        if args.url:
            cmd += ["--url", args.url]
        sh(cmd)

    # 1. 추출
    if not args.skip_extract:
        import extract_all_furniture as eaf
        eaf.load_keys()
        only = {s.strip() for s in args.only.split(",") if s.strip()}
        targets = []
        for slug, cmd in eaf.load_brands():
            if only and slug not in only:
                continue
            if cmd is None:
                print(f"[extract] {slug}: 어댑터/엔진 없음 — 스킵")
                continue
            if not args.force and eaf.rowcount(slug) > 0:
                print(f"[extract] {slug}: 이미 {eaf.rowcount(slug)}행 — 스킵")
                continue
            targets.append((slug, cmd))
        if targets:
            if args.parallel > 1:
                extract_parallel(targets, args.parallel)
            else:
                for slug, cmd in targets:
                    sh(cmd, check=False)  # 브랜드 하나 실패해도 계속
        else:
            print("[extract] 대상 없음")

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

    # 3. GEO 매핑 + 리포트
    sh(["map_geo_furniture.py"])

    # 4. QA 게이트
    qa_cmd = ["qa_geo_mapping.py"] + (["--strict"] if args.strict_qa else [])
    rc = sh(qa_cmd, check=False)
    if rc != 0:
        print("[pipeline] QA 실패 — map_geo_furniture.py 규칙 수정 후 "
              "--skip-extract 로 재실행하세요")
        sys.exit(1)

    # 5. 카탈로그 생성 (decompose → group → 골든 회귀 verify)
    rc = sh(["furniture_catalog.py", "all"], check=False)
    if rc != 0:
        print("[pipeline] 카탈로그 골든 스위트 실패 — furniture_catalog.py 규칙 확인")
        sys.exit(1)

    # 6. MongoDB 적재 (insights.furniture_products)
    if not args.no_mongo:
        rc = sh(["furniture_load_mongo.py"], check=False)
        if rc != 0:
            print("[pipeline] 몽고 적재 실패 (연결 확인) — 파일 산출물은 정상")

    print("\n[pipeline] 완료 — outputs/furniture_geo_report.md")


if __name__ == "__main__":
    main()
