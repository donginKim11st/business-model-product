"""
Blocking strategies. Blocking is the irreversible recall ceiling: a candidate
pair that blocking never emits can never be merged by any downstream stage.

Three blockers are provided so the demo can compare them honestly:
  1. MinHashLSH        - the originally proposed approach (token Jaccard)
  2. DeterministicKey  - brand + model-code + normalized identifier keys
  3. CharNGramLSH      - char 3-gram MinHash over brand-normalized text
The Hybrid blocker is the union of 2 + 3 + 1, which is the recommended fix.

Pure-Python MinHash (no numpy / datasketch dependency).
"""
import hashlib
from itertools import combinations

from .normalize import tokenize, char_ngrams, extract_attributes, BRAND_LEXICON

_MERSENNE = (1 << 61) - 1  # large prime for universal hashing


def _stable_hash(s):
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def _hash_params(num_perm, seed=0):
    # deterministic (a, b) pairs for h(x) = (a*x + b) mod p
    params = []
    for i in range(num_perm):
        a = _stable_hash(f"a{seed}:{i}") % _MERSENNE or 1
        b = _stable_hash(f"b{seed}:{i}") % _MERSENNE
        params.append((a, b))
    return params


class _MinHasher:
    def __init__(self, num_perm=128, seed=0):
        self.num_perm = num_perm
        self.params = _hash_params(num_perm, seed)

    def signature(self, shingles):
        if not shingles:
            return tuple([0] * self.num_perm)
        hashed = [_stable_hash(sh) % _MERSENNE for sh in shingles]
        sig = []
        for a, b in self.params:
            sig.append(min(((a * h + b) % _MERSENNE) for h in hashed))
        return tuple(sig)


class _LSHIndex:
    """Band the signature; two records collide if any band matches."""

    def __init__(self, bands, rows):
        self.bands = bands
        self.rows = rows
        self.buckets = [dict() for _ in range(bands)]

    def add(self, key, signature):
        for bi in range(self.bands):
            band = tuple(signature[bi * self.rows:(bi + 1) * self.rows])
            self.buckets[bi].setdefault(band, []).append(key)

    def candidate_pairs(self):
        pairs = set()
        for bucket in self.buckets:
            for keys in bucket.values():
                if len(keys) > 1:
                    for a, b in combinations(sorted(keys), 2):
                        pairs.add((a, b))
        return pairs

    @staticmethod
    def threshold(bands, rows):
        return (1.0 / bands) ** (1.0 / rows)


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------
class MinHashLSHBlocker:
    """The originally proposed blocker: MinHash LSH over word-token shingles."""

    name = "MinHash LSH (proposed)"

    def __init__(self, num_perm=128, bands=32, rows=4):
        assert bands * rows == num_perm
        self.hasher = _MinHasher(num_perm, seed=1)
        self.bands, self.rows = bands, rows

    def shingles(self, rec):
        return set(tokenize(rec["title"]))

    def candidate_pairs(self, records):
        idx = _LSHIndex(self.bands, self.rows)
        for rec in records:
            idx.add(rec["id"], self.hasher.signature(self.shingles(rec)))
        return idx.candidate_pairs()

    @property
    def lsh_threshold(self):
        return _LSHIndex.threshold(self.bands, self.rows)


class CharNGramLSHBlocker:
    """Char 3-gram MinHash over brand-normalized text. Robust to KR spacing/typos."""

    name = "CharNGram LSH"

    def __init__(self, n=3, num_perm=128, bands=40, rows=None):
        # bands*rows must equal num_perm; pick rows to get a permissive threshold
        rows = num_perm // bands
        self.n = n
        self.hasher = _MinHasher(num_perm, seed=2)
        self.bands, self.rows = bands, rows

    def shingles(self, rec):
        attrs = extract_attributes(rec)
        # prepend canonical brand so brand-normalized text overlaps cross-lingually
        prefix = (attrs["brand"] or "") + " "
        return char_ngrams(prefix + rec["title"], self.n)

    def candidate_pairs(self, records):
        idx = _LSHIndex(self.bands, self.rows)
        for rec in records:
            idx.add(rec["id"], self.hasher.signature(self.shingles(rec)))
        return idx.candidate_pairs()


class DeterministicKeyBlocker:
    """Block on exact keys: brand+model, brand+core-attributes, and GTIN.

    GTIN is a *candidate-generating* key, never an auto-merge: a reused GTIN
    (L037/L038) becomes a candidate pair that the scorer/LLM must still reject.
    """

    name = "Deterministic key (brand+model / GTIN)"

    def candidate_pairs(self, records):
        keymap = {}

        def add(key, rid):
            keymap.setdefault(key, []).append(rid)

        for rec in records:
            attrs = extract_attributes(rec)
            brand = attrs["brand"] or "?"
            # brand + each model code, plus a 5-char model prefix so region/suffix
            # variants collide (MTJV3 vs MTJV3AM/A); model prefix is brand-agnostic
            # so a missing-brand listing still blocks on its model.
            for m in attrs["models"]:
                add(("bm", brand, m), rec["id"])
                if len(m) >= 5:
                    add(("mp", m[:5]), rec["id"])
                else:
                    add(("mp", m), rec["id"])
            # brand + numeric attribute fingerprint (catches model-less cross-lingual)
            fp = (
                brand,
                attrs["volume_ml"],
                attrs["weight_g"],
                attrs["wattage"],
                attrs["color"],
                attrs["connector"],
            )
            if brand != "?":
                add(("ba", fp), rec["id"])
            # brand + product-line + category + size: groups the SAME product across
            # noisy seller titles (recall). Precision is protected by the conflict
            # guards (line/size/category) at scoring/cluster time.
            if brand != "?" and attrs["category"] and attrs["size_token"]:
                add(("lcs", brand, tuple(attrs["product_lines"]),
                     attrs["category"], attrs["size_token"]), rec["id"])
            # GTIN
            g = (rec.get("gtin") or "").strip()
            if g:
                add(("gtin", g), rec["id"])

        pairs = set()
        for keys in keymap.values():
            if len(keys) > 1:
                for a, b in combinations(sorted(keys), 2):
                    pairs.add((a, b))
        return pairs


class HybridBlocker:
    """Recommended fix: union of deterministic + char-ngram + minhash legs.
    Sparse and dense/lexical legs are empirically complementary."""

    name = "Hybrid (deterministic + charngram + minhash)"

    def __init__(self):
        self.legs = [DeterministicKeyBlocker(), CharNGramLSHBlocker(), MinHashLSHBlocker()]

    def candidate_pairs(self, records):
        pairs = set()
        for leg in self.legs:
            pairs |= leg.candidate_pairs(records)
        return pairs
