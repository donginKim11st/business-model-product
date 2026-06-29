"""
Text normalization + structured attribute extraction.

This module implements the "extraction stage" the architecture review flagged as
missing: pack-count / volume / weight / color / connector / wattage are pulled out
as first-class structured attributes BEFORE any similarity is computed, because
those discriminating tokens are exactly the low-frequency tokens that Jaccard and
embeddings wash out (500ml vs 1.5L, 2-pack vs 6-pack, black vs silver).
"""
import re
import unicodedata

# ---------------------------------------------------------------------------
# Bilingual lexicons. In production this leg = a multilingual bi-encoder
# (e.g. BGE-M3) + ANN. For an offline, dependency-free demo we bridge KR<->EN
# with a small curated map so the cross-lingual story is honest and reproducible.
# ---------------------------------------------------------------------------
BRAND_LEXICON = {
    "sony": "sony", "소니": "sony",
    "apple": "apple", "애플": "apple", "airpods": "apple", "에어팟": "apple", "에어팟프로": "apple",
    "samsung": "samsung", "삼성": "samsung", "갤럭시": "samsung", "galaxy": "samsung",
    "nintendo": "nintendo", "닌텐도": "nintendo",
    "dyson": "dyson", "다이슨": "dyson",
    "lg": "lg", "엘지": "lg",
    "coca-cola": "cocacola", "코카콜라": "cocacola", "coca": "cocacola", "콜라": "cocacola",
    "downy": "downy", "다우니": "downy",
    "곰표": "gompyo", "대한제분": "gompyo",
    "sk-ii": "skii", "skii": "skii", "sk2": "skii",
    "anker": "anker", "앤커": "anker",
    # demo brand (registered at onboarding — real behaviour: a brand's name is
    # added to normalization when their catalog is onboarded)
    "비오라": "viora", "viora": "viora",
    "싸이닉": "scinic", "scinic": "scinic",
}

# token-level synonym normalization (applied during tokenization)
TERM_LEXICON = {
    "유기led": "oled", "oled": "oled",
    "유에스비씨": "usbc", "usb-c": "usbc", "usbc": "usbc", "c타입": "usbc", "ctype": "usbc",
    "lightning": "lightning", "라이트닝": "lightning",
    "디텍트": "detect", "detect": "detect",
    "제로": "zero", "zero": "zero",
    "리터": "l",
}

COLOR_LEXICON = {
    "black": "black", "블랙": "black",
    "silver": "silver", "실버": "silver", "platinum": "silver", "플래티넘": "silver",
    "white": "white", "화이트": "white",
    "neon": "neon", "네온": "neon", "네온블루레드": "neon",
}

CONNECTOR_LEXICON = {
    "usbc": "usbc", "lightning": "lightning",
}

# Product-category noun: the discriminator between different products of the SAME
# brand (앰플 vs 세럼 vs 크림) when there is no model code. Ordered most-specific
# first; matched as a substring of the title so Korean compounds (피테라에센스,
# 히알루론앰플) are caught. A category MISMATCH is treated as a hard conflict.
CATEGORY_LEXICON = [
    ("선크림", "suncream"), ("선블럭", "suncream"), ("선블록", "suncream"), ("아이크림", "eyecream"),
    ("앰플", "ampoule"), ("세럼", "serum"), ("에센스", "essence"), ("토너", "toner"), ("스킨", "toner"),
    ("로션", "lotion"), ("미스트", "mist"), ("크림", "cream"), ("클렌징", "cleanser"), ("클렌저", "cleanser"),
    ("마스크", "mask"), ("립밤", "lipbalm"), ("샴푸", "shampoo"),
    ("헤드폰", "headphone"), ("헤드셋", "headphone"), ("headphone", "headphone"), ("이어폰", "earphone"),
    ("마우스", "mouse"), ("청소기", "vacuum"), ("vacuum", "vacuum"), ("충전기", "charger"),
    ("칫솔", "toothbrush"), ("toothbrush", "toothbrush"), ("밀가루", "flour"), ("섬유유연제", "softener"),
]

