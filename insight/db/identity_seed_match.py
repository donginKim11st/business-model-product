#!/usr/bin/env python3
"""identity 산출 ↔ insight 씨앗 매칭 → insight_uid 스탬프 (T4).

canonical join 라운드트립의 마지막 조각: identity 가 카테고리별 추출기로 만든 정형 CSV
(brand/style_code/name/고시…)에 insight_uid(+ctlg_no)를 찍어 identity_backfill(T2)이
합류할 수 있게 한다. identity 추출기 본체는 불변 — 이 래퍼만 추가.

매칭(사용자 확정 = 강키 우선 + 이름 폴백):
  1) 강키: style_code/barcode 등이 씨앗·산출 양쪽에 있으면 그걸로 확정 매칭.
  2) 이름 폴백: 강키 없으면 disp↔name bigram recall(food_price.name_recall 재사용) >= 임계값.
  3) 임계 미달 = unmatched → uid 미스탬프 → 그 product 는 backfill 에서 status:empty.
틀린 uid 스탬프(정형 팩트 오착) 방지가 목적이라 매칭된 행만 출력한다.

category-agnostic: 산출 컬럼 집합을 가정하지 않는다(어떤 카테고리든 그대로 통과 + uid 부착).

  python3 db/identity_seed_match.py --seed identity/seeds/seed.csv \
      --extracted identity/outputs/all_brands.csv --out identity/outputs/all_brands_uid.csv
"""
import os
import sys
import csv
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import food_price_backfill as fp          # name_recall(catalog_name, candidate) 재사용(DRY)

DEFAULT_STRONG_KEYS = ("style_code", "barcode", "gtin")
DEFAULT_NAME_THRESH = 0.4                 # 식품 골드셋 보정값. 카테고리별 튜닝은 thresh_map 으로.

# 색상어 — 이름 매칭의 변별 근거에서 제외(색상 공유는 동일상품 신호가 아님).
# "그린"·"화이트" 단독 토큰이 아무 의류 이름에 1.0 으로 붙는 교차도메인 오탐 차단.
COLOR_WORDS = {
    "화이트", "블랙", "그린", "레드", "블루", "옐로우", "핑크", "골드", "실버", "그레이", "그래이",
    "네이비", "브라운", "베이지", "카키", "퍼플", "오렌지", "민트", "와인", "아이보리", "차콜",
    "버건디", "코랄", "라벤더", "터콰이즈", "크림", "탄", "올리브", "머스타드", "스카이",
    "white", "black", "green", "red", "blue", "yellow", "pink", "gold", "silver", "grey", "gray",
    "navy", "brown", "beige", "khaki", "purple", "orange", "mint", "wine", "ivory", "charcoal",
}


def _clean(v):
    return (v.strip() if isinstance(v, str) else v) or None


def _content_toks(text):
    """food_price 토큰화(용량/숫자 제거) 후 색상어까지 제거한 '변별 토큰'."""
    return [t for t in fp._nm_toks(text or "") if t not in COLOR_WORDS]


def _content_bigrams(text):
    s = "".join(_content_toks(text))
    return set(s[i:i + 2] for i in range(len(s) - 1)) if len(s) >= 2 else ({s} if s else set())


def _content_recall(a, b):
    """씨앗 변별 bigram 중 후보 이름에 존재하는 비율. 색상어 제외. 내용 없으면 0(=매칭 불가)."""
    A = _content_bigrams(a)
    if not A:
        return 0.0
    return len(A & _content_bigrams(b)) / len(A)


def _color_match(seed_color, row):
    """씨앗 색(OPT_NM 등) 이 후보 color 에 포함되면 True. 변형충돌 tie-break(C1)."""
    sc = (seed_color or "").strip().lower()
    return bool(sc) and sc in ((row.get("color") or "").lower())


def _size_match(seed_size, row):
    """씨앗 사이즈가 후보 sizes/size 에 포함되면 True. 변형 tie-break(C1)."""
    ss = (seed_size or "").strip().lower()
    return bool(ss) and ss in ((row.get("sizes") or row.get("size") or "").lower())


