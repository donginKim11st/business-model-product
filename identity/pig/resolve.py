"""
Pipeline orchestration:
  block -> score -> route (auto-merge / boundary->LLM / auto-reject) -> cluster.

Only positive (merge) decisions create union-find edges. The resolved clusters
are the canonical "true product" nodes of the identity graph.
"""
from .similarity import score_pair, attribute_conflicts
from .normalize import extract_attributes
from .adjudicate import in_boundary, get_adjudicator, DEFAULT_LOW, DEFAULT_HIGH


class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb

    def clusters(self):
        groups = {}
        for i in self.parent:
            groups.setdefault(self.find(i), []).append(i)
        return list(groups.values())


def resolve(records, blocker, adjudicator=None, low=DEFAULT_LOW, high=DEFAULT_HIGH,
            cluster_guard=False):
    """cluster_guard=True: two clusters are only joined if NO member pair across
    them hard-conflicts — blocks transitive 'blob' merges where A-B and B-C are
    each plausible but A and C are different products (size/category/etc.)."""
    adjudicator = adjudicator or get_adjudicator()
    by_id = {r["id"]: r for r in records}
    attrs = {r["id"]: extract_attributes(r) for r in records}

    candidate_pairs = sorted(blocker.candidate_pairs(records))

    uf = UnionFind(by_id.keys())
    members = {i: [i] for i in by_id}  # only used when cluster_guard
    merge_pairs = []
    decisions = []
    funnel = {"candidates": len(candidate_pairs), "auto_merge": 0,
              "boundary_llm": 0, "auto_reject": 0, "llm_same": 0, "llm_different": 0}

    def do_merge(a, b, score):
        if cluster_guard:
            merge_pairs.append((score, a, b))  # defer to guarded pass
        else:
            uf.union(a, b)

    for a, b in candidate_pairs:
        sc = score_pair(by_id[a], by_id[b], attrs[a], attrs[b])
        s = sc["score"]
        record = {"pair": [a, b], "score": s, "stage": None, "decision": None,
                  "conflicts": sc["conflicts"], "reason": None,
                  "model_match": sc["model_match"], "gtin_match": sc["gtin_match"]}

        if in_boundary(s, low, high):
            verdict = adjudicator.adjudicate(by_id[a], by_id[b])
            record["stage"] = "llm"
            record["decision"] = verdict["decision"]
            record["reason"] = verdict["reason"]
            record["llm_confidence"] = verdict.get("confidence")
            funnel["boundary_llm"] += 1
            if verdict["decision"] == "same":
                do_merge(a, b, s)
                funnel["llm_same"] += 1
            else:
                funnel["llm_different"] += 1
        elif s > high:
            record["stage"] = "auto_merge"
            record["decision"] = "same"
            record["reason"] = "score above merge threshold"
            do_merge(a, b, s)
            funnel["auto_merge"] += 1
        else:
            record["stage"] = "auto_reject"
            record["decision"] = "different"
            record["reason"] = (f"hard conflict: {sc['conflicts'][0][0]}"
                                if sc["conflicts"] else "score below merge threshold")
            funnel["auto_reject"] += 1

        decisions.append(record)

    if cluster_guard:
        # join strongest merges first; block a union if any cross-cluster member
        # pair hard-conflicts (prevents transitive blobs).
        funnel["guard_blocked"] = 0
        for score, a, b in sorted(merge_pairs, key=lambda x: -x[0]):
            ra, rb = uf.find(a), uf.find(b)
            if ra == rb:
                continue
            conflict = any(attribute_conflicts(attrs[x], attrs[y])
                           for x in members[ra] for y in members[rb])
            if conflict:
                funnel["guard_blocked"] += 1
                continue
            uf.union(a, b)
            keep, drop = (ra, rb) if uf.find(a) == ra else (rb, ra)
            members[keep] += members[drop]
            members.pop(drop, None)

    clusters = uf.clusters()
    return {
        "blocker": blocker.name,
        "adjudicator": adjudicator.backend,
        "band": [low, high],
        "candidate_pairs": candidate_pairs,
        "decisions": decisions,
        "funnel": funnel,
        "clusters": clusters,
        "attrs": attrs,
    }
