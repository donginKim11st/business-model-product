# 카탈로그명 추출 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스포츠/아웃도어 정형 CSV(`outputs/all_brands.csv`, 61K행)에서 규칙 기반으로 깨끗한 카탈로그명을 분해·추출하고 색상 통합 모델 단위로 묶는다.

**Architecture:** 2-스테이지 파일 변환. Stage1(`catalog_decompose.py`)이 행별로 `catalog_name`+분해필드를 산출하고, Stage2(`catalog_group.py`)가 style-code base(브랜드별 규칙)·이름 폴백으로 모델 단위 묶음+대표명을 만든다. 도메인 지식은 `catalog_lexicon.py` 한 곳. LLM은 옵션(`--llm-gate`)으로 잔여 하드케이스만.

**Tech Stack:** Python 3.9(stdlib만: csv/re/urllib), pytest 8.3.5. 신규 의존성 없음.

## Global Constraints

- **Python 3.9 호환** — `match`문, `X | Y` 타입유니온, `str.removesuffix` 등 3.10+ 문법 금지.
- **stdlib 전용** — 신규 pip 의존성 추가 금지. LLM 호출은 `urllib.request`.
- **파일 위치** — 모듈은 `identity/` 루트, 산출은 `identity/outputs/`, 테스트는 `identity/tests/`. 경로는 `HERE = os.path.dirname(os.path.abspath(__file__))` 기준(cwd 무관).
- **CSV I/O** — 읽기/쓰기 모두 `encoding="utf-8-sig"`.
- **도메인 지식은 `catalog_lexicon.py`에만** — 파서 코드에 브랜드/색상/유형 하드코딩 금지.
- **LLM 기본 OFF** — `--llm-gate` 없으면 네트워크 호출 없음(cost-priority). 사용 시 `gpt-4o-mini`(env `OPENAI_MODEL` 오버라이드), temperature 0.
- **테스트 실행** — `identity/` 디렉토리에서 `python3 -m pytest`.
- **커밋 메시지** — Conventional Commits, scope `catalog`. **Claude-Session 트레일러 넣지 말 것**(사용자 규칙).

---

### Task 1: 도메인 사전 + pytest 스캐폴딩

**Files:**
- Create: `identity/catalog_lexicon.py`
- Create: `identity/conftest.py`
- Test: `identity/tests/test_catalog_lexicon.py`

**Interfaces:**
- Consumes: 없음
- Produces: `catalog_lexicon` 모듈 — `BRAND_KO: dict[str,str]`, `BRAND_ALIASES: dict[str,list[str]]`, `GENDER_MAP: dict[str,str]`, `GENDER_NAME_TOKENS: list[str]`, `PRODUCT_TYPES: list[str]`(긴 것 우선 정렬), `COLOR_TOKENS: list[str]`, `STYLECODE_SUFFIX: dict[str,dict]`.

- [ ] **Step 1: conftest.py 작성(pytest 경로)**

`identity/conftest.py`:
```python
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
```

- [ ] **Step 2: 실패 테스트 작성**

`identity/tests/test_catalog_lexicon.py`:
```python
import catalog_lexicon as lex

def test_all_30_brand_slugs_present():
    expected = {
        "adidas", "kolping", "natgeo", "nepa", "nike", "montbell", "arena",
        "skechers", "northface", "eider", "proworldcup", "mizuno", "k2",
        "millet", "nb", "underarmour", "blackyak", "outdoorproducts", "vans",
        "worldcup", "prospecs", "starsports", "columbia", "crocs", "redface",
        "puma", "westwood", "jansport", "fila", "lecaf",
    }
    assert expected <= set(lex.BRAND_KO)
    assert lex.BRAND_KO["nike"] == "나이키"
    assert lex.BRAND_KO["fila"] == "휠라"  # brand 컬럼엔 KIDS 오염값

def test_gender_map_core():
    assert lex.GENDER_MAP["남성"] == "M"
    assert lex.GENDER_MAP["women"] == "W"
    assert lex.GENDER_MAP["공용"] == "U"
    assert lex.GENDER_MAP["키즈"] == "K"
    assert "outlet" not in lex.GENDER_MAP  # 성별 아닌 값은 매핑 안 함

def test_product_types_longest_first():
    # 부분매칭 오검출 방지: '축구화'가 '신발'보다 앞
    assert lex.PRODUCT_TYPES.index("축구화") < lex.PRODUCT_TYPES.index("신발")
    assert lex.PRODUCT_TYPES.index("다운재킷") < lex.PRODUCT_TYPES.index("재킷")

def test_stylecode_suffix_rules():
    assert lex.STYLECODE_SUFFIX["nike"] == {"sep": "-"}
    assert lex.STYLECODE_SUFFIX["arena"] == {"tail_alpha": 3}
    assert lex.STYLECODE_SUFFIX["columbia"] == {"tail_digit": 3}
    assert "adidas" not in lex.STYLECODE_SUFFIX  # 폴백 대상
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_catalog_lexicon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'catalog_lexicon'`

