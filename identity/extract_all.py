#!/usr/bin/env python3
"""30개 브랜드 공식몰 정형 데이터 전수 추출 — 단일 진입점.

  python3 extract_all.py                # 전체(없는 것만) 추출 → OCR 보강 → 대시보드
  python3 extract_all.py --force        # 기존 CSV 무시하고 전부 재추출
  python3 extract_all.py --only fila,puma   # 특정 브랜드만
  python3 extract_all.py --skip-ocr     # 고시 이미지 OCR 보강 건너뜀
  python3 extract_all.py --dashboard    # 추출 건너뛰고 병합+대시보드만

키는 ~/Work/business-model/run.sh 에서 자동 로드(있으면): OPENAI_API_KEY(고시 OCR),
NAVER_CLIENT_ID/SECRET(naver 경유). 언블로커 키(UNBLOCKER_PROVIDER/KEY) 있으면 아디다스가
브라우저 없이 그걸로, 없으면 patchright headed 폴백.

각 브랜드 어댑터는 outputs/extract_brand_<slug>.csv (또는 nike/nb는 extract_<slug>.csv) 생성.
플랫폼별 방식: cafe24/Shopify/Demandware=JSON-LD, k-village/자체몰=DOM, 나이키/아디다스=봇차단우회.
"""
import csv
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
PY = sys.executable  # 현재 파이썬(키 로드된 환경) 기준

# ── 브랜드 레지스트리: slug → (스크립트 명령, 결과 CSV, 비고) ──────────────
# cmd 는 PY 뒤에 붙는 인자 리스트. 결과는 csv_path 에 생성됨.
BRANDS = [
    # JSON-LD / DOM 자체 스크립트 (인자 없이 실행 → extract_brand_<slug>.csv)
    ("fila", ["extract_fila.py"]), ("puma", ["extract_brand_puma.py"]),
    ("crocs", ["extract_crocs.py"]), ("underarmour", ["extract_underarmour.py"]),
    ("lecaf", ["extract_lecaf.py"]), ("jansport", ["extract_jansport.py"]),
    ("arena", ["extract_brand_arena.py"]), ("proworldcup", ["extract_proworldcup.py"]),
    ("kolping", ["kolping_extract.py"]), ("northface", ["extract_northface.py"]),
    ("natgeo", ["extract_natgeo.py"]), ("starsports", ["extract_starsports.py"]),
    ("skechers", ["extract_skechers.py"]), ("prospecs", ["extract_prospecs.py"]),
    ("worldcup", ["extract_worldcup.py"]), ("vans", ["extract_vans.py"]),
    ("blackyak", ["extract_blackyak.py"]), ("montbell", ["extract_montbell.py"]),
    ("millet", ["extract_millet.py"]), ("nepa", ["extract_nepa.py"]),
    ("columbia", ["extract_columbia.py"]), ("redface", ["extract_redface.py"]),
    ("mizuno", ["mizuno_extract.py"]), ("westwood", ["westwood_extract.py"]),
    ("eider", ["eider_extract.py"]), ("outdoorproducts", ["extract_outdoorproducts.py"]),
    # 통합 엔진(official_extract) 경유
    ("nike", ["official_extract.py", "nike", "에어포스1", "15"]),
    ("nb", ["official_extract.py", "nb", "all"]),
    # 봇차단 우회 (patchright/unblocker)
    ("adidas", ["extract_adidas.py"]),
    # k2: k-village 내부 JSON (eider 와 동일 사이트) — 스크립트 있으면
    ("k2", ["extract_k2.py"]),
]
# 고시가 이미지/렌더텍스트라 OCR 보강 대상 (OPENAI_API_KEY 필요)
OCR_BRANDS = ["mizuno", "montbell", "outdoorproducts", "westwood", "proworldcup", "crocs", "kolping"]


def csv_path(slug):
    for p in (f"extract_brand_{slug}.csv", f"extract_{slug}.csv"):
        fp = os.path.join(OUT, p)
        if os.path.exists(fp):
            return fp
    return os.path.join(OUT, f"extract_brand_{slug}.csv")


def rowcount(slug):
    fp = csv_path(slug)
    if not os.path.exists(fp):
        return 0
    return max(0, sum(1 for _ in open(fp, encoding="utf-8-sig")) - 1)


def load_keys():
    """run.sh 의 export 라인을 현재 프로세스 환경에 로드(값 미출력)."""
    run = os.path.expanduser("~/Work/business-model/run.sh")
    if not os.path.exists(run):
        return
    for line in open(run, encoding="utf-8"):
        line = line.strip()
        if line.startswith("export ") and "=" in line:
            k, _, v = line[len("export "):].partition("=")
            v = v.strip().strip('"').strip("'")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v


