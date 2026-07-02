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
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalog_lexicon as lex

IN_DEFAULT = os.path.join(HERE, "outputs", "all_brands.csv")
OUT_DEFAULT = os.path.join(HERE, "outputs", "catalog_decomposed.csv")

OUT_COLS = ["source", "brand_norm", "style_code", "title_geo", "title_commerce",
            "product_name", "gender", "product_type", "color", "size", "material",
            "origin", "gender_code", "price", "url", "name", "needs_llm"]

CANON_PATH = os.path.join(HERE, "outputs", "_catalog_canonical.json")
_CANON = None

_JUNK = re.compile(r"★[^★]*★|\[[^\]]*\]")
_ENUM_STOCK = re.compile(r"(?:(?<=\s)|^)\d{1,2}(?:-\d{1,2})?[.)]\s+|일시품절|재고소진|입고예정|품절|[�]+")
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
    low = hay.lower()
    for alias, canon in lex.TYPE_ALIASES.items():   # 영문/변형 표기 → 정규 한글 유형
        if alias.isascii():
            if re.search(r"\b" + re.escape(alias) + r"\b", low):
                return canon
        elif alias in hay:
            return canon
    return None


def _strip_tokens(text, tokens):
    out = text
    for tok in tokens:
        if not tok:
            continue
        out = re.sub(r"\b" + re.escape(tok) + r"\b", " ", out, flags=re.IGNORECASE)
    return _WS.sub(" ", out).strip()


def primary_color(color):
    """색상 컬럼값 → 이름용 대표색 1개. 콤마/슬래시/파이프/하이픈 첫 세그먼트,
    세그먼트에 한글 색상어가 있으면 한글 토큰만(브랜드/영문 수식어 'PUMA'·'Poison' 제거)."""
    if not color:
        return ""
    seg = re.split(r"[,/|\-]", color)[0].strip()
    ko = [t for t in seg.split() if re.search(r"[가-힣]", t)]
    return " ".join(ko) if ko else seg


def strip_type(product_line, product_type):
    """상품명 = product_line 에서 유형 명사 제거(핵심 모델명). 유형 없으면 그대로."""
    if not product_type:
        return product_line
    return _strip_tokens(product_line, [product_type])


_APPAREL_ORDER = {s: i for i, s in enumerate(
    ["XXXS", "XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "XXXXL",
     "FREE", "F", "OS", "ONESIZE"])}


def _size_key(tok):
    u = tok.upper()
    return (0, _APPAREL_ORDER[u]) if u in _APPAREL_ORDER else (1, u)


def size_range_label(tokens):
    """사이즈 토큰들 → 숫자/문자 분리 라벨(숫자=수치범위, 문자=순서범위, 빈값·중복 제외).

    숫자·문자가 섞여도 한 범위로 뭉치지 않는다(예: 230·250·XL → '230~250 XL')."""
    toks = [t.strip() for t in tokens if t and t.strip()]
    nums, alphas = [], []
    for t in toks:
        try:
            float(t)
            nums.append(t)
        except ValueError:
            alphas.append(t)
    parts = []
    if nums:
        ns = sorted(set(nums), key=float)
        parts.append(ns[0] if len(ns) == 1 else "%s~%s" % (ns[0], ns[-1]))
    if alphas:
        aa = sorted(set(alphas), key=_size_key)
        parts.append(aa[0] if len(aa) == 1 else "%s~%s" % (aa[0], aa[-1]))
    return " ".join(parts)


def size_label(size_field):
    """사이즈 컬럼값(파이프 구분) → 이름용 범위 라벨(숫자/문자 분리)."""
    return size_range_label((size_field or "").split("|"))


def color_ko(color):
    """색상 문자열의 영문 색 단어를 한글로 치환(구분자·미지 단어 보존)."""
    if not color:
        return ""
    return re.sub(r"[A-Za-z]+",
                  lambda m: lex.COLOR_KO.get(m.group(0).lower(), m.group(0)),
                  color)


def name_attrs(gender_label, product_type, color, size="", cap=5):
    """카탈로그명에 붙일 속성(우선순위 성별→유형→색상→사이즈, 빈값 제외, 최대 cap)."""
    seq = [gender_label, product_type, color, size]
    return [a for a in seq if a][:cap]


def compose_catalog_name(brand_norm, product_name, attrs):
    """브랜드 + 상품명 + 속성들 → 정규 카탈로그명."""
    parts = [brand_norm, product_name] + list(attrs)
    return _WS.sub(" ", " ".join(p for p in parts if p)).strip()


