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

IN_DEFAULT = os.path.join(HERE, "outputs", "catalog_decomposed.csv")
OUT_DEFAULT = os.path.join(HERE, "outputs", "catalogs.csv")

GROUP_COLS = ["source", "brand_norm", "model_key", "catalog_name", "product_type",
              "gender", "colorways", "n_colorways", "style_codes", "price_min",
              "price_max", "size_range", "n_variants", "sample_url"]

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
    parts = [d.get("source", ""), d.get("product_line", ""),
             d.get("product_type", ""), d.get("gender_norm", "")]
    return "name:" + _WS.sub("", " ".join(parts)).lower()


def model_key(d):
    b = base_style_code(d.get("source", ""), d.get("style_code", ""))
    if b:
        return "sc:%s:%s" % (d.get("source", ""), b)
    return name_key(d)


def _isnum(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _sizes_of(members):
    vals = []
    for m in members:
        for s in (m.get("sizes") or "").split("|"):
            s = s.strip()
            if s:
                vals.append(s)
    # 숫자 사이즈는 수치 정렬, 아니면 문자 정렬
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
        names = [m.get("catalog_name", "") for m in members if m.get("catalog_name")]
        rep = collections.Counter(names).most_common(1)[0][0] if names else ""
        prices = [float(m["price"]) for m in members if _isnum(m.get("price"))]
        colors = sorted({m.get("colorway", "") for m in members if m.get("colorway")})
        genders = sorted({m.get("gender_norm", "") for m in members if m.get("gender_norm")})
        codes = {m.get("style_code", "") for m in members if m.get("style_code")}
        sizes = _sizes_of(members)
        cats.append({
            "source": members[0].get("source", ""),
            "brand_norm": members[0].get("brand_norm", ""),
            "model_key": key,
            "catalog_name": rep,
            "product_type": members[0].get("product_type", ""),
            "gender": "|".join(genders),
            "colorways": "|".join(colors),
            "n_colorways": str(len(colors)),
            "style_codes": str(len(codes)),
            "price_min": str(int(min(prices))) if prices else "",
            "price_max": str(int(max(prices))) if prices else "",
            "size_range": ("%s~%s" % (sizes[0], sizes[-1])) if sizes else "",
            "n_variants": str(len(members)),
            "sample_url": members[0].get("url", ""),
        })
    return cats
