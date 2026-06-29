"""
Evaluation against ground-truth entity_id labels.

Metrics:
  - Blocking recall: fraction of true-match pairs that survive blocking
    (the recall ceiling). Computed per blocker so MinHash-only vs Hybrid is
    directly comparable.
  - End-to-end pairwise precision / recall / F1 over the resolved clusters.
  - Variant guard: of the hand-built hard-negative variant pairs, how many were
    correctly kept separate (false-merge count).
  - Cluster-level: # resolved clusters vs # true entities.
"""
from itertools import combinations


def true_match_pairs(records):
    by_entity = {}
    for r in records:
        by_entity.setdefault(r["entity_id"], []).append(r["id"])
    pairs = set()
    for ids in by_entity.values():
        for a, b in combinations(sorted(ids), 2):
            pairs.add((a, b))
    return pairs


def predicted_match_pairs(clusters):
    pairs = set()
    for cl in clusters:
        for a, b in combinations(sorted(cl), 2):
            pairs.add((a, b))
    return pairs


def blocking_recall(records, blocker):
    truth = true_match_pairs(records)
    cand = blocker.candidate_pairs(records)
    cand_norm = {tuple(sorted(p)) for p in cand}
    covered = truth & cand_norm
    missed = sorted(truth - cand_norm)
    return {
        "true_pairs": len(truth),
        "candidate_pairs": len(cand_norm),
        "covered": len(covered),
        "recall": (len(covered) / len(truth)) if truth else 1.0,
        "missed_pairs": missed,
    }


def prf(records, clusters):
    truth = true_match_pairs(records)
    pred = predicted_match_pairs(clusters)
    tp = len(truth & pred)
    fp = len(pred - truth)
    fn = len(truth - pred)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "false_merges": sorted(pred - truth),
        "missed_merges": sorted(truth - pred),
    }


# Hand-labeled hard-negative variant pairs: high text similarity, different SKU.
# The pipeline MUST keep each of these separate.
VARIANT_TRAPS = [
    ("L001", "L005", "XM5 black vs silver (color)"),
    ("L001", "L007", "XM5 vs XM4 (model)"),
    ("L001", "L009", "bare vs bundle"),
    ("L017", "L020", "cola 500ml vs 1.5L (volume)"),
    ("L022", "L023", "downy 1ea vs 6-pack (pack)"),
    ("L025", "L026", "flour 1kg vs 3kg (weight)"),
    ("L010", "L014", "AirPods USB-C vs Lightning (connector)"),
    ("L027", "L030", "switch white vs neon (color)"),
    ("L037", "L038", "GTIN reuse: phone case A vs B"),
    ("L040", "L041", "anker 65W vs 45W (wattage)"),
]


def variant_guard(records, clusters):
    pred = predicted_match_pairs(clusters)
    rows = []
    false_merges = 0
    for a, b, label in VARIANT_TRAPS:
        key = tuple(sorted((a, b)))
        merged = key in pred
        if merged:
            false_merges += 1
        rows.append({"pair": [a, b], "label": label, "kept_separate": not merged})
    return {"total_traps": len(VARIANT_TRAPS), "false_merges": false_merges, "rows": rows}


# Semantic-ambiguity pairs: look identical, truly different, NO clean attribute
# separates them — these are the cases the LLM adjudication stage exists for.
SEMANTIC_TRAPS = [
    ("L045", "L001", "refurbished vs new (no attribute decides it -> LLM)"),
]


def semantic_guard(records, clusters):
    pred = predicted_match_pairs(clusters)
    rows = []
    false_merges = 0
    for a, b, label in SEMANTIC_TRAPS:
        merged = tuple(sorted((a, b))) in pred
        if merged:
            false_merges += 1
        rows.append({"pair": [a, b], "label": label, "kept_separate": not merged})
    return {"total_traps": len(SEMANTIC_TRAPS), "false_merges": false_merges, "rows": rows}


def cluster_stats(records, clusters):
    n_true = len({r["entity_id"] for r in records})
    return {"true_entities": n_true, "resolved_clusters": len(clusters)}
