#!/usr/bin/env python3
"""Stage1: 스포츠 정형 CSV(all_brands.csv) 행별 카탈로그명 분해/정규화.

  python3 catalog_decompose.py [--in PATH] [--out PATH] [--limit N] [--llm-gate] [--llm-limit N]
"""
import os
import re
import csv
import sys
import argparse
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalog_lexicon as lex

IN_DEFAULT = os.path.join(HERE, "outputs", "all_brands.csv")
OUT_DEFAULT = os.path.join(HERE, "outputs", "catalog_decomposed.csv")

OUT_COLS = ["source", "brand_norm", "style_code", "catalog_name", "product_line",
            "product_type", "gender_norm", "colorway", "price", "sizes", "url",
            "name", "needs_llm"]

_JUNK = re.compile(r"★[^★]*★|\[[^\]]*\]")
_WS = re.compile(r"\s+")
_HANGUL = re.compile(r"[가-힣]{2,}")
# 트레일링 다단어 영문(2단어 이상) — 한/영 중복 의심 신호
_ASCII_TAIL = re.compile(r"(?:[A-Za-z][A-Za-z0-9']*\s+)+[A-Za-z][A-Za-z0-9']*\s*$")


def _norm(s):
    return _WS.sub(" ", unicodedata.normalize("NFKC", s or "")).strip()


def brand_aliases(source):
    al = set(a.lower() for a in lex.BRAND_ALIASES.get(source, []))
    al.add((source or "").lower())
    ko = lex.BRAND_KO.get(source)
    if ko:
        al.add(ko.lower())
    return {a for a in al if a}


def norm_gender(raw, name):
    key = (raw or "").strip().lower()
    if key in lex.GENDER_MAP:
        return lex.GENDER_MAP[key]
    low = (name or "").lower()
    for tok in lex.GENDER_NAME_TOKENS:
        if re.search(r"\b" + re.escape(tok.lower()) + r"\b", low):
            return lex.GENDER_MAP.get(tok.lower())
    if re.search(r"[（(]남[)）]", name or ""):
        return "M"
    if re.search(r"[（(]여[)）]", name or ""):
        return "W"
    return None


def find_product_type(category, name):
    hay = "%s %s" % (name or "", category or "")
    for t in lex.PRODUCT_TYPES:  # 긴 것 우선(lexicon 정렬 보장)
        if t in hay:
            return t
    return None


def _strip_tokens(text, tokens):
    out = text
    for tok in tokens:
        if not tok:
            continue
        out = re.sub(r"\b" + re.escape(tok) + r"\b", " ", out, flags=re.IGNORECASE)
    return _WS.sub(" ", out).strip()


def clean_product_line(name, source, color):
    line = _JUNK.sub(" ", name or "")
    line = _norm(line)
    line = re.sub(r"[（(][남여][)）]", " ", line)
    line = _strip_tokens(line, lex.GENDER_NAME_TOKENS)
    color_toks = [c for c in re.split(r"[,\|/\s]+", color or "") if c] + lex.COLOR_TOKENS
    line = _strip_tokens(line, color_toks)
    line = _strip_tokens(line, sorted(brand_aliases(source), key=len, reverse=True))
    return _WS.sub(" ", line).strip()


def compute_needs_llm(product_line):
    if not product_line or len(product_line) <= 1:
        return True
    if _HANGUL.search(product_line) and _ASCII_TAIL.search(product_line):
        return True
    return False


def decompose_row(row):
    source = (row.get("source") or "").strip()
    name = row.get("name") or ""
    brand_norm = lex.BRAND_KO.get(source) or (row.get("brand") or source or "").strip()
    gender_norm = norm_gender(row.get("gender"), name)
    product_type = find_product_type(row.get("category"), name)
    product_line = clean_product_line(name, source, row.get("color"))
    catalog_name = _WS.sub(" ", ("%s %s" % (brand_norm, product_line))).strip()
    return {
        "source": source,
        "brand_norm": brand_norm,
        "style_code": (row.get("style_code") or "").strip(),
        "catalog_name": catalog_name,
        "product_line": product_line,
        "product_type": product_type or "",
        "gender_norm": gender_norm or "",
        "colorway": _norm(row.get("color")),
        "price": (row.get("price") or "").strip(),
        "sizes": (row.get("sizes") or "").strip(),
        "url": (row.get("url") or "").strip(),
        "name": _norm(name),
        "needs_llm": "1" if compute_needs_llm(product_line) else "0",
    }


def run_stage1(in_path=IN_DEFAULT, out_path=OUT_DEFAULT, limit=0, llm_gate=False, llm_limit=0):
    if not os.path.exists(in_path):
        sys.exit("✗ 입력 없음: %s — 먼저 extract_all.py 로 all_brands.csv 를 만드세요." % in_path)
    rows = list(csv.DictReader(open(in_path, encoding="utf-8-sig")))
    if limit:
        rows = rows[:limit]
    out, n_empty, n_llm = [], 0, 0
    for r in rows:
        if not (r.get("name") or "").strip():
            n_empty += 1
            continue
        d = decompose_row(r)
        if d["needs_llm"] == "1":
            n_llm += 1
        out.append(d)
    if llm_gate:
        import catalog_llm_gate as gate
        n_gated = gate.apply_stage1(out, limit=llm_limit)
        print("  [LLM] 게이트 보정 %d행 (모델 %s)" % (n_gated, gate.MODEL))
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS)
        w.writeheader()
        for d in out:
            w.writerow(d)
    print("[Stage1] %d행 → %s (빈name skip %d · needs_llm %d)" % (len(out), out_path, n_empty, n_llm))
    return {"rows": len(out), "needs_llm": n_llm, "empty": n_empty}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=IN_DEFAULT)
    ap.add_argument("--out", dest="out_path", default=OUT_DEFAULT)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--llm-gate", action="store_true")
    ap.add_argument("--llm-limit", type=int, default=0)
    args = ap.parse_args()
    run_stage1(args.in_path, args.out_path, args.limit, args.llm_gate, args.llm_limit)


if __name__ == "__main__":
    main()
