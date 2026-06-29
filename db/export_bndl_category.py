#!/usr/bin/env python3
"""번들(bndl_grp) → 11번가 canonical 카테고리(DISP_CTGR1_NM) 매핑 export.

카테고리 랭킹(category_rank.py)의 grouping 키가 될 '번들의 카테고리'는 Oracle
pd_ctlg 에만 있어 repo 밖이다. 이 스크립트가 그 매핑을 jsonl로 떨궈 적재기(load_mongo.py)와
랭킹 배치(category_rank.py)가 Oracle 없이도 카테고리로 묶을 수 있게 한다.

  카테고리(DISP_CTGR1_NM) → 번들(BNDL_CTLG_GRP_NO) → 카탈로그(CTLG_NO)
  한 번들 안 카탈로그들의 DISP_CTGR1_NM '최빈값'을 그 번들의 카테고리로 정한다(투표).
  (archive/make_browse.py 와 동일 조인·투표 규칙.)

접속(키는 환경변수, 값 출력 안 함):
  ORA_USER=.. ORA_PW=.. [ORA_HOST=172.18.176.69 ORA_PORT=1528 ORA_SID=TMALL ORA_LIB=/opt/homebrew/lib] \
    python3 db/export_bndl_category.py [--out bndl_category.jsonl] [--reg-typ 1002,801]

출력(번들당 1줄):
  {"bndl_grp": 7863, "ctgr1": "...", "ctgr2": "...", "ctgr_path": "..>..", "n_ctlg": 5, "ctgr1_votes": {"...": 5}}
"""
import os
import sys
import json
import argparse
from collections import defaultdict, Counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="bndl_category.jsonl")
    ap.add_argument("--reg-typ", default="1002,801",
                    help="REG_TYP_CD IN (...) 콤마구분 (식품=1002, 뷰티=801)")
    args = ap.parse_args()

    if not (os.environ.get("ORA_USER") and os.environ.get("ORA_PW")):
        sys.exit("✗ ORA_USER / ORA_PW 환경변수 필요")

    import oracledb  # thick 모드 — 구버전 verifier 지원(archive/db_extract.py 와 동일)
    oracledb.init_oracle_client(lib_dir=os.environ.get("ORA_LIB", "/opt/homebrew/lib"))
    conn = oracledb.connect(
        user=os.environ["ORA_USER"], password=os.environ["ORA_PW"],
        dsn=oracledb.makedsn(os.environ.get("ORA_HOST", "172.18.176.69"),
                             int(os.environ.get("ORA_PORT", "1528")),
                             sid=os.environ.get("ORA_SID", "TMALL")))
    cur = conn.cursor()
    cur.arraysize = 50000

    reg = [r.strip() for r in args.reg_typ.split(",") if r.strip()]
    placeholders = ",".join(f"'{r}'" for r in reg)
    # 카탈로그 단위로 카테고리(1차/2차)를 끌어와 번들로 집계. archive/make_browse.py 조인 동일.
    cur.execute(f"""
        SELECT c.BNDL_CTLG_GRP_NO, c.CTLG_NO, l.DISP_CTGR1_NM, l.DISP_CTGR2_NM
          FROM pd_ctlg c
          JOIN pd_bndl_ctlg_grp g ON c.BNDL_CTLG_GRP_NO = g.BNDL_CTLG_GRP_NO
          LEFT JOIN DP_DISP_CTGR_LIST l ON c.DISP_CTGR_NO = l.DISP_CTGR_NO
         WHERE c.REG_TYP_CD IN ({placeholders})
           AND c.BNDL_CTLG_GRP_NO IS NOT NULL
    """)

    votes1 = defaultdict(Counter)          # bndl_grp -> Counter(ctgr1)
    path_votes = defaultdict(Counter)      # bndl_grp -> Counter("ctgr1 > ctgr2")
    n_ctlg = Counter()                     # bndl_grp -> 카탈로그 수
    while True:
        rows = cur.fetchmany()
        if not rows:
            break
        for grp, ctlg_no, c1, c2 in rows:
            if grp is None:
                continue
            n_ctlg[grp] += 1
            if c1:
                votes1[grp][c1] += 1
                path_votes[grp][" > ".join(x for x in (c1, c2) if x)] += 1
    conn.close()

    n = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for grp in sorted(n_ctlg):
            v1 = votes1.get(grp)
            ctgr1 = v1.most_common(1)[0][0] if v1 else None
            path = path_votes[grp].most_common(1)[0][0] if path_votes.get(grp) else (ctgr1 or "")
            ctgr2 = path.split(" > ")[1] if " > " in path else None
            rec = {"bndl_grp": int(grp) if str(grp).isdigit() else grp,
                   "ctgr1": ctgr1, "ctgr2": ctgr2, "ctgr_path": path,
                   "n_ctlg": n_ctlg[grp],
                   "ctgr1_votes": dict(v1.most_common(5)) if v1 else {}}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1

    n_cat = len({votes1[g].most_common(1)[0][0] for g in votes1 if votes1[g]})
    print(f"export 완료 → {args.out} · 번들 {n:,}개 · DISP_CTGR1 종류 {n_cat}개 "
          f"· 카테고리 미상 {sum(1 for g in n_ctlg if not votes1.get(g)):,}개")


if __name__ == "__main__":
    main()