def domain_of(category, domain_map):
    """category(문자열) → 도메인. domain_map={도메인:[키워드…]}; 키워드가 부분일치하면 그 도메인.
    카테고리 게이트의 단일 해석 지점. 매핑 없거나 미일치면 None(=게이트 통과/판정 보류)."""
    if not category or not domain_map:
        return None
    for dom, kws in domain_map.items():
        if any(k in category for k in kws):
            return dom
    return None


def load_domain_map(path):
    """도메인 맵 JSON 로드. 예: {"식품":["식품","라면","음료"], "의류·신발":["의류","신발"]}. 없으면 None."""
    import json
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_thresh(category, thresh_map, default=DEFAULT_NAME_THRESH):
    """category_l1 → 이름 폴백 임계. thresh_map 에 카테고리 키 있으면 그 값,
    없으면 map 의 "default", 그것도 없으면 인자 default. 카테고리별 튜닝의 단일 해석 지점."""
    if thresh_map:
        if category in thresh_map:
            return thresh_map[category]
        if "default" in thresh_map:
            return thresh_map["default"]
    return default


def load_thresh_map(path):
    """카테고리별 임계 JSON 로드. 예: {"default":0.4, "식품":0.5, "신발":0.35}. 없으면 None."""
    import json
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    return {k: float(v) for k, v in m.items()}


def match_seed_to_extracted(seed_rows, extracted_rows, name_thresh=DEFAULT_NAME_THRESH,
                            strong_keys=DEFAULT_STRONG_KEYS, thresh_map=None,
                            domain_map=None, extracted_domain=None, min_content_toks=1):
    """순수 함수: 씨앗 행 × identity 산출 행 → uid 스탬프된 산출 행 list.

    각 씨앗은 최대 1개 산출에 매칭(강키 우선, 없으면 이름 최고점). 매칭된 산출 행을 복사해
    insight_uid/ctlg_no 를 찍고 _match(매칭 방식)를 기록. unmatched 씨앗은 출력 안 함.

    강키 매칭은 게이트를 우회(권위 있음). 이름 폴백에만 적용:
      · 카테고리 게이트: domain_of(씨앗 category_l1) 와 extracted_domain 이 둘 다 알려졌고 다르면 매칭 안 함.
        (교차도메인 오탐 차단. domain_map 없거나 미일치면 통과 — 관대.)
      · 색상어 가드: 색상어 제외 변별 토큰이 min_content_toks 미만이면 매칭 안 함. recall 도 색상어 제외 계산.
      · 임계는 씨앗 category_l1 별 thresh_map.
    T4 회귀 가드의 단위 검증 지점."""
    # 강키 인덱스: {key: {value: row}}
    strong = {k: {} for k in strong_keys}
    for r in extracted_rows:
        for k in strong_keys:
            v = _clean(r.get(k))
            if v is not None:
                strong[k].setdefault(v, r)
    out = []
    for s in seed_rows:
        matched = None
        method = None
        for k in strong_keys:                       # 1) 강키 우선
            v = _clean(s.get(k))
            if v is not None and v in strong[k]:
                matched, method = strong[k][v], f"key:{k}"
                break
        if matched is None:                          # 2) 이름 폴백(게이트 + 색상어 가드 + 카테고리별 임계)
            sd = domain_of(s.get("category_l1"), domain_map)
            if extracted_domain and sd and sd != extracted_domain:
                continue                             # 카테고리 게이트: 도메인 불일치 → 매칭 안 함(오탐 차단)
            disp = _clean(s.get("disp")) or _clean(s.get("keyword")) or ""
            if len(_content_toks(disp)) < min_content_toks:
                continue                             # 색상어 가드: 변별 토큰 부족(색상어뿐) → 매칭 안 함
            thr = resolve_thresh(s.get("category_l1"), thresh_map, name_thresh)
            seed_color, seed_size = _clean(s.get("color")), _clean(s.get("size"))
            # (recall, color_match, size_match) 사전식 — 같은 이름 변형충돌을 색/사이즈로 해소(C1).
            # recall 이 지배(상품군 결정), 동률일 때만 색→사이즈로 변형 선택. 강키 부재 시 precision 보강.
            best, best_key = None, (-1.0, 0, 0)
            for r in extracted_rows:
                score = _content_recall(disp, r.get("name") or "")
                key = (score, 1 if _color_match(seed_color, r) else 0, 1 if _size_match(seed_size, r) else 0)
                if key > best_key:
                    best, best_key = r, key
            if best is not None and best_key[0] >= thr:
                tag = "name" + ("+color" if best_key[1] else "") + ("+size" if best_key[2] else "")
                matched, method = best, f"{tag}:{best_key[0]:.2f}@{thr:g}"
        if matched is not None:
            out.append(dict(matched, insight_uid=s.get("insight_uid"),
                            ctlg_no=s.get("ctlg_no"), _match=method))
    return out