- [ ] **Step 4: catalog_lexicon.py 작성**

`identity/catalog_lexicon.py`:
```python
"""스포츠/아웃도어 카탈로그명 추출 — 도메인 사전(단일 출처). 로직 없음, 데이터만.

brand 컬럼은 오염값(KIDS/INTIMO/OUTLET)이 있어 신뢰 불가 → source 슬러그 기준.
값은 all_brands.csv 실측(2026-07-01, 30 브랜드).
"""

BRAND_KO = {
    "adidas": "아디다스", "kolping": "콜핑", "natgeo": "내셔널지오그래픽",
    "nepa": "네파", "nike": "나이키", "montbell": "몽벨", "arena": "아레나",
    "skechers": "스케쳐스", "northface": "노스페이스", "eider": "아이더",
    "proworldcup": "프로월드컵", "mizuno": "미즈노", "k2": "케이투",
    "millet": "밀레", "nb": "뉴발란스", "underarmour": "언더아머",
    "blackyak": "블랙야크", "outdoorproducts": "아웃도어프로덕츠", "vans": "반스",
    "worldcup": "월드컵", "prospecs": "프로스펙스", "starsports": "스타스포츠",
    "columbia": "컬럼비아", "crocs": "크록스", "redface": "레드페이스",
    "puma": "푸마", "westwood": "웨스트우드", "jansport": "잔스포츠",
    "fila": "휠라", "lecaf": "르까프",
}

# 이름에서 제거할 브랜드 별칭(영문/복합 표기). 슬러그+한글명은 코드에서 자동 추가.
BRAND_ALIASES = {
    "nike": ["nike"], "adidas": ["adidas"], "puma": ["puma"],
    "nb": ["newbalance", "new balance", "nb"], "fila": ["fila"],
    "crocs": ["crocs"], "vans": ["vans"], "skechers": ["skechers"],
    "columbia": ["columbia"], "underarmour": ["under armour", "underarmour", "ua"],
    "natgeo": ["national geographic", "natgeo"], "mizuno": ["mizuno"],
}

GENDER_MAP = {
    "남성": "M", "men": "M", "male": "M", "남": "M", "mens": "M", "man": "M",
    "여성": "W", "women": "W", "female": "W", "여": "W", "womens": "W", "woman": "W",
    "공용": "U", "unisex": "U", "남녀공용": "U", "남여공용": "U", "공통": "U",
    "키즈": "K", "kids": "K", "아동": "K", "kid": "K", "주니어": "K", "junior": "K",
}

GENDER_NAME_TOKENS = ["남녀공용", "남여공용", "남성", "여성", "공용", "키즈", "아동",
                      "주니어", "junior", "women", "womens", "mens", "men",
                      "kids", "unisex"]

# 긴 것 우선(부분 문자열 매칭 오검출 방지). 신발/의류/가방/모자 계열.
PRODUCT_TYPES = [
    "트레킹화", "테니스화", "농구화", "골프화", "러닝화", "등산화", "워킹화",
    "축구화", "실내화", "스니커즈", "슬리퍼", "샌들", "부츠", "신발",
    "다운재킷", "다운자켓", "바람막이", "패딩", "재킷", "자켓", "점퍼", "코트",
    "베이스레이어", "트레이닝팬츠", "조거팬츠", "래시가드", "수영복",
    "맨투맨", "후드티", "후드", "티셔츠", "니트", "셔츠", "폴로",
    "레깅스", "반바지", "쇼츠", "팬츠", "바지", "원피스", "스커트", "치마",
    "브라탑", "탑", "크로스백", "숄더백", "토트백", "백팩", "파우치", "지갑", "가방",
    "비니", "캡", "모자", "양말", "장갑", "머플러", "벨트",
]

COLOR_TOKENS = [
    "black", "블랙", "white", "화이트", "그레이", "gray", "grey", "네이비", "navy",
    "레드", "red", "블루", "blue", "그린", "green", "옐로우", "yellow", "핑크", "pink",
    "퍼플", "purple", "오렌지", "orange", "브라운", "brown", "베이지", "beige",
    "카키", "khaki", "실버", "silver", "골드", "gold", "민트", "mint", "코랄", "coral",
    "버건디", "burgundy", "차콜", "charcoal", "아이보리", "ivory", "라벤더", "lavender",
]

# 브랜드별 style_code 색상 접미사 규칙(모델 base 분리). 신뢰 가능한 브랜드만 등록.
# 미등록 → 이름 기반 폴백(catalog_group 처리).
#   sep: 구분자로 마지막 세그먼트 절단
#   tail: 끝 N자 절단 | tail_alpha: 끝 N자가 전부 알파벳일 때 절단 | tail_digit: 끝 N자가 숫자일 때
STYLECODE_SUFFIX = {
    "nike": {"sep": "-"},
    "lecaf": {"sep": "-"},
    "puma": {"sep": "_"},
    "arena": {"tail_alpha": 3},
    "k2": {"tail": 2},
    "eider": {"tail": 2},
    "columbia": {"tail_digit": 3},
    "redface": {"tail_digit": 3},
    "prospecs": {"tail_digit": 3},
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_catalog_lexicon.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: 커밋**

```bash
git add identity/catalog_lexicon.py identity/conftest.py identity/tests/test_catalog_lexicon.py
git commit -m "feat(catalog): 카탈로그명 추출 도메인 사전 + pytest 스캐폴딩"
```

---

### Task 2: 행별 분해 핵심 `decompose_row`

**Files:**
- Create: `identity/catalog_decompose.py`
- Test: `identity/tests/test_catalog_decompose.py`

**Interfaces:**
- Consumes: `catalog_lexicon` (Task 1)
- Produces: `catalog_decompose` 모듈 — 순수함수들:
  - `decompose_row(row: dict) -> dict` (키: source, brand_norm, style_code, catalog_name, product_line, product_type, gender_norm, colorway, price, sizes, url, name, needs_llm)
  - `norm_gender(raw, name) -> Optional[str]`, `find_product_type(category, name) -> Optional[str]`, `clean_product_line(name, source, color) -> str`, `compute_needs_llm(product_line) -> bool`, `OUT_COLS: list[str]`

- [ ] **Step 1: 실패 테스트 작성**

`identity/tests/test_catalog_decompose.py`:
```python
import catalog_decompose as cd

