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


def _clean(v):
    return (v.strip() if isinstance(v, str) else v) or None


def match_seed_to_extracted(seed_rows, extracted_rows, name_thresh=0.4,
                            strong_keys=DEFAULT_STRONG_KEYS):
    """순수 함수: 씨앗 행 × identity 산출 행 → uid 스탬프된 산출 행 list.

    각 씨앗은 최대 1개 산출에 매칭(강키 우선, 없으면 이름 최고점). 매칭된 산출 행을 복사해
    insight_uid/ctlg_no 를 찍고 _match(매칭 방식)를 기록. unmatched 씨앗은 출력 안 함.
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
        if matched is None:                          # 2) 이름 폴백
            disp = _clean(s.get("disp")) or _clean(s.get("keyword")) or ""
            best, best_score = None, 0.0
            for r in extracted_rows:
                score = fp.name_recall(disp, r.get("name") or "")
                if score > best_score:
                    best, best_score = r, score
            if best is not None and best_score >= name_thresh:
                matched, method = best, f"name:{best_score:.2f}"
        if matched is not None:
            out.append(dict(matched, insight_uid=s.get("insight_uid"),
                            ctlg_no=s.get("ctlg_no"), _match=method))
    return out


def _read_csv(path, encoding="utf-8-sig"):
    with open(path, newline="", encoding=encoding) as f:
        return list(csv.DictReader(f))


def run(seed_path, extracted_path, out_path, name_thresh=0.4, strong_keys=DEFAULT_STRONG_KEYS):
    seed = _read_csv(seed_path)
    extracted = _read_csv(extracted_path)              # identity CSV(BOM 가능) → utf-8-sig
    stamped = match_seed_to_extracted(seed, extracted, name_thresh, strong_keys)
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
    ap.add_argument("--name-thresh", type=float, default=0.4, help="이름 폴백 bigram recall 임계(골드셋 0.4)")
    ap.add_argument("--strong-keys", default=",".join(DEFAULT_STRONG_KEYS),
                    help="강키 우선순위(콤마구분). 양쪽에 있으면 확정 매칭")
    args = ap.parse_args()
    for p in (args.seed, args.extracted):
        if not os.path.exists(p):
            sys.exit(f"✗ 입력 없음: {p}")
    sk = tuple(k.strip() for k in args.strong_keys.split(",") if k.strip())
    n_seed, n_ext, n_match = run(args.seed, args.extracted, args.out, args.name_thresh, sk)
    print(f"매칭: 씨앗 {n_seed:,} × 산출 {n_ext:,} → uid 스탬프 {n_match:,}행 "
          f"(미매칭 씨앗은 backfill 에서 status:empty) → {args.out}")


if __name__ == "__main__":
    main()
