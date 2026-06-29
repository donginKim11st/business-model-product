#!/usr/bin/env python3
"""
End-to-end PoC runner.

    python3 run.py

Reads data/listings.json, runs the entity-resolution cascade, and writes:
    outputs/report.md            human-readable report (paste into a doc/deck)
    outputs/report.html          styled, browser-openable version
    outputs/identity_graph.json  the resolved canonical product nodes
    outputs/metrics.json         machine-readable metrics
    outputs/blocking_comparison.csv

Set PIG_USE_CLAUDE=1 and ANTHROPIC_API_KEY to run the boundary stage on the real
claude-haiku-4-5 model instead of the offline deterministic stand-in.
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pig.blocking import MinHashLSHBlocker, HybridBlocker
from pig.resolve import resolve
from pig.evaluate import blocking_recall, prf, variant_guard, semantic_guard, cluster_stats
from pig import report


def main():
    data_path = os.path.join(HERE, "data", "listings.json")
    with open(data_path, encoding="utf-8") as f:
        records = json.load(f)["listings"]

    minhash = MinHashLSHBlocker()
    hybrid = HybridBlocker()

    # 1) blocking recall comparison
    blocking_comparison = []
    for blk, is_mh in ((minhash, True), (hybrid, False)):
        br = blocking_recall(records, blk)
        br["name"] = blk.name
        br["is_minhash"] = is_mh
        blocking_comparison.append(br)

    # 2) full resolution on the hybrid blocker (the recommended path)
    run = resolve(records, hybrid)

    # 3) evaluation
    prf_res = prf(records, run["clusters"])
    guard = variant_guard(records, run["clusters"])
    sem_guard = semantic_guard(records, run["clusters"])
    cl = cluster_stats(records, run["clusters"])

    ctx = {
        "records": records,
        "blocking_comparison": blocking_comparison,
        "run": run,
        "prf": prf_res,
        "variant_guard": guard,
        "semantic_guard": sem_guard,
        "cluster_stats": cl,
    }

    out_dir = os.path.join(HERE, "outputs")
    os.makedirs(out_dir, exist_ok=True)

    md = report.build_markdown(ctx)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(out_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(report.build_html(md, ctx))
    with open(os.path.join(out_dir, "identity_graph.json"), "w", encoding="utf-8") as f:
        json.dump(report.build_graph_json(ctx), f, ensure_ascii=False, indent=2)

    metrics = {
        "n_listings": len(records),
        "true_entities": cl["true_entities"],
        "resolved_clusters": cl["resolved_clusters"],
        "adjudicator": run["adjudicator"],
        "blocking": [
            {"name": b["name"], "recall": b["recall"],
             "candidate_pairs": b["candidate_pairs"], "missed": len(b["missed_pairs"])}
            for b in blocking_comparison
        ],
        "precision": prf_res["precision"], "recall": prf_res["recall"], "f1": prf_res["f1"],
        "false_merges": prf_res["false_merges"], "missed_merges": prf_res["missed_merges"],
        "variant_traps": guard["total_traps"], "variant_false_merges": guard["false_merges"],
        "semantic_traps": sem_guard["total_traps"], "semantic_false_merges": sem_guard["false_merges"],
        "funnel": run["funnel"],
    }
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "blocking_comparison.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["blocker", "candidate_pairs", "true_pairs", "covered", "recall"])
        for b in blocking_comparison:
            w.writerow([b["name"], b["candidate_pairs"], b["true_pairs"],
                        b["covered"], f"{b['recall']:.4f}"])

    # console summary
    print("=" * 64)
    print(" PRODUCT IDENTITY GRAPH — PoC 결과")
    print("=" * 64)
    print(f" 리스팅 {len(records)} → 해소된 진짜상품 {cl['resolved_clusters']}개 "
          f"(정답 {cl['true_entities']}개)")
    print(f" 판정 엔진: {run['adjudicator']}")
    print("-" * 64)
    print(" 블로킹 재현율:")
    for b in blocking_comparison:
        print(f"   {b['name']:48} {b['recall']*100:5.1f}%  "
              f"({b['covered']}/{b['true_pairs']} 쌍)")
    print("-" * 64)
    print(f" 엔드투엔드(*튜닝된 셋, 정확도 아님):  P={prf_res['precision']*100:.1f}%  "
          f"R={prf_res['recall']*100:.1f}%  F1={prf_res['f1']*100:.1f}%")
    print(f" Variant 가드: {guard['total_traps']}개 함정 중 오병합 "
          f"{guard['false_merges']}개 (규칙으로 결정적 분리)")
    print(f" 의미적 모호성: {sem_guard['total_traps']}개 중 오병합 "
          f"{sem_guard['false_merges']}개 (LLM 단계가 분리)")
    fn = run["funnel"]
    print(f" Cascade: 후보 {fn['candidates']} → 자동병합 {fn['auto_merge']} / "
          f"LLM {fn['boundary_llm']} / 자동기각 {fn['auto_reject']}")
    print("=" * 64)
    print(" outputs/ 에 report.md, report.html, identity_graph.json, "
          "metrics.json, blocking_comparison.csv 생성")


if __name__ == "__main__":
    main()