def _row(**kw):
    base = {"source": "", "brand": "", "style_code": "", "name": "", "color": "",
            "price": "", "currency": "KRW", "category": "", "gender": "",
            "sizes": "", "origin": "", "material": "", "mfg_date": "", "url": ""}
    base.update(kw)
    return base

def test_trailing_type_and_gender_adidas():
    r = _row(source="adidas", name="F50 하이퍼패스트 클럽 벨크로 아스트로 터프 축구화 키즈",
             gender="KIDS", category="신발", color="Pink")
    d = cd.decompose_row(r)
    assert d["brand_norm"] == "아디다스"
    assert d["gender_norm"] == "K"
    assert d["product_type"] == "축구화"
    assert "키즈" not in d["product_line"]
    assert d["catalog_name"].startswith("아디다스 F50 하이퍼패스트")
    assert d["catalog_name"].endswith("축구화")

def test_leading_gender_blackyak():
    r = _row(source="blackyak", name="남성 아이스프레쉬 라운드 베이스레이어",
             gender="남성", category="상의", color="BLACK,NAVY,WHITE")
    d = cd.decompose_row(r)
    assert d["gender_norm"] == "M"
    assert d["product_type"] == "베이스레이어"
    assert not d["product_line"].startswith("남성")
    assert d["catalog_name"] == "블랙야크 아이스프레쉬 라운드 베이스레이어"

def test_name_only_eider():
    r = _row(source="eider", name="ST 슬라이드 2", gender="공용",
             category="신발", color="Red")
    d = cd.decompose_row(r)
    assert d["gender_norm"] == "U"
    assert d["product_line"] == "ST 슬라이드 2"
    assert d["catalog_name"] == "아이더 ST 슬라이드 2"

def test_trailing_color_jansport():
    r = _row(source="jansport", name="슈퍼브레이크 BLACK", gender="",
             category="백팩", color="BLACK")
    d = cd.decompose_row(r)
    assert d["product_type"] == "백팩"
    assert "BLACK" not in d["product_line"].upper()
    assert d["catalog_name"] == "잔스포츠 슈퍼브레이크"

def test_paren_gender_kolping():
    r = _row(source="kolping", name="국민바지2.5 210(남)", gender="MALE",
             category="여름 바지", color="BLACK|KHAKI|NAVY")
    d = cd.decompose_row(r)
    assert d["gender_norm"] == "M"
    assert "(남)" not in d["product_line"]
    assert d["catalog_name"].startswith("콜핑 국민바지2.5")

def test_bilingual_dup_flags_needs_llm():
    r = _row(source="puma", name="푸마 아반티 LS Puma Avanti LS", gender="남성",
             category="신발", color="PUMA Black-PUMA White")
    d = cd.decompose_row(r)
    # 브랜드/성별 제거 후에도 한글+트레일링 다단어 영문 잔존 → LLM 게이트 대상
    assert d["needs_llm"] == "1"