def run_brand(slug, cmd, force):
    script = os.path.join(HERE, cmd[0])
    if not os.path.exists(script):
        print(f"  [skip] {slug}: 스크립트 없음 ({cmd[0]})")
        return False
    n = rowcount(slug)
    if n >= 10 and not force:
        print(f"  [have] {slug}: {n}행 (이미 있음, --force로 재추출)")
        return True
    print(f"  [run ] {slug}: {' '.join(cmd)} …")
    try:
        r = subprocess.run([PY] + [os.path.join(HERE, cmd[0])] + cmd[1:],
                           cwd=HERE, capture_output=True, timeout=1800, text=True)
        n2 = rowcount(slug)
        tail = (r.stdout or r.stderr).strip().splitlines()[-1:] or [""]
        print(f"        → {n2}행  {tail[0][:60]}")
        return n2 > 0
    except subprocess.TimeoutExpired:
        print(f"        ✗ 타임아웃")
        return False
    except Exception as e:
        print(f"        ✗ {str(e)[:50]}")
        return False


def run_ocr(force):
    if not os.environ.get("OPENAI_API_KEY"):
        print("  [skip] OCR: OPENAI_API_KEY 없음")
        return
    for slug in OCR_BRANDS:
        gp = os.path.join(OUT, f"gosi_{slug}.csv")
        if os.path.exists(gp) and not force:
            print(f"  [have] gosi:{slug} (이미 있음)")
            continue
        print(f"  [ocr ] {slug} …")
        try:
            subprocess.run([PY, os.path.join(HERE, "ocr_openai.py"), slug],
                          cwd=HERE, capture_output=True, timeout=3600, text=True)
        except Exception as e:
            print(f"        ✗ {str(e)[:50]}")


def merge_gosi():
    """gosi_<slug>.csv 의 origin/material/mfg_date 를 브랜드 CSV에 병합."""
    for gf in glob.glob(os.path.join(OUT, "gosi_*.csv")):
        slug = os.path.basename(gf)[5:-4]
        bf = csv_path(slug)
        if not os.path.exists(bf):
            continue
        gosi = {r["style_code"]: r for r in csv.DictReader(open(gf, encoding="utf-8-sig"))}
        rows = list(csv.DictReader(open(bf, encoding="utf-8-sig")))
        if not rows:
            continue
        cols = list(rows[0].keys())
        for r in rows:
            g = gosi.get(r.get("style_code", ""))
            if g:
                if g.get("material"):
                    r["material"] = g["material"]
                if g.get("origin"):
                    r["origin"] = g["origin"]
                if g.get("mfg_date") and "mfg_date" in cols:
                    r["mfg_date"] = g["mfg_date"]
        with open(bf, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)


def build_dashboard():
    subprocess.run([PY, os.path.join(HERE, "all_brands_html.py")], cwd=HERE)


def main():
    args = sys.argv[1:]
    force = "--force" in args
    skip_ocr = "--skip-ocr" in args
    only = None
    if "--only" in args:
        only = set(args[args.index("--only") + 1].split(","))
    dashboard_only = "--dashboard" in args

    load_keys()
    print(f"키: OPENAI={'O' if os.environ.get('OPENAI_API_KEY') else 'X'} · "
          f"NAVER={'O' if os.environ.get('NAVER_CLIENT_ID') else 'X'} · "
          f"UNBLOCKER={os.environ.get('UNBLOCKER_PROVIDER', 'X')}")

    if not dashboard_only:
        print("\n── 1) 브랜드 추출 ──")
        for slug, cmd in BRANDS:
            if only and slug not in only:
                continue
            run_brand(slug, cmd, force)
            time.sleep(0.2)

        if not skip_ocr:
            print("\n── 2) 고시 이미지 OCR 보강 ──")
            run_ocr(force)

    print("\n── 3) 병합 + 대시보드 ──")
    merge_gosi()
    build_dashboard()

    # 요약
    print("\n── 결과 요약 ──")
    total = 0
    for slug, _ in BRANDS:
        n = rowcount(slug)
        total += n
        if n:
            print(f"  {slug:16} {n:>5}행")
    print(f"  {'합계':16} {total:>5}행")
    print("→ outputs/all_brands.csv, outputs/all_brands_dashboard.html")


if __name__ == "__main__":
    main()
