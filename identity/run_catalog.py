#!/usr/bin/env python3
"""카탈로그명 추출 원샷 러너: Stage1(분해) → Stage2(모델 묶음).

  python3 run_catalog.py                 # 규칙만(무료)
  python3 run_catalog.py --limit 500     # 골든 샘플
  python3 run_catalog.py --llm-gate --llm-limit 300   # 잔여 300행만 LLM 보정
"""
import os
import sys
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalog_decompose as cd
import catalog_group as cg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--llm-gate", action="store_true")
    ap.add_argument("--llm-limit", type=int, default=0)
    ap.add_argument("--dec-out", default=cd.OUT_DEFAULT)
    ap.add_argument("--cat-out", default=cg.OUT_DEFAULT)
    args = ap.parse_args()

    s1 = cd.run_stage1(cd.IN_DEFAULT, args.dec_out, args.limit, args.llm_gate, args.llm_limit)
    s2 = cg.run_stage2(args.dec_out, args.cat_out, args.llm_gate, args.llm_limit)
    print("─" * 50)
    print("완료 · 행 %d(needs_llm %d) → 카탈로그 %d" % (s1["rows"], s1["needs_llm"], s2["catalogs"]))


if __name__ == "__main__":
    main()