def test_out_cols_stable():
    assert cd.OUT_COLS[:4] == ["source", "brand_norm", "style_code", "catalog_name"]
    assert "needs_llm" in cd.OUT_COLS
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_catalog_decompose.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'catalog_decompose'`

- [ ] **Step 3: catalog_decompose.py 핵심부 작성(CLI 제외)**

`identity/catalog_decompose.py`:
```python
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

_JUNK = re.compile(r"★[^★]*★|\[[^\]]*\]|[（(][^)）]*[)）]")
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
        if tok.lower() in low:
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
        out = re.sub(re.escape(tok), " ", out, flags=re.IGNORECASE)
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_catalog_decompose.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add identity/catalog_decompose.py identity/tests/test_catalog_decompose.py
git commit -m "feat(catalog): Stage1 decompose_row 규칙 기반 카탈로그명 분해"
```

---

### Task 3: Stage1 CLI (all_brands.csv → catalog_decomposed.csv)

**Files:**
- Modify: `identity/catalog_decompose.py` (append `main()` + `__main__`)
- Test: `identity/tests/test_catalog_decompose.py` (add CLI I/O test)

**Interfaces:**
- Consumes: `decompose_row`, `OUT_COLS` (Task 2)
- Produces: `run_stage1(in_path, out_path, limit=0) -> dict` (요약: {"rows": N, "needs_llm": M}); CLI `python3 catalog_decompose.py`.

- [ ] **Step 1: 실패 테스트 추가**

`identity/tests/test_catalog_decompose.py` 하단에 추가:
```python
import csv as _csv
import os as _os

def test_run_stage1_writes_output(tmp_path):
    src = tmp_path / "in.csv"
    with open(src, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["source", "brand", "style_code", "name",
            "color", "price", "currency", "category", "gender", "sizes",
            "origin", "material", "mfg_date", "url"])
        w.writeheader()
        w.writerow({"source": "eider", "style_code": "DUS26N77R2",
                    "name": "ST 슬라이드 2", "color": "Red", "gender": "공용",
                    "category": "신발", "price": "39000", "sizes": "250|260",
                    "url": "http://x"})
    out = tmp_path / "out.csv"
    summary = cd.run_stage1(str(src), str(out), limit=0)
    assert summary["rows"] == 1
    rows = list(_csv.DictReader(open(out, encoding="utf-8-sig")))
    assert rows[0]["catalog_name"] == "아이더 ST 슬라이드 2"
    assert list(rows[0].keys()) == cd.OUT_COLS
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_catalog_decompose.py::test_run_stage1_writes_output -v`
Expected: FAIL — `AttributeError: module 'catalog_decompose' has no attribute 'run_stage1'`

- [ ] **Step 3: main/run_stage1 구현(파일 하단에 추가)**

`identity/catalog_decompose.py` 끝에 추가:
```python
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
```

> 주: `--llm-gate`가 참조하는 `catalog_llm_gate`는 Task 6에서 생성. 그 전까지 `--llm-gate` 미사용(기본 OFF)이면 import 되지 않음.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_catalog_decompose.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add identity/catalog_decompose.py identity/tests/test_catalog_decompose.py
git commit -m "feat(catalog): Stage1 CLI — all_brands.csv → catalog_decomposed.csv"
```

---

### Task 4: Stage2 묶음 핵심 (`model_key`, `base_style_code`, `group`)

**Files:**
- Create: `identity/catalog_group.py`
- Test: `identity/tests/test_catalog_group.py`

**Interfaces:**
- Consumes: `catalog_lexicon` (Task 1); 입력은 Stage1 산출 dict(키: source, brand_norm, style_code, catalog_name, product_line, product_type, gender_norm, colorway, price, sizes, url).
- Produces: `catalog_group` 모듈 — `base_style_code(source, style_code) -> Optional[str]`, `name_key(d) -> str`, `model_key(d) -> str`, `group(drows) -> list[dict]`, `GROUP_COLS: list[str]`.

- [ ] **Step 1: 실패 테스트 작성**

