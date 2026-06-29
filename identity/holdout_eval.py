#!/usr/bin/env python3
"""
Held-out generalization check.

Runs the SAME pipeline (no retuning, no lexicon additions) on data/listings_holdout.json,
whose brands are deliberately ABSENT from the demo bilingual lexicon. Reports the
in-sample vs held-out blocking recall so the in-sample 100% is put in context.

    python3 holdout_eval.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pig.blocking import MinHashLSHBlocker, HybridBlocker
from pig.resolve import resolve
from pig.evaluate import blocking_recall, prf, true_match_pairs, cluster_stats
from pig.normalize import extract_attributes


def load(path):
    with open(os.path.join(HERE, "data", path), encoding="utf-8") as f:
        return json.load(f)["listings"]


def per_pair_breakdown(records, blocker):
    truth = true_match_pairs(records)
    cand = {tuple(sorted(p)) for p in blocker.candidate_pairs(records)}
    by = {r["id"]: r for r in records}
    rows = []
    for a, b in sorted(truth):
        rows.append({"pair": (a, b), "covered": (a, b) in cand,
                     "a": by[a]["title"], "b": by[b]["title"]})
    return rows


def main():
    insample = load("listings.json")
    holdout = load("listings_holdout.json")
    hybrid = HybridBlocker()
    minhash = MinHashLSHBlocker()

    in_rec = blocking_recall(insample, hybrid)["recall"]
    ho_hy = blocking_recall(holdout, hybrid)
    ho_mh = blocking_recall(holdout, minhash)
    run = resolve(holdout, hybrid)
    ho_prf = prf(holdout, run["clusters"])
    rows = per_pair_breakdown(holdout, hybrid)

    L = []
    L.append("# Held-out 일반화 점검\n")
    L.append("동일 파이프라인(재튜닝·사전 추가 없음)을 **사전에 없는 브랜드**(Bose/GoPro/Nespresso/"
             "Logitech/Philips/Garmin/LEGO)로 구성한 held-out 셋에 적용.\n")
    L.append("| 지표 | In-sample | Held-out |")
    L.append("|---|---:|---:|")
    L.append(f"| 하이브리드 블로킹 재현율 | {in_rec*100:.1f}% | **{ho_hy['recall']*100:.1f}%** |")
    L.append(f"| (참고) MinHash 단독 재현율 | — | {ho_mh['recall']*100:.1f}% |")
    L.append(f"| 엔드투엔드 Precision | — | {ho_prf['precision']*100:.1f}% |")
    L.append(f"| 엔드투엔드 Recall | — | {ho_prf['recall']*100:.1f}% |")
    L.append("")
    L.append("## 쌍별 커버리지 (어디서 일반화되고 어디서 깨지나)")
    L.append("| 쌍 | 커버 | A | B |")
    L.append("|---|---|---|---|")
    for r in rows:
        mark = "✅" if r["covered"] else "❌ miss"
        L.append(f"| {r['pair'][0]}–{r['pair'][1]} | {mark} | {r['a']} | {r['b']} |")
    L.append("")
    L.append("## 해석")
    L.append("- ✅ **양쪽 리스팅이 라틴 SKU/모델 코드를 공유**하면(QC45, HX9924, MX Master 3S) 결정적 "
             "모델키/lexical이 **브랜드 사전 없이도** 잡습니다 — 이 부분은 일반화됩니다.")
    L.append("- ❌ **한국어 리스팅이 브랜드/모델을 음차**하면(히어로12, 버추오, 페닉스7, 레고 75192) 공유 "
             "라틴 토큰이 없어 데모의 사전+lexical 방식이 **놓칩니다** — 운영에서 "
             "**다국어 bi-encoder(BGE-M3)+ANN**이 필요한 바로 그 케이스입니다.")
    L.append(f"- ⚠️ **두 번째 갭(같은 교훈):** held-out 엔드투엔드 P={ho_prf['precision']*100:.0f}%·"
             f"R={ho_prf['recall']*100:.0f}%로 낮습니다. 원인은 색상 사전에 **'blue/블루'가 없어** "
             "Bose QC45 **블랙(HE1)과 블루(HE2)가 오병합**된 것 — 즉 브랜드뿐 아니라 **색상·속성 사전도 "
             "in-sample에 맞춰 수작업**돼 있어 똑같이 일반화되지 않습니다. (정직성을 위해 held-out을 보고 "
             "사전을 고치지 **않았습니다.**)")
    L.append("- 결론: in-sample 100%는 튜닝 산물이고, **현실적 일반화 재현율/정밀도는 그보다 낮습니다.** "
             "이 숫자가 (a) 운영용 다국어 인코더와 (b) 학습 기반 속성 추출 투자의 정량 근거입니다.")
    report = "\n".join(L)

    out_dir = os.path.join(HERE, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "holdout_report.md"), "w", encoding="utf-8") as f:
        f.write(report)

    print("=" * 64)
    print(" HELD-OUT 일반화 점검")
    print("=" * 64)
    print(f" 하이브리드 블로킹 재현율:  in-sample {in_rec*100:.1f}%  →  held-out {ho_hy['recall']*100:.1f}%")
    print(f" held-out 커버: {ho_hy['covered']}/{ho_hy['true_pairs']} 쌍 "
          f"(MinHash 단독: {ho_mh['recall']*100:.1f}%)")
    print(f" held-out 엔드투엔드:  P={ho_prf['precision']*100:.1f}%  R={ho_prf['recall']*100:.1f}%")
    print("-" * 64)
    for r in rows:
        print(f"   {'OK  ' if r['covered'] else 'MISS'}  {r['pair'][0]}-{r['pair'][1]}  {r['b'][:34]}")
    print("=" * 64)
    print(" outputs/holdout_report.md 생성")


if __name__ == "__main__":
    main()