# Product-line markers — the cosmetics equivalent of a model code. Configured
# PER BRAND at onboarding (here: SCINIC lines). Two listings whose line sets are
# both non-empty and DISJOINT are different products (슈퍼마일드 ≠ UV엑스퍼트),
# even at the same brand/category/size.
PRODUCT_LINES = [
    (("슈퍼마일드", "슈퍼 마일드"), "supermild"),
    (("uv엑스퍼트", "uv 엑스퍼트", "엑스퍼트"), "uvexpert"),
    (("아쿠아마일드", "아쿠아 마일드"), "aquamild"),
    (("스네일매트릭스", "스네일 매트릭스", "스네일"), "snail"),
    (("병풀",), "cicabp"),
    (("시카노이드",), "cicanoid"),
    (("파데스킵",), "fadeskip"),
    (("퍼펙트데일리", "퍼펙트 데일리"), "perfectdaily"),
    (("세이프티마일드", "세이프티 마일드", "세이프티"), "safety"),
    (("워터프루프",), "waterproof"),
    (("프레스티지",), "prestige"),
    (("더심플", "더 심플"), "simple"),
    (("히아루론산앰플", "히알루론산앰플", "히아루론산 앰플", "히알루론산 앰플"), "hyaluronampoule"),
]
# unified size token (50ml / 60g / 1.5l / 1kg) — catches cross-unit mismatch
# (50ml vs 60g) that the separate volume_ml/weight_g fields miss.
_SIZE_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|kg|g|l)\b", re.I)

GRAYMARKET_MARKERS = ["병행수입", "면세", "직구", "해외구매"]
BUNDLE_MARKERS = ["번들", "패키지", "bundle", "풀패키지"]
# a genuine bundle joins two DIFFERENT products ("앰플 + 로션", "2종") — it must NOT
# fire on "SPF50+", "PA++++", "1+1", or "3개세트"(single-product multi-buy).
_SPF_PA_RE = re.compile(r"spf\s*\d+\+*|pa\++|\d+\s*\+\s*\d+", re.I)
_MULTIKIND_RE = re.compile(r"\d\s*종")  # "2종" = two kinds → real bundle


def _has_bundle_plus(raw):
    return "+" in _SPF_PA_RE.sub(" ", raw) or bool(_MULTIKIND_RE.search(raw))
# Condition is a SEMANTIC distinction (refurbished/used vs new) with no clean
# numeric attribute — it is deliberately NOT a hard conflict, so the pipeline
# routes it to the LLM adjudicator instead of separating it with a rule.
CONDITION_MARKERS = {
    "refurb": ["리퍼비시", "리퍼", "refurbished", "refurb"],
    "used": ["중고", "used", "전시품"],
}


def nfkc(text):
    return unicodedata.normalize("NFKC", text or "")


# spelling synonyms unified at the text layer so tokenize/category/line/size all
# see one canonical form (썬에센스 == 선에센스).
_SYNONYMS = [("썬", "선")]


def lower(text):
    t = nfkc(text).lower()
    for a, b in _SYNONYMS:
        t = t.replace(a, b)
    return t


# tokens: keep ascii alnum runs and CJK runs; drop pure punctuation
_TOKEN_RE = re.compile(r"[a-z0-9]+|[가-힣]+")

# marketing / packaging / generic-benefit noise that doesn't identify a product —
# removed for similarity so the SAME product matches across noisy seller titles.
STOPWORDS = {
    "정품", "무료배송", "무료", "배송", "당일발송", "당일출고", "새상품", "미개봉",
    "단독구성", "단독", "구성", "어워드", "위너", "기획", "세트", "본품", "단품",
    "사은품", "증정", "최저가", "대용량", "약산성", "저자극", "보습", "수분", "진정",
    "속보습", "영양", "비건", "pa", "pc",
}
_STOP_RE = re.compile(r"^spf\d*$")


def tokenize(text):
    """Word-level tokens, lowercased, synonym-normalized, noise removed."""
    out = []
    for t in _TOKEN_RE.findall(lower(text)):
        if t in STOPWORDS or _STOP_RE.match(t):
            continue
        out.append(TERM_LEXICON.get(t, t))
    return out


def char_ngrams(text, n=3):
    s = re.sub(r"\s+", "", lower(text))
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


# ---------------------------------------------------------------------------
# Attribute extraction
# ---------------------------------------------------------------------------
_VOLUME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|l|리터|리)\b")
_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g)\b")
_WATT_RE = re.compile(r"(\d+)\s*w\b")
# pack count: "24개", "24개입", "24펫", "x24", "x 24", "24pet", "12개"
_PACK_RE = re.compile(r"(?:x\s*(\d+)\b)|(\d+)\s*(?:개입|개|펫|pet|입|박스(?:입)?|병|캔)\b")
# connector: detect on raw text (tokenizer would split usb-c at the hyphen)
_CONNECTOR_RE = [
    ("usbc", re.compile(r"usb[\s-]?c|type[\s-]?c|c\s*타입|유에스비씨")),
    ("lightning", re.compile(r"lightning|라이트닝")),
]
# model-code candidate: an alnum run (hyphens allowed inside), normalized later
_MODEL_TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# measurement-ish / ordinal tokens that must NOT be treated as model codes
_MEASURE_LIKE = re.compile(r"^\d+(?:\.\d+)?(ml|l|kg|g|w|개|펫|pet|입|박스|세|세대|년형|년)?$")
_ORDINAL_LIKE = re.compile(r"^\d+(st|nd|rd|th)$")