def _read_csv(path, encoding="utf-8-sig"):
    with open(path, newline="", encoding=encoding) as f:
        return list(csv.DictReader(f))


def run(seed_path, extracted_path, out_path, name_thresh=DEFAULT_NAME_THRESH,
        strong_keys=DEFAULT_STRONG_KEYS, thresh_map=None, domain_map=None, extracted_domain=None):
    seed = _read_csv(seed_path)
    extracted = _read_csv(extracted_path)              # identity CSV(BOM 가능) → utf-8-sig
    stamped = match_seed_to_extracted(seed, extracted, name_thresh, strong_keys, thresh_map,
                                      domain_map, extracted_domain)
    cols = list(extracted[0].keys()) if extracted else []
    for extra in ("insight_uid", "ctlg_no", "_match"):
        if extra not in cols:
            cols.append(extra)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in stamped:
            w.writerow(r)
    return len(seed), len(extracted), len(stamped)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default="identity/seeds/seed.csv")
    ap.add_argument("--extracted", default="identity/outputs/all_brands.csv")
    ap.add_argument("--out", default="identity/outputs/all_brands_uid.csv")
    ap.add_argument("--name-thresh", type=float, default=DEFAULT_NAME_THRESH,
                    help="이름 폴백 bigram recall 기본 임계(골드셋 0.4). 카테고리별은 --thresh-map")
    ap.add_argument("--thresh-map", default=os.environ.get("ID_THRESH_MAP", ""),
                    help='카테고리별 임계 JSON 경로. 예: {"default":0.4,"식품":0.5,"신발":0.35}')
    ap.add_argument("--strong-keys", default=",".join(DEFAULT_STRONG_KEYS),
                    help="강키 우선순위(콤마구분). 양쪽에 있으면 확정 매칭")
    ap.add_argument("--domain-map", default=os.environ.get("ID_DOMAIN_MAP", ""),
                    help='카테고리 게이트용 도메인 맵 JSON. 예: {"식품":["라면","음료"],"의류·신발":["의류"]}')
    ap.add_argument("--extracted-domain", default=os.environ.get("ID_EXTRACTED_DOMAIN", ""),
                    help="산출 CSV 의 도메인(예: 의류·신발). 씨앗 도메인과 다르면 매칭 차단")
    args = ap.parse_args()
    for p in (args.seed, args.extracted):
        if not os.path.exists(p):
            sys.exit(f"✗ 입력 없음: {p}")
    sk = tuple(k.strip() for k in args.strong_keys.split(",") if k.strip())
    tmap = load_thresh_map(args.thresh_map)
    dmap = load_domain_map(args.domain_map)
    edom = args.extracted_domain or None
    n_seed, n_ext, n_match = run(args.seed, args.extracted, args.out, args.name_thresh, sk, tmap,
                                 dmap, edom)
    print(f"매칭: 씨앗 {n_seed:,} × 산출 {n_ext:,} → uid 스탬프 {n_match:,}행 "
          f"(임계맵 {'O' if tmap else 'X'} · 게이트 {edom or 'X'}) → {args.out}")


if __name__ == "__main__":
    main()
