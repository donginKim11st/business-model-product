#!/usr/bin/env python3
"""Stage2: catalog_decomposed.csv → 모델 단위 카탈로그 묶음(catalogs.csv).

  python3 catalog_group.py [--in PATH] [--out PATH] [--llm-gate] [--llm-limit N]
"""
import os
import re
import csv
import sys
import argparse
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalog_lexicon as lex
import catalog_decompose as cd

IN_DEFAULT = os.path.join(HERE, "outputs", "catalog_decomposed.csv")
OUT_DEFAULT = os.path.join(HERE, "outputs", "catalogs.csv")

GROUP_COLS = ["source", "brand_norm", "model_key", "catalog_name", "product_name",
              "gender", "product_type", "colors", "n_colors", "size_range",
              "materials", "origins", "style_codes", "price_min", "price_max",
              "n_variants", "sample_url"]

_WS = re.compile(r"\s+")


def base_style_code(source, style_code):
    sc = (style_code or "").strip()
    rule = lex.STYLECODE_SUFFIX.get(source)
    if not sc or not rule:
        return None
    if "sep" in rule and rule["sep"] in sc:
        head = sc.rsplit(rule["sep"], 1)[0]
        return head or None
    if "tail_alpha" in rule:
        n = rule["tail_alpha"]
        if len(sc) > n and sc[-n:].isalpha():
            return sc[:-n]
    if "tail_digit" in rule:
        n = rule["tail_digit"]
        if len(sc) > n and sc[-n:].isdigit():
            return sc[:-n]
    if "tail" in rule:
        n = rule["tail"]
        if len(sc) > n:
            return sc[:-n]
    return None


def name_key(d):
    parts = [d.get("source", ""), d.get("product_name", ""),
             d.get("product_type", ""), d.get("gender_code", "")]
    return "name:" + _WS.sub("", " ".join(parts)).lower()


def model_key(d):
    b = base_style_code(d.get("source", ""), d.get("style_code", ""))
    if b:
        return "sc:%s:%s" % (d.get("source", ""), b)
    return name_key(d)


def _modal(values):
    vals = [v for v in values if v]
    if not vals:
        return ""
    return collections.Counter(vals).most_common(1)[0][0]


def _isnum(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _sizes_of(members):
    vals = []
    for m in members:
        for s in (m.get("size") or "").split("|"):
            s = s.strip()
            if s:
                vals.append(s)
    try:
        uniq = sorted(set(vals), key=lambda x: float(x))
    except ValueError:
        uniq = sorted(set(vals))
    return uniq


def group(drows):
    buckets = collections.OrderedDict()
    for d in drows:
        buckets.setdefault(model_key(d), []).append(d)
    cats = []
    for key, members in buckets.items():
        product_name = _modal([m.get("product_name", "") for m in members])
        gender = _modal([m.get("gender", "") for m in members])
        product_type = _modal([m.get("product_type", "") for m in members])
        attrs = cd.name_attrs(gender, product_type, "", include_color=False)
        catalog_name = cd.compose_catalog_name(members[0].get("brand_norm", ""), product_name, attrs)
        prices = [float(m["price"]) for m in members if _isnum(m.get("price"))]
        colors = sorted({m.get("color", "") for m in members if m.get("color")})
        materials = sorted({m.get("material", "") for m in members if m.get("material")})
        origins = sorted({m.get("origin", "") for m in members if m.get("origin")})
        codes = {m.get("style_code", "") for m in members if m.get("style_code")}
        sizes = _sizes_of(members)
        cats.append({
            "source": members[0].get("source", ""),
            "brand_norm": members[0].get("brand_norm", ""),
            "model_key": key,
            "catalog_name": catalog_name,
            "product_name": product_name,
            "gender": gender,
            "product_type": product_type,
            "colors": "|".join(colors),
            "n_colors": str(len(colors)),
            "size_range": ("%s~%s" % (sizes[0], sizes[-1])) if sizes else "",
            "materials": "|".join(materials),
            "origins": "|".join(origins),
            "style_codes": str(len(codes)),
            "price_min": str(int(min(prices))) if prices else "",
            "price_max": str(int(max(prices))) if prices else "",
            "n_variants": str(len(members)),
            "sample_url": members[0].get("url", ""),
        })
    return cats


def run_stage2(in_path=IN_DEFAULT, out_path=OUT_DEFAULT, llm_gate=False, llm_limit=0):
    if not os.path.exists(in_path):
        sys.exit("✗ 입력 없음: %s — 먼저 catalog_decompose.py 를 실행하세요." % in_path)
    drows = list(csv.DictReader(open(in_path, encoding="utf-8-sig")))
    cats = group(drows)
    if llm_gate:
        import catalog_llm_gate as gate
        n = gate.apply_stage2(cats, drows, limit=llm_limit)
        print("  [LLM] 그룹 보정 %d건 (모델 %s)" % (n, gate.MODEL))
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GROUP_COLS)
        w.writeheader()
        for c in cats:
            w.writerow(c)
    print("[Stage2] %d행 → %d 카탈로그 → %s" % (len(drows), len(cats), out_path))
    return {"rows": len(drows), "catalogs": len(cats)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=IN_DEFAULT)
    ap.add_argument("--out", dest="out_path", default=OUT_DEFAULT)
    ap.add_argument("--llm-gate", action="store_true")
    ap.add_argument("--llm-limit", type=int, default=0)
    args = ap.parse_args()
    run_stage2(args.in_path, args.out_path, args.llm_gate, args.llm_limit)


if __name__ == "__main__":
    main()