`identity/tests/test_catalog_group.py`:
```python
import catalog_group as cg

def _d(**kw):
    base = {"source": "", "brand_norm": "", "style_code": "", "catalog_name": "",
            "product_line": "", "product_type": "", "gender_norm": "",
            "colorway": "", "price": "", "sizes": "", "url": ""}
    base.update(kw)
    return base

def test_base_style_code_rules():
    assert cg.base_style_code("nike", "IM5752-300") == "IM5752"
    assert cg.base_style_code("puma", "409960_01") == "409960"
    assert cg.base_style_code("arena", "A6BL1LO15WHT") == "A6BL1LO15"
    assert cg.base_style_code("k2", "KUF26C53HB") == "KUF26C53"
    assert cg.base_style_code("columbia", "C72YM3621346") == "C72YM3621"
    assert cg.base_style_code("adidas", "KK1334") is None  # 규칙 없음 → 폴백

def test_group_by_stylecode_base_merges_colorways():
    rows = [
        _d(source="nike", style_code="IM5752-300", catalog_name="나이키 에어 포스 1",
           product_line="에어 포스 1", colorway="퍼", price="159000", sizes="240|250"),
        _d(source="nike", style_code="IM5752-100", catalog_name="나이키 에어 포스 1",
           product_line="에어 포스 1", colorway="화이트", price="159000", sizes="250|260"),
    ]
    cats = cg.group(rows)
    assert len(cats) == 1
    c = cats[0]
    assert c["catalog_name"] == "나이키 에어 포스 1"
    assert c["n_variants"] == "2"
    assert c["n_colorways"] == "2"
    assert c["style_codes"] == "2"
    assert c["size_range"] == "240~260"

def test_group_by_name_fallback_adidas():
    rows = [
        _d(source="adidas", style_code="KK1334", catalog_name="아디다스 삼바",
           product_line="삼바", product_type="신발", gender_norm="U", colorway="Pink"),
        _d(source="adidas", style_code="HQ2274", catalog_name="아디다스 삼바",
           product_line="삼바", product_type="신발", gender_norm="U", colorway="Black"),
    ]
    cats = cg.group(rows)
    assert len(cats) == 1  # style_code 규칙 없어도 이름으로 묶임
    assert cats[0]["n_colorways"] == "2"

def test_group_cols_stable():
    assert cg.GROUP_COLS[:4] == ["source", "brand_norm", "model_key", "catalog_name"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_catalog_group.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'catalog_group'`

- [ ] **Step 3: catalog_group.py 핵심부 작성**

`identity/catalog_group.py`:
```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_catalog_group.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add identity/catalog_group.py identity/tests/test_catalog_group.py
git commit -m "feat(catalog): Stage2 모델 묶음 — style-code base + 이름 폴백"
```

---

### Task 5: Stage2 CLI (catalog_decomposed.csv → catalogs.csv)

**Files:**
- Modify: `identity/catalog_group.py` (append `run_stage2()` + `main()` + `__main__`)
- Test: `identity/tests/test_catalog_group.py` (add CLI I/O test)

**Interfaces:**
- Consumes: `group`, `GROUP_COLS` (Task 4)
- Produces: `run_stage2(in_path, out_path, llm_gate=False, llm_limit=0) -> dict`; CLI `python3 catalog_group.py`.

- [ ] **Step 1: 실패 테스트 추가**

`identity/tests/test_catalog_group.py` 하단에 추가:
```python
import csv as _csv

def test_run_stage2_writes_output(tmp_path):
    src = tmp_path / "dec.csv"
    import catalog_decompose as cd
    with open(src, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=cd.OUT_COLS)
        w.writeheader()
        w.writerow({"source": "nike", "brand_norm": "나이키", "style_code": "IM5752-300",
                    "catalog_name": "나이키 에어 포스 1", "product_line": "에어 포스 1",
                    "product_type": "신발", "gender_norm": "M", "colorway": "퍼",
                    "price": "159000", "sizes": "240|250", "url": "http://x",
                    "name": "에어 포스 1", "needs_llm": "0"})
        w.writerow({"source": "nike", "brand_norm": "나이키", "style_code": "IM5752-100",
                    "catalog_name": "나이키 에어 포스 1", "product_line": "에어 포스 1",
                    "product_type": "신발", "gender_norm": "M", "colorway": "화이트",
                    "price": "159000", "sizes": "250|260", "url": "http://y",
                    "name": "에어 포스 1", "needs_llm": "0"})
    out = tmp_path / "cat.csv"
    summary = cg.run_stage2(str(src), str(out))
    assert summary["catalogs"] == 1
    rows = list(_csv.DictReader(open(out, encoding="utf-8-sig")))
    assert rows[0]["n_variants"] == "2"
    assert list(rows[0].keys()) == cg.GROUP_COLS
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_catalog_group.py::test_run_stage2_writes_output -v`
Expected: FAIL — `AttributeError: module 'catalog_group' has no attribute 'run_stage2'`

- [ ] **Step 3: run_stage2/main 구현(파일 하단에 추가)**

`identity/catalog_group.py` 끝에 추가:
```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_catalog_group.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add identity/catalog_group.py identity/tests/test_catalog_group.py
git commit -m "feat(catalog): Stage2 CLI — catalog_decomposed.csv → catalogs.csv"
```

---

### Task 6: LLM 게이트 (옵션, 잔여 한정·캐시·비용상한)

**Files:**
- Create: `identity/catalog_llm_gate.py`
- Test: `identity/tests/test_catalog_llm_gate.py`

