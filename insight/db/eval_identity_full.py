#!/usr/bin/env python3
"""identity 매칭 전수 검증 + 건당 비용 (A/B: 베이스라인 vs C1 색·사이즈 변별).

추출된 identity 데이터(all_brands.csv) 전수에서 교란 씨앗을 만들고 style_code 정답 대비
precision/recall 을 측정한다. 두 조건:
  A) 베이스라인 : 씨앗 = 교란 이름만(현 식품 스코프와 동일 = 변별자 없음).
  B) C1         : 씨앗 = 교란 이름 + 정답 행의 color/size(OPT_NM 흐른 상태 시뮬). tie-break 발동.
B 의 precision 상승폭 = C1-data(OPT_NM)가 흐르면 얻는 실효. 비용은 매칭 wall-clock/건(API=$0).

  python3 db/eval_identity_full.py [--n 2000] [--out db/exports/identity_validation.html]
"""
import os
import sys
import csv
import time
import json
import random
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from identity_seed_match import _content_bigrams, _content_recall, _color_match, _size_match
import food_price_backfill as fp  # noqa

DEFAULT_EXT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "identity", "outputs", "all_brands.csv")


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _perturb(name, rng):
    toks = (name or "").split()
    if len(toks) <= 2:
        return name
    keep = [t for t in toks if rng.random() > rng.uniform(0.3, 0.5)]
    return " ".join(keep if len(keep) >= 2 else toks[:2])


def _match(seed_bg, seed_color, seed_size, ext, ext_bg, thr):
    """프로덕션 사전식 tie-break 미러(속도 위해 bigram 사전계산). → (idx, recall) or (None,0)."""
    best_i, best_key = None, (-1.0, 0, 0)
    for i, bg in ext_bg:
        if not bg:
            continue
        score = len(seed_bg & bg) / len(seed_bg) if seed_bg else 0.0
        key = (score, 1 if _color_match(seed_color, ext[i]) else 0,
               1 if _size_match(seed_size, ext[i]) else 0)
        if key > best_key:
            best_i, best_key = i, key
    return (best_i, best_key[0]) if best_key[0] >= thr else (None, best_key[0])


