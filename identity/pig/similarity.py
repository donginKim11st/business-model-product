"""
Pairwise field similarity + attribute compatibility guard.

Returns a score in [0,1] AND a list of hard conflicts. The conflict list is the
"variant granularity" fix: when two listings differ on a discriminating
attribute (model / color / connector / volume / weight / pack-count / wattage /
bundle), they are the SAME family but DIFFERENT SKUs and must not be merged on
similarity alone — pack-size is arithmetic, not cosine distance.
"""
from .normalize import extract_attributes


def jaccard(a, b):
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _num_conflict(x, y):
    """True if both present and meaningfully different."""
    if x is None or y is None:
        return False
    return abs(x - y) > 1e-6


def attribute_conflicts(ax, ay):
    """List of (attribute, value_x, value_y) hard conflicts."""
    conflicts = []

    # model: conflict only if both have models AND none match under the same
    # prefix-aware rule used for positive matching (avoids a split/join mismatch
    # like "16Z90S-GA56K" vs "16Z90S GA56K").
    if ax["models"] and ay["models"]:
        if not models_match(ax["models"], ay["models"]):
            conflicts.append(("model", ax["model"], ay["model"]))

    for attr in ("color", "connector", "category", "size_token"):
        x, y = ax.get(attr), ay.get(attr)
        if x is not None and y is not None and x != y:
            conflicts.append((attr, x, y))

    # product-line mismatch: both have lines and they share none -> different SKU
    lx, ly = set(ax.get("product_lines") or []), set(ay.get("product_lines") or [])
    if lx and ly and not (lx & ly):
        conflicts.append(("product_line", sorted(lx), sorted(ly)))

    for attr in ("volume_ml", "weight_g", "wattage", "pack_count"):
        if _num_conflict(ax[attr], ay[attr]):
            conflicts.append((attr, ax[attr], ay[attr]))

    if ax["is_bundle"] != ay["is_bundle"]:
        conflicts.append(("bundle", ax["is_bundle"], ay["is_bundle"]))

    return conflicts


def brand_compatible(ax, ay):
    if ax["brand"] and ay["brand"]:
        return ax["brand"] == ay["brand"]
    return None  # unknown


def models_match(models_x, models_y, min_prefix=4):
    """Exact match on any code, or prefix match (handles MTJV3 vs MTJV3AM/A)."""
    for mx in models_x:
        for my in models_y:
            if mx == my:
                return True
            short, long = (mx, my) if len(mx) <= len(my) else (my, mx)
            if len(short) >= min_prefix and long.startswith(short):
                return True
    return False


def _attr_agreement(ax, ay):
    n = 0
    for attr in ("volume_ml", "weight_g", "wattage", "pack_count"):
        if ax[attr] is not None and ax[attr] == ay[attr]:
            n += 1
    if ax["color"] and ax["color"] == ay["color"]:
        n += 1
    if ax["connector"] and ax["connector"] == ay["connector"]:
        n += 1
    return n


def score_pair(rec_x, rec_y, ax=None, ay=None):
    ax = ax or extract_attributes(rec_x)
    ay = ay or extract_attributes(rec_y)

    conflicts = attribute_conflicts(ax, ay)
    brand_ok = brand_compatible(ax, ay)
    tok_sim = jaccard(ax["token_set"], ay["token_set"])
    model_match = models_match(ax["models"], ay["models"])

    gtin_x = (rec_x.get("gtin") or "").strip()
    gtin_y = (rec_y.get("gtin") or "").strip()
    gtin_match = bool(gtin_x and gtin_y and gtin_x == gtin_y)

    # GTIN is corroborating evidence, NOT a merger by itself: a shared GTIN only
    # counts as strong when something else agrees (brand / model / lexical),
    # otherwise it is treated as a possible reuse (weak). Handles L037/L038.
    gtin_strong = gtin_match and (brand_ok is True or model_match or tok_sim > 0.30)

    # Structured-evidence-driven score. Cross-marketplace titles share few tokens,
    # so brand + model + attribute agreement carry the signal, not lexical overlap.
    score = 0.0
    if gtin_strong:
        score += 0.55
    elif gtin_match:
        score += 0.10
    if model_match:
        score += 0.45
    if brand_ok is True:
        score += 0.25
    score += 0.08 * _attr_agreement(ax, ay)
    score += 0.35 * tok_sim
    score = max(0.0, min(1.0, score))

    # Hard conflicts cap the score so variants/bundles/GTIN-reuse fall out of the
    # merge band — GTIN match does NOT override a hard conflict.
    if conflicts:
        score = min(score, 0.30)
    if brand_ok is False:
        score = min(score, 0.30)

    # Condition mismatch (refurbished/used vs new) is a SOFT signal: it pulls an
    # otherwise-high score down INTO the boundary band so the LLM adjudicates it,
    # rather than auto-merging or auto-rejecting with a rule.
    condition_mismatch = (ax["condition"] or "new") != (ay["condition"] or "new")
    if condition_mismatch and not conflicts:
        score = min(score, 0.66)

    return {
        "score": round(score, 4),
        "tok_sim": round(tok_sim, 4),
        "brand_ok": brand_ok,
        "model_match": model_match,
        "gtin_match": gtin_match,
        "gtin_strong": bool(gtin_strong),
        "condition_mismatch": bool(condition_mismatch),
        "conflicts": conflicts,
    }