**Interfaces:**
- Consumes: Stage1 out dict 리스트(키 `needs_llm`, `name`, `brand_norm`, `product_line`, `product_type`, `gender_norm`), Stage2 cats 리스트.
- Produces: `catalog_llm_gate` 모듈 — `MODEL: str`, `_cache_load()/_cache_save()`, `apply_stage1(rows, limit=0, api_key=None, cache=None) -> int`, `apply_stage2(cats, drows, limit=0, api_key=None, cache=None) -> int`, `_call_openai(prompt, api_key) -> str`(네트워크; 테스트는 캐시로 우회).
- 캐시 파일: `outputs/_catalog_llm_cache.json`.

- [ ] **Step 1: 실패 테스트 작성(네트워크 없이 캐시 경로만)**

`identity/tests/test_catalog_llm_gate.py`:
```python
import json
import catalog_llm_gate as gate

def test_apply_stage1_uses_cache_no_network():
    rows = [
        {"needs_llm": "1", "name": "푸마 아반티 LS Puma Avanti LS", "brand_norm": "푸마",
         "product_line": "아반티 LS Puma Avanti LS", "product_type": "신발", "gender_norm": "M",
         "catalog_name": "푸마 아반티 LS Puma Avanti LS"},
        {"needs_llm": "0", "name": "아이더 ST 슬라이드 2", "brand_norm": "아이더",
         "product_line": "ST 슬라이드 2", "product_type": "신발", "gender_norm": "U",
         "catalog_name": "아이더 ST 슬라이드 2"},
    ]
    # 캐시 선주입 → 네트워크 호출 없이 처리
    cache = {gate._key1(rows[0]): {"product_line": "아반티 LS", "product_type": "신발", "gender": "M"}}
    n = gate.apply_stage1(rows, limit=0, api_key="TEST", cache=cache)
    assert n == 1
    assert rows[0]["product_line"] == "아반티 LS"
    assert rows[0]["catalog_name"] == "푸마 아반티 LS"
    assert rows[0]["needs_llm"] == "0"  # 보정됨
    # needs_llm=0 행은 건드리지 않음
    assert rows[1]["product_line"] == "ST 슬라이드 2"

def test_apply_stage1_limit_zero_when_no_candidates():
    rows = [{"needs_llm": "0", "name": "x", "brand_norm": "b", "product_line": "x",
             "product_type": "", "gender_norm": "", "catalog_name": "b x"}]
    n = gate.apply_stage1(rows, limit=0, api_key="TEST", cache={})
    assert n == 0

def test_limit_caps_and_reports(capsys):
    rows = [{"needs_llm": "1", "name": "a", "brand_norm": "b", "product_line": "a",
             "product_type": "", "gender_norm": "", "catalog_name": "b a"},
            {"needs_llm": "1", "name": "c", "brand_norm": "b", "product_line": "c",
             "product_type": "", "gender_norm": "", "catalog_name": "b c"}]
    # 캐시에 1건만 → limit=1 이면 1건 처리, 1건은 미처리(로그 보고)
    cache = {gate._key1(rows[0]): {"product_line": "a2", "product_type": "", "gender": ""}}
    n = gate.apply_stage1(rows, limit=1, api_key="TEST", cache=cache)
    assert n == 1
    out = capsys.readouterr().out
    assert "미보정" in out  # 무음 절단 금지
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_catalog_llm_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'catalog_llm_gate'`

- [ ] **Step 3: catalog_llm_gate.py 작성**

