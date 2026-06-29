#!/usr/bin/env python3
"""아디다스 CSV 후처리: color에서 선두 gender/division 토큰 제거, sizes에서 'hidden' 제거+정렬."""
import csv
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "outputs", "extract_brand_adidas.csv")
COLS = ["source", "brand", "style_code", "name", "color", "price", "currency",
        "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]

# 선두에서만 제거(첫 실제 컬러 토큰에서 멈춤). 'Cloud White' 등 컬러 첫토큰은 보존.
NOISE = {w.lower() for w in [
    "men", "women", "kids", "kid", "키즈", "우먼", "맨", "unisex", "boys", "girls",
    "boy", "girl", "infants", "infant", "youth", "junior", "baby",
    "남성", "여성", "남아", "여아", "유아", "아동", "남", "여", "공용",
    "originals", "original", "sportswear", "performance", "lifestyle",
    "running", "training", "outdoor", "football", "soccer", "basketball",
    "golf", "tennis", "swim", "swimming", "cycling", "essentials", "terrex",
    "adicolor", "sport", "gym", "&", "y_3", "y-3", "motorsport", "hiking",
    "skateboarding", "trail", "adidas_by_stella_mccartney", "yoga", "baseball",
    "walking", "boxing", "track", "weightlifting", "volleyball", "winter",
    "rugby", "cricket", "handball", "hockey", "dance", "studio",
]}


def clean_color(c):
    toks = c.split()
    while toks and toks[0].lower() in NOISE:
        toks.pop(0)
    return " ".join(toks).strip()


def clean_sizes(s):
    parts = [p.strip() for p in s.split("|") if p.strip() and p.strip().lower() != "hidden"]
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    num = [p for p in out if re.fullmatch(r"\d+(\.\d+)?", p)]
    if len(num) == len(out):
        out = sorted(out, key=lambda x: float(x))
    else:
        out = sorted(out)
    return "|".join(out)


def main():
    rows = list(csv.DictReader(open(PATH, encoding="utf-8-sig")))
    for r in rows:
        r["color"] = clean_color(r.get("color", ""))
        r["sizes"] = clean_sizes(r.get("sizes", ""))
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, PATH)
    print(f"cleaned {len(rows)} rows")
    for r in rows[:2] + rows[-2:]:
        print("  ", r["style_code"], "|", r["color"], "|", r["sizes"][:40])


if __name__ == "__main__":
    main()
