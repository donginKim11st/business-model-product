"""
Seller-identity resolution — the layer Pholcent (consumer price tracker) has no
reason to build. Clusters storefronts into the SAME business so the brand can see
"these 3 shop names across 11번가/쿠팡 are one 사업자 undercutting my catalog."

Signals (in priority): exact 사업자등록번호 (biz_reg_no) match, then shop-name
similarity for storefronts missing a biz number. Production would add shared
contact/shipping, price synchronization, and behavioural fingerprints.
"""
from .normalize import char_ngrams
from .similarity import jaccard
from .resolve import UnionFind


def _storefront_key(rec):
    return (rec["marketplace"], rec.get("seller_name", "?"))


def resolve_sellers(records, name_sim_threshold=0.6):
    storefronts = {}
    for r in records:
        k = _storefront_key(r)
        sf = storefronts.setdefault(k, {
            "marketplace": r["marketplace"], "name": r.get("seller_name", "?"),
            "biz": (r.get("biz_reg_no") or "").strip(), "listings": [],
            "is_official": bool(r.get("is_official")),
        })
        sf["listings"].append(r["id"])

    keys = list(storefronts)
    uf = UnionFind(keys)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = storefronts[keys[i]], storefronts[keys[j]]
            same = False
            if a["biz"] and b["biz"]:
                same = a["biz"] == b["biz"]
            else:  # fall back to shop-name similarity when a biz number is missing
                same = jaccard(char_ngrams(a["name"], 2), char_ngrams(b["name"], 2)) >= name_sim_threshold
            if same:
                uf.union(keys[i], keys[j])

    groups = {}
    for k in keys:
        groups.setdefault(uf.find(k), []).append(k)

    clusters = []
    for members in groups.values():
        sfs = [storefronts[k] for k in members]
        names = sorted({s["name"] for s in sfs})
        markets = sorted({s["marketplace"] for s in sfs})
        listing_ids = [lid for s in sfs for lid in s["listings"]]
        biz = next((s["biz"] for s in sfs if s["biz"]), "")
        clusters.append({
            "biz_reg_no": biz,
            "storefront_names": names,
            "storefront_count": len(members),
            "marketplaces": markets,
            "listing_ids": listing_ids,
            "listing_count": len(listing_ids),
            "is_official": any(s["is_official"] for s in sfs),
            "multi_storefront": len(names) > 1,
        })
    clusters.sort(key=lambda c: (-c["storefront_count"], -c["listing_count"]))
    return clusters