`identity/catalog_llm_gate.py`:
```python
#!/usr/bin/env python3
"""카탈로그명 추출 LLM 게이트(옵션·잔여 한정). 기본 OFF.

규칙이 저신뢰(needs_llm=1)로 남긴 행/그룹만 gpt-4o-mini 로 보정. 재개 캐시 + 비용상한.
네트워크는 _call_openai 에만; 캐시 히트 시 호출 없음(테스트는 캐시 선주입으로 우회).
"""
import os
import re
import sys
import json
import hashlib
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
URL = "https://api.openai.com/v1/chat/completions"
CACHE_PATH = os.path.join(HERE, "outputs", "_catalog_llm_cache.json")
_WS = re.compile(r"\s+")


def _cache_load():
    if os.path.exists(CACHE_PATH):
        try:
            return json.load(open(CACHE_PATH, encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def _cache_save(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)


def _key1(row):
    return "d1:" + hashlib.md5((row.get("name", "")).encode("utf-8")).hexdigest()


def _call_openai(prompt, api_key):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 200,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("authorization", "Bearer %s" % api_key)
    with urllib.request.urlopen(req, timeout=40) as resp:
        payload = json.loads(resp.read())
    return payload["choices"][0]["message"]["content"].strip()


def _decompose_prompt(row):
    return (
        "다음 스포츠/아웃도어 상품의 원본명에서 '깨끗한 상품명(product_line)'과 "
        "'상품유형(product_type)', '성별(gender: M/W/U/K/빈칸)'을 뽑으세요.\n"
        "규칙: 브랜드명·성별·색상·한/영 중복은 상품명에서 제외. 모델라인+유형만 남김.\n"
        "브랜드: %s\n원본명: %s\n"
        '오직 JSON만: {"product_line": "...", "product_type": "...", "gender": "M|W|U|K|"}'
        % (row.get("brand_norm", ""), row.get("name", ""))
    )


def apply_stage1(rows, limit=0, api_key=None, cache=None):
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY")
    own_cache = cache is None
    if own_cache:
        cache = _cache_load()
    cands = [r for r in rows if r.get("needs_llm") == "1"]
    if not cands:
        return 0
    budget = limit if limit else len(cands)
    n_done = 0
    for r in cands:
        if n_done >= budget:
            break
        k = _key1(r)
        if k not in cache:
            if not api_key:
                continue
            try:
                txt = _call_openai(_decompose_prompt(r), api_key)
                cache[k] = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            except Exception as e:  # noqa: BLE001
                print("  [LLM] 호출 실패(규칙 유지): %s" % e)
                continue
        parsed = cache[k]
        pl = _WS.sub(" ", (parsed.get("product_line") or "")).strip()
        if pl:
            r["product_line"] = pl
            r["product_type"] = parsed.get("product_type") or r.get("product_type", "")
            g = parsed.get("gender")
            if g in ("M", "W", "U", "K"):
                r["gender_norm"] = g
            r["catalog_name"] = _WS.sub(" ", "%s %s" % (r.get("brand_norm", ""), pl)).strip()
            r["needs_llm"] = "0"
            n_done += 1
    left = len(cands) - n_done
    if left > 0:
        print("  [LLM] %d행 미보정(비용상한/캐시부재) — --llm-limit 상향 시 처리" % left)
    if own_cache:
        _cache_save(cache)
    return n_done


def apply_stage2(cats, drows, limit=0, api_key=None, cache=None):
    # v1: Stage1 보정으로 대표명 품질이 확보되므로 Stage2 게이트는 no-op 자리표시.
    # (그룹 과병합/과분할 의심 케이스가 확인되면 후속 확장.)
    return 0
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_catalog_llm_gate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 테스트 회귀 확인**

Run: `python3 -m pytest -v`
Expected: PASS (전체 통과 — lexicon/decompose/group/gate)

- [ ] **Step 6: 커밋**

```bash
git add identity/catalog_llm_gate.py identity/tests/test_catalog_llm_gate.py
git commit -m "feat(catalog): LLM 게이트(옵션) — 잔여 분해 보정, 캐시·비용상한"
```

---

### Task 7: 원샷 러너 + 실데이터 스모크 + 골든 샘플

**Files:**
- Create: `identity/run_catalog.py`
- Create: `identity/CATALOG_README.md`

**Interfaces:**
- Consumes: `catalog_decompose.run_stage1`, `catalog_group.run_stage2`
- Produces: CLI `python3 run_catalog.py` — Stage1→Stage2 순차 실행 + 요약. `--llm-gate` `--llm-limit N` `--limit N` 패스스루.

- [ ] **Step 1: run_catalog.py 작성**

`identity/run_catalog.py`:
```python
#!/usr/bin/env python3
"""카탈로그명 추출 원샷 러너: Stage1(분해) → Stage2(모델 묶음).

  python3 run_catalog.py                 # 규칙만(무료)
  python3 run_catalog.py --limit 500     # 골든 샘플
  python3 run_catalog.py --llm-gate --llm-limit 300   # 잔여 300행만 LLM 보정
"""
import os
import sys
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalog_decompose as cd
import catalog_group as cg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--llm-gate", action="store_true")
    ap.add_argument("--llm-limit", type=int, default=0)
    ap.add_argument("--dec-out", default=cd.OUT_DEFAULT)
    ap.add_argument("--cat-out", default=cg.OUT_DEFAULT)
    args = ap.parse_args()

    s1 = cd.run_stage1(cd.IN_DEFAULT, args.dec_out, args.limit, args.llm_gate, args.llm_limit)
    s2 = cg.run_stage2(args.dec_out, args.cat_out, args.llm_gate, args.llm_limit)
    print("─" * 50)
    print("완료 · 행 %d(needs_llm %d) → 카탈로그 %d" % (s1["rows"], s1["needs_llm"], s2["catalogs"]))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 500행 골든 샘플 실행(실데이터 스모크)**

Run: `python3 run_catalog.py --limit 500 --dec-out outputs/catalog_decomposed.sample.csv --cat-out outputs/catalogs.sample.csv`
Expected: `[Stage1] 500행 ...` → `[Stage2] 500행 → N 카탈로그 ...` → `완료 ...` (에러 없음)