def _to_ml(value, unit):
    v = float(value)
    if unit in ("l", "리터", "리"):
        return v * 1000.0
    return v


def _to_g(value, unit):
    v = float(value)
    if unit == "kg":
        return v * 1000.0
    return v


def extract_attributes(record):
    """Return a dict of structured attributes for a listing record."""
    raw = record.get("title", "")
    text = lower(raw)
    norm_tokens = tokenize(raw)
    token_set = set(norm_tokens)

    # brand: from brand_raw if present, else infer from tokens
    brand = ""
    braw = lower(record.get("brand_raw", "")).strip()
    if braw and braw in BRAND_LEXICON:
        brand = BRAND_LEXICON[braw]
    if not brand:
        for t in norm_tokens:
            if t in BRAND_LEXICON:
                brand = BRAND_LEXICON[t]
                break

    # volume (normalize to ml)
    volume_ml = None
    m = _VOLUME_RE.search(text)
    if m:
        volume_ml = _to_ml(m.group(1), m.group(2))

    # weight (normalize to g)
    weight_g = None
    m = _WEIGHT_RE.search(text)
    if m:
        weight_g = _to_g(m.group(1), m.group(2))

    # wattage
    wattage = None
    m = _WATT_RE.search(text)
    if m:
        wattage = int(m.group(1))

    # pack count
    pack_count = None
    m = _PACK_RE.search(text)
    if m:
        pack_count = int(m.group(1) or m.group(2))

    # color
    color = None
    for t in norm_tokens:
        if t in COLOR_LEXICON:
            color = COLOR_LEXICON[t]
            break
    if color is None:
        for k, v in COLOR_LEXICON.items():
            if k in text:
                color = v
                break

    # connector: detect on raw text (handles "usb-c", "type-c", "c타입", "유에스비씨")
    connector = None
    for label, pat in _CONNECTOR_RE:
        if pat.search(text):
            connector = label
            break

    # model code: alnum runs with both a letter and a digit; exclude measurements,
    # ordinals ("2nd"), and brand tokens ("sk2"). Hyphens are stripped (WH-1000XM5).
    models = set()
    for raw_tok in _MODEL_TOKEN_RE.findall(text):
        cand = raw_tok.replace("-", "")
        if len(cand) < 3:
            continue
        if _MEASURE_LIKE.match(cand) or _ORDINAL_LIKE.match(cand):
            continue
        if cand in BRAND_LEXICON:
            continue
        if any(c.isalpha() for c in cand) and any(c.isdigit() for c in cand):
            models.add(cand)
    # for LG gram style "16z90s" + "ga56k" keep the most specific (longest)
    model = max(models, key=len) if models else None

    is_bundle = any(mk in raw for mk in BUNDLE_MARKERS) or _has_bundle_plus(raw)
    is_graymarket = any(mk in raw for mk in GRAYMARKET_MARKERS)

    condition = None  # None == new/unspecified
    for cond, markers in CONDITION_MARKERS.items():
        if any(mk in text for mk in markers):
            condition = cond
            break

    # category from the EARLIEST-occurring marker in the title (the head noun),
    # so "수분 선에센스 / 저자극 선크림 차단" resolves to essence, not suncream.
    category, best = None, len(text) + 1
    for sub, label in CATEGORY_LEXICON:
        i = text.find(sub)
        if 0 <= i < best:
            best, category = i, label

    text_ns = text.replace(" ", "")
    product_lines = sorted({label for variants, label in PRODUCT_LINES
                            if any(v.replace(" ", "") in text_ns for v in variants)})

    sm = _SIZE_TOKEN_RE.search(text)
    size_token = (sm.group(1) + sm.group(2).lower()) if sm else None

    return {
        "brand": brand or None,
        "model": model,
        "models": sorted(models),
        "color": color,
        "connector": connector,
        "volume_ml": volume_ml,
        "weight_g": weight_g,
        "wattage": wattage,
        "pack_count": pack_count,
        "is_bundle": is_bundle,
        "is_graymarket": is_graymarket,
        "condition": condition,
        "category": category,
        "product_lines": product_lines,
        "size_token": size_token,
        "tokens": norm_tokens,
        "token_set": token_set,
    }
