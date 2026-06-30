#!/usr/bin/env python3
"""identity 매칭 정확도 평가 (단위테스트 아님 — 실데이터 측정 하니스).

기계동작이 아니라 '맞는 것끼리 붙는가/오매칭 없는가'를 실측한다. 두 평가:

  A) 교차도메인 오탐(false positive): 식품 씨앗(insights_demo) × 의류 산출(all_brands.csv).
     두 도메인은 안 겹치므로 매칭은 ~0 이어야 정상. 붙으면 그 점수와 함께 보고(오탐 위험).

  B) 도메인 내 정확도: 실제 의류 이름을 교란(토큰 일부 제거)해 씨앗을 만들고, 정답(style_code)
     대비 임계별 recall(매칭률)·precision(맞춘 비율)·variant 충돌(같은 이름 다른 style_code) 측정.
     이름 폴백만으로 색/사이즈 변형을 구분 못하는 한계를 수치로 드러낸다.

  MONGO_URI=.. INSIGHTS_DB=insights_demo python3 db/eval_identity_match.py \
      [--extracted identity/outputs/all_brands.csv] [--n-food 200] [--n-appar 300]
"""
import os
import sys
import csv
import random
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import food_price_backfill as fp
from identity_seed_match import resolve_thresh, load_thresh_map


def _read_extracted(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _best_match(seed_bg, ext_bg):
    """seed bigram set 에 대해 최고 recall 의 (index, score). ext_bg=[(idx,bigramset)]."""
    if not seed_bg:
        return None, 0.0
    best_i, best = None, 0.0
    for i, bg in ext_bg:
        if not bg:
            continue
        score = len(seed_bg & bg) / len(seed_bg)
        if score > best:
            best_i, best = i, score
    return best_i, best


def eval_cross_domain(db, extracted, ext_bg, tmap, n_food, default_thresh):
    """A) 식품 씨앗 × 의류 산출 → 매칭(오탐) 수. 0 이어야 정상."""
    foods = list(db.products.find({"type": "package"}, {"keyword": 1, "category_l1": 1}).limit(n_food))
    fp_hits = []
    for p in foods:
        disp = p.get("keyword") or ""
        thr = resolve_thresh(p.get("category_l1"), tmap, default_thresh)
        bi, score = _best_match(fp._nm_bigrams(disp), ext_bg)
        if bi is not None and score >= thr:
            fp_hits.append((disp, extracted[bi].get("name"), extracted[bi].get("brand"), score, thr))
    print(f"\n[A] 교차도메인 오탐 — 식품 씨앗 {len(foods)} × 의류 산출 {len(extracted):,}")
    print(f"    오탐 매칭: {len(fp_hits)}건  (0 이어야 정상)")
    for disp, name, brand, score, thr in sorted(fp_hits, key=lambda x: -x[3])[:8]:
        print(f"      ⚠ '{disp}' ↔ [{brand}] '{name}' score={score:.2f} (임계 {thr})")
    return len(fp_hits), len(foods)


def _perturb(name, rng):
    """의류 이름 → insight 키워드처럼 교란(토큰 30~50% 제거, 순서 유지). 최소 2토큰."""
    toks = (name or "").split()
    if len(toks) <= 2:
        return name
    keep = [t for t in toks if rng.random() > rng.uniform(0.3, 0.5)]
    if len(keep) < 2:
        keep = toks[:2]
    return " ".join(keep)


def eval_in_domain(extracted, ext_bg, n_appar, threshes, rng):
    """B) 의류 자기평가: 교란 씨앗 → 정답 style_code 대비 임계별 recall/precision."""
    pool = [i for i, r in enumerate(extracted) if (r.get("style_code") and r.get("name"))]
    rng.shuffle(pool)
    sample = pool[:n_appar]
    # 같은 이름이 여러 style_code 로 존재하는 변형 충돌 비율(이름만으로 구분 불가한 비중)
    from collections import Counter
    name_counts = Counter((extracted[i].get("name") or "").strip() for i in range(len(extracted)))

    print(f"\n[B] 도메인 내 정확도 — 의류 교란 씨앗 {len(sample)} (정답=style_code)")
    print(f"    {'임계':>6} {'recall':>8} {'precision':>10} {'변형충돌':>9}")
    results = {}
    for thr in threshes:
        matched = correct = variant_collision = 0
        for i in sample:
            src = extracted[i]
            seed = _perturb(src.get("name"), rng)
            bi, score = _best_match(fp._nm_bigrams(seed), ext_bg)
            if bi is None or score < thr:
                continue
            matched += 1
            if extracted[bi].get("style_code") == src.get("style_code"):
                correct += 1
            elif (extracted[bi].get("name") or "").strip() == (src.get("name") or "").strip():
                variant_collision += 1   # 같은 이름 다른 style_code(색/사이즈 변형) → 이름만으론 구분 불가
        recall = matched / len(sample) if sample else 0
        precision = correct / matched if matched else 0
        vc = variant_collision / matched if matched else 0
        results[thr] = (recall, precision, vc)
        print(f"    {thr:>6} {recall:>7.1%} {precision:>9.1%} {vc:>8.1%}")
    # 변형 다발 카탈로그 진단
    dup_names = sum(1 for n, c in name_counts.items() if n and c > 1)
    print(f"    (변형 다발: 동일 이름이 2+ style_code 인 이름 {dup_names:,}개 → 이름 폴백의 구조적 상한)")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extracted", default="identity/outputs/all_brands.csv")
    ap.add_argument("--thresh-map", default="db/identity_name_thresh.json")
    ap.add_argument("--n-food", type=int, default=200)
    ap.add_argument("--n-appar", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not os.path.exists(args.extracted):
        sys.exit(f"✗ 산출 CSV 없음: {args.extracted}")
    extracted = _read_extracted(args.extracted)
    ext_bg = [(i, fp._nm_bigrams(r.get("name") or "")) for i, r in enumerate(extracted)]
    tmap = load_thresh_map(args.thresh_map)
    rng = random.Random(args.seed)
    print(f"산출 {len(extracted):,}행 · 임계맵 {tmap}")

    from pymongo import MongoClient
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights_demo")]

    fp_hits, n_food = eval_cross_domain(db, extracted, ext_bg, tmap, args.n_food, 0.4)
    eval_in_domain(extracted, ext_bg, args.n_appar, [0.3, 0.4, 0.5, 0.6], rng)

    print("\n요약:")
    print(f"  · 교차도메인 오탐률: {fp_hits}/{n_food} = {fp_hits/max(1,n_food):.1%} (낮을수록 좋음)")
    print("  · 도메인 내: precision 이 낮으면 색/사이즈 변형을 이름만으로 못 가림 → 강키(style_code/색) 필요 신호")


if __name__ == "__main__":
    main()