- [ ] **Step 3: 골든 샘플 육안 검수**

Run: `python3 -c "import csv; [print(r['brand_norm'],'|',r['catalog_name'],'|',r['gender_norm'],'|',r['product_type']) for r in list(csv.DictReader(open('outputs/catalog_decomposed.sample.csv',encoding='utf-8-sig')))[:25]]"`
Expected: 25행의 `catalog_name`이 브랜드+정제상품명 형태이고 성별/색상 잡음이 제거돼 있음. 이상 패턴(빈 catalog_name, 브랜드 중복) 있으면 `catalog_lexicon.py` 사전 보강 후 재실행.

- [ ] **Step 4: CATALOG_README.md 작성**

`identity/CATALOG_README.md`:
```markdown
# 카탈로그명 추출 (스포츠/아웃도어 정형 → 카탈로그)

`outputs/all_brands.csv`(30 브랜드 공식몰 정형)에서 깨끗한 카탈로그명을 뽑고 모델 단위로 묶는다.

## 실행
```bash
python3 run_catalog.py                              # 규칙만(무료·결정적)
python3 run_catalog.py --limit 500 \
  --dec-out outputs/catalog_decomposed.sample.csv \
  --cat-out outputs/catalogs.sample.csv             # 골든 샘플
python3 run_catalog.py --llm-gate --llm-limit 300   # 잔여 하드케이스만 gpt-4o-mini 보정
```

## 산출
- `outputs/catalog_decomposed.csv` — 행별 분해(brand_norm·product_line·product_type·gender_norm·colorway·**catalog_name**·needs_llm)
- `outputs/catalogs.csv` — 모델 단위 카탈로그(대표 **catalog_name** + 색상/가격/사이즈 집계)

## 구조
- `catalog_lexicon.py` — 도메인 사전(브랜드·성별·유형·색상·style-code 접미사) 단일 출처
- `catalog_decompose.py` — Stage1(행별 분해). `decompose_row(row)`
- `catalog_group.py` — Stage2(모델 묶음). style-code base(브랜드별) 우선, 이름 폴백
- `catalog_llm_gate.py` — 옵션 LLM 보정(기본 OFF, 캐시·비용상한)

## 테스트
```bash
python3 -m pytest -v
```
```

- [ ] **Step 5: 커밋**

```bash
git add identity/run_catalog.py identity/CATALOG_README.md
git commit -m "feat(catalog): 원샷 러너 + 골든 샘플 + README"
```

> 주: 골든 샘플 산출(`outputs/*.sample.csv`)은 `.gitignore` 정책에 따라 커밋 제외 가능 — `outputs/`가 무시 대상인지 확인.

---

## Self-Review

**Spec coverage:**
- §4 아키텍처(2-스테이지·파일 리더) → Task 2/3(Stage1), Task 4/5(Stage2), Task 7(러너). ✅
- §5 Stage1 컬럼(brand_norm=source 슬러그·gender_norm·product_type·colorway·product_line·catalog_name·needs_llm) → Task 2 `decompose_row` + Task 2 테스트. ✅
- §6 Stage2(style-code 접미사 우선 + 이름 폴백, 색상 통합, 집계) → Task 4 `model_key`/`base_style_code`/`group`. ✅
- §7 LLM 게이트(기본 OFF·캐시·비용상한·무음절단 금지) → Task 6 `apply_stage1` + limit 보고 테스트. ✅
- §8 오류처리(입력 부재·오염값·빈 name·utf-8-sig·멱등) → Task 3 `run_stage1`(입력체크·빈name skip), 전 모듈 utf-8-sig. ✅
- §9 테스트(브랜드 관례별·style-code 묶음·이름 폴백·골든샘플) → Task 2/4 픽스처 + Task 7 샘플. ✅
- §3 확정사항(유형 유지·색상 통합 모델·CSV 2종·A코어+게이트옵션) → 전 태스크 반영. ✅

**Placeholder scan:** 모든 스텝에 실제 코드/명령/기대출력 포함. "TBD/TODO/적절히 처리" 없음. `apply_stage2`는 v1 no-op을 **의도적 자리표시로 명시**(스펙의 Stage2 게이트는 "의심 그룹만" 선택적 — v1 범위에서 Stage1 보정으로 충분, 후속 확장 주석). ✅

**Type consistency:** `OUT_COLS`(Task 2)와 Stage2 입력 키 일치, `decompose_row` 반환 키 == `OUT_COLS`, `group` 반환 키 == `GROUP_COLS`, `_key1`(Task 6)는 테스트/구현 동일 시그니처. `run_stage1`/`run_stage2` 반환 dict 키(`rows`/`needs_llm`/`catalogs`)가 Task 7 러너에서 참조하는 키와 일치. ✅