def _canon_store():
    """title_geo canonical 캐시(catalog_geo 배치가 채움). 없으면 빈 dict."""
    global _CANON
    if _CANON is None:
        try:
            _CANON = json.load(open(CANON_PATH, encoding="utf-8")) if os.path.exists(CANON_PATH) else {}
        except (ValueError, OSError):
            _CANON = {}
    return _CANON


def canonical_name(brand_norm, product_name):
    """title_geo 용 canonical 모델명. 배치 캐시 우선, 없으면 원 상품명 폴백."""
    return _canon_store().get("%s|%s" % (brand_norm, product_name), product_name)


def commerce_size(size, product_type):
    """커머스 제목용 사이즈: 가방 치수 제외, 아디 A/ 접두 제거,
    신발 숫자는 발길이 범위(150~350)일 때만 mm 부여(키즈 옷호수 90~140 오적용 방지)."""
    s = (size or "").strip()
    if not s or "*" in s or "cm" in s.lower():
        return ""
    if s.upper().startswith("A/"):
        s = s[2:]
    if product_type in lex.FOOTWEAR_TYPES and re.match(r"^\d+(\.\d+)?$", s) \
            and 150 <= float(s) <= 350:
        return s + "mm"
    return s


def attr_type(product_name, product_type):
    """제목용 유형: product_name 이 이미 유형어를 포함하면 재부착 생략('립스탑팬츠 … 팬츠' 방지)."""
    if not product_type:
        return ""
    if product_type in (product_name or "").replace(" ", ""):
        return ""
    return product_type


def clean_product_line(name, source, color):
    line = _JUNK.sub(" ", name or "")
    line = _ENUM_STOCK.sub(" ", line)   # 옵션 열거자("1-1. ")·재고문구·모지바케
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
    gender_code = norm_gender(row.get("gender"), name)
    gender = lex.GENDER_LABEL.get(gender_code, "")
    product_type = find_product_type(row.get("category"), name)
    product_line = clean_product_line(name, source, row.get("color"))
    product_name = strip_type(product_line, product_type)
    color = color_ko(_norm(row.get("color")))
    gender_c = gender if gender != "공용" else ""   # 커머스 제목엔 '공용' 노이즈 제외
    # title_geo: 브랜드 + canonical 모델명 + 유형 (색상·사이즈·성별 제외, AI검색 엔티티용).
    title_geo = compose_catalog_name(brand_norm, canonical_name(brand_norm, product_name),
                                     [product_type] if product_type else [])
    # title_commerce(기저): 브랜드+상품명+성별+유형+색상. 사이즈는 run_stage1 에서 전개하며 붙인다.
    title_commerce = compose_catalog_name(
        brand_norm, product_name,
        name_attrs(gender_c, attr_type(product_name, product_type), primary_color(color)))
    return {
        "source": source,
        "brand_norm": brand_norm,
        "style_code": (row.get("style_code") or "").strip(),
        "title_geo": title_geo,
        "title_commerce": title_commerce,
        "product_name": product_name,
        "gender": gender,
        "product_type": product_type or "",
        "color": color,
        "size": (row.get("sizes") or "").strip(),
        "material": _norm(row.get("material")),
        "origin": _norm(row.get("origin")),
        "gender_code": gender_code or "",
        "price": (row.get("price") or "").strip(),
        "url": (row.get("url") or "").strip(),
        "name": _norm(name),
        "needs_llm": "1" if compute_needs_llm(product_name) else "0",
    }


def run_stage1(in_path=IN_DEFAULT, out_path=OUT_DEFAULT, limit=0, llm_gate=False, llm_limit=0):
    if not os.path.exists(in_path):
        sys.exit("✗ 입력 없음: %s — 먼저 extract_all.py 로 all_brands.csv 를 만드세요." % in_path)
    rows = list(csv.DictReader(open(in_path, encoding="utf-8-sig")))
    if limit:
        rows = rows[:limit]
    out, n_empty = [], 0
    for r in rows:
        if not (r.get("name") or "").strip():
            n_empty += 1
            continue
        base = decompose_row(r)
        gender_c = base["gender"] if base["gender"] != "공용" else ""
        sizes = [s.strip() for s in (r.get("sizes") or "").split("|") if s.strip()]
        if sizes:
            # 사이즈마다 별도 카탈로그로 전개(title_commerce 끝에 단일 사이즈; title_geo 는 동일).
            for s in sizes:
                d = dict(base)
                d["size"] = s
                d["title_commerce"] = compose_catalog_name(
                    base["brand_norm"], base["product_name"],
                    name_attrs(gender_c, attr_type(base["product_name"], base["product_type"]),
                               primary_color(base["color"]), commerce_size(s, base["product_type"]), cap=5))
                out.append(d)
        else:
            out.append(base)
    n_llm = sum(1 for d in out if d["needs_llm"] == "1")
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