def run_condition(sample, ext, ext_bg, use_variant, thr, rng):
    matched = correct = 0
    t0 = time.time()
    for i in sample:
        src = ext[i]
        disp = _perturb(src["name"], rng)
        sbg = _content_bigrams(disp)
        sc = src.get("color") if use_variant else None
        ss = (src.get("sizes") or "").split("|")[0] if use_variant else None
        bi, score = _match(sbg, sc, ss, ext, ext_bg, thr)
        if bi is None:
            continue
        matched += 1
        if ext[bi].get("style_code") == src.get("style_code"):
            correct += 1
    dt = time.time() - t0
    return {"matched": matched, "correct": correct, "n": len(sample),
            "recall": matched / len(sample) if sample else 0,
            "precision": correct / matched if matched else 0,
            "secs": round(dt, 1), "per_item_ms": round(dt / max(1, len(sample)) * 1000, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extracted", default=DEFAULT_EXT)
    ap.add_argument("--n", type=int, default=2000, help="검증 표본(교란 씨앗) 수")
    ap.add_argument("--thr", type=float, default=0.4)
    ap.add_argument("--out", default=os.path.join(HERE, "exports", "identity_validation.html"))
    args = ap.parse_args()

    ext = _read(args.extracted)
    t0 = time.time()
    ext_bg = [(i, _content_bigrams(r.get("name") or "")) for i, r in enumerate(ext)]
    prep = time.time() - t0
    rng = random.Random(42)
    pool = [i for i, r in enumerate(ext) if r.get("style_code") and r.get("name")]
    rng.shuffle(pool)
    sample = pool[:args.n]
    print(f"전수 검증: 산출 {len(ext):,}행 · 표본 {len(sample):,} · 임계 {args.thr} · bigram 사전계산 {prep:.1f}s")
    A = run_condition(sample, ext, ext_bg, use_variant=False, thr=args.thr, rng=random.Random(42))
    B = run_condition(sample, ext, ext_bg, use_variant=True, thr=args.thr, rng=random.Random(42))
    print(f"  {'조건':<22}{'recall':>9}{'precision':>11}{'건당ms':>9}")
    print(f"  {'A) 베이스라인(이름만)':<20}{A['recall']:>9.1%}{A['precision']:>11.1%}{A['per_item_ms']:>9}")
    print(f"  {'B) C1(색/사이즈)':<22}{B['recall']:>9.1%}{B['precision']:>11.1%}{B['per_item_ms']:>9}")
    lift = B["precision"] - A["precision"]
    print(f"  → C1 precision 상승: {A['precision']:.1%} → {B['precision']:.1%} (+{lift*100:.0f}p)")
    full = 34095  # insight 우주 product 수(현재)
    print(f"  비용: API=$0(LLM 없음) · 매칭 {A['per_item_ms']}ms/건 · 전체 {full:,}건 1회 ≈ {A['per_item_ms']*full/1000/60:.0f}분(=$0)")

    # 결과 → HTML
    data = {"ext": len(ext), "n": len(sample), "thr": args.thr, "A": A, "B": B,
            "lift_p": round(lift, 3), "full": full,
            "per_item_ms": A["per_item_ms"], "full_min": round(A["per_item_ms"] * full / 1000 / 60, 1)}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(_html(data))
    print(f"  검증 대시보드 → {args.out}")


def _html(d):
    bar = lambda v, c: f'<div style="background:#eef0f2;border-radius:6px;height:26px;width:240px"><div style="background:{c};height:100%;width:{v*100:.0f}%;border-radius:6px"></div></div>'
    return f"""<!doctype html><html lang=ko><meta charset=utf-8><title>identity 매칭 전수 검증</title>
<style>body{{font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f7f9;color:#1c1e21}}
header{{background:#1c2733;color:#fff;padding:16px 24px}}header h1{{margin:0;font-size:18px}}.sub{{opacity:.7;font-size:12px}}
.wrap{{padding:22px;max-width:760px}}.card{{background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:20px 24px;margin-bottom:16px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:10px 12px;border-bottom:1px solid #eef0f2;text-align:left}}
th{{font-size:12px;color:#65676b}}.big{{font-size:30px;font-weight:700}}.lift{{color:#1a7f37}}.mut{{color:#65676b;font-size:13px}}
.row{{display:flex;align-items:center;gap:14px;margin:10px 0}}</style>
<header><h1>identity 매칭 전수 검증 (A/B)</h1><div class=sub>산출 {d['ext']:,}행 · 표본 {d['n']:,} · 임계 {d['thr']} · 정답=style_code</div></header>
<div class=wrap>
 <div class=card><b>precision (정답률)</b>
  <div class=row><div style=width:170px>A) 베이스라인(이름만)</div>{bar(d['A']['precision'],'#b42318')}<b>{d['A']['precision']*100:.0f}%</b></div>
  <div class=row><div style=width:170px>B) C1(색/사이즈 변별)</div>{bar(d['B']['precision'],'#1a7f37')}<b>{d['B']['precision']*100:.0f}%</b></div>
  <p class=lift><b>C1 효과: +{d['lift_p']*100:.0f}p</b> (변형충돌을 색/사이즈로 해소). OPT_NM 이 흐르면 얻는 실효.</p></div>
 <div class=card><b>recall / 비용</b>
  <table><tr><th>조건</th><th>recall</th><th>precision</th><th>건당</th></tr>
   <tr><td>A 베이스라인</td><td>{d['A']['recall']*100:.0f}%</td><td>{d['A']['precision']*100:.0f}%</td><td>{d['A']['per_item_ms']}ms</td></tr>
   <tr><td>B C1</td><td>{d['B']['recall']*100:.0f}%</td><td>{d['B']['precision']*100:.0f}%</td><td>{d['B']['per_item_ms']}ms</td></tr></table>
  <p class=mut>API 비용 <b>$0</b> (LLM 없음, 매칭=bigram+tie-break 순수 CPU). 전체 {d['full']:,}건 1회 매칭 ≈ <b>{d['full_min']}분</b> (=$0).</p></div>
</div></html>"""


if __name__ == "__main__":
    main()
