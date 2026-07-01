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
    A,B=d["A"],d["B"]
    def bar(v,grad):
        return f'<div style="height:30px;width:300px;background:#1a2230;border-radius:8px;overflow:hidden;display:inline-block;vertical-align:middle"><div style="height:100%;width:{v*100:.0f}%;background:{grad};border-radius:8px"></div></div>'
    G="linear-gradient(90deg,#7c5cff,#3f8cff)"; Gr="linear-gradient(90deg,#ff6b8a,#ff3d6e)"
    return f"""<!doctype html><html lang=ko><meta charset=utf-8><title>identity 매칭 전수 검증</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>:root{{--bg:#0a0e17;--panel:#121826;--panel2:#0f1420;--bd:#1e2737;--tx:#e8eef9;--mut:#7c8aa3}}
*{{box-sizing:border-box}}body{{font:14px/1.55 Inter,-apple-system,sans-serif;margin:0;color:var(--tx);min-height:100vh;
 background:radial-gradient(1200px 600px at 80% -10%,#16213a 0,transparent 60%),var(--bg)}}
.top{{display:flex;align-items:center;gap:12px;padding:22px 30px}}
.logo{{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#7c5cff,#3f8cff);box-shadow:0 6px 20px rgba(124,92,255,.45)}}
h1{{margin:0;font-size:17px;font-weight:700}}.sub{{color:var(--mut);font-size:12px}}
.wrap{{padding:6px 30px 30px;max-width:820px}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:16px}}
.kpi{{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--bd);border-radius:18px;padding:18px 20px}}
.kpi.feat{{background:linear-gradient(135deg,rgba(124,92,255,.22),rgba(63,140,255,.10)),var(--panel)}}
.kpi .lbl{{color:var(--mut);font-size:11.5px;text-transform:uppercase;letter-spacing:.6px}}
.kpi .val{{font-size:30px;font-weight:800;margin-top:6px;letter-spacing:-1px;font-variant-numeric:tabular-nums}}
.kpi .val.up{{background:linear-gradient(90deg,#22d3a5,#3f8cff);-webkit-background-clip:text;background-clip:text;color:transparent}}
.card{{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--bd);border-radius:18px;padding:20px 24px;margin-bottom:16px}}
.card h2{{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;margin:0 0 16px}}
.row{{display:flex;align-items:center;gap:16px;margin:14px 0}}.row .nm{{width:160px;font-size:13.5px;color:#cdd7e6}}.row b{{font-size:18px;width:54px;text-align:right;font-variant-numeric:tabular-nums}}
table{{border-collapse:separate;border-spacing:0;width:100%}}td,th{{padding:10px 12px;text-align:left;font-size:13px}}th{{color:var(--mut);font-size:11px;text-transform:uppercase}}td{{border-top:1px solid var(--bd);font-variant-numeric:tabular-nums}}
.mut{{color:var(--mut);font-size:12.5px;margin-top:8px}}.tag{{display:inline-block;background:#0e2a26;color:#22d3a5;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}}
</style>
<div class=top><div class=logo></div><div><h1>identity 매칭 전수 검증 (A/B)</h1>
 <div class=sub>산출 {d['ext']:,}행 · 표본 {d['n']:,} · 임계 {d['thr']} · 정답=style_code</div></div></div>
<div class=wrap>
 <div class=kpis>
  <div class=kpi><div class=lbl>A 베이스라인 precision</div><div class=val>{A['precision']*100:.0f}%</div></div>
  <div class="kpi feat"><div class=lbl>B · C1 precision</div><div class="val up">{B['precision']*100:.0f}%</div></div>
  <div class=kpi><div class=lbl>C1 상승폭</div><div class="val up">+{d['lift_p']*100:.0f}p</div></div>
 </div>
 <div class=card><h2>precision (정답률)</h2>
  <div class=row><div class=nm>A) 베이스라인(이름만)</div>{bar(A['precision'],Gr)}<b>{A['precision']*100:.0f}%</b></div>
  <div class=row><div class=nm>B) C1(색/사이즈 변별)</div>{bar(B['precision'],G)}<b>{B['precision']*100:.0f}%</b></div>
  <p class=mut>변형충돌(같은 이름 다른 style_code)을 색/사이즈로 해소 → OPT_NM 이 흐르면 얻는 실효.</p></div>
 <div class=card><h2>recall / 비용</h2>
  <table><tr><th>조건</th><th>recall</th><th>precision</th><th>건당</th></tr>
   <tr><td>A 베이스라인</td><td>{A['recall']*100:.0f}%</td><td>{A['precision']*100:.0f}%</td><td>{A['per_item_ms']}ms</td></tr>
   <tr><td>B C1</td><td>{B['recall']*100:.0f}%</td><td>{B['precision']*100:.0f}%</td><td>{B['per_item_ms']}ms</td></tr></table>
  <p class=mut>API 비용 <span class=tag>$0</span> (LLM 없음, 매칭=bigram+tie-break 순수 CPU). 전체 {d['full']:,}건 1회 ≈ <b>{d['full_min']}분</b>.</p></div>
</div></html>"""

if __name__ == "__main__":
    main()
