#!/usr/bin/env python3
"""identity 매칭 가이드라인 보정 루프 — run → validate → calibrate → repeat (사람 인더루프).

카테고리별 이름 임계(identity_name_thresh.json)를 정답 라벨로 보정한다. 정답 소스 2가지:
  · perturb : identity 산출 이름을 교란해 씨앗 생성, style_code 로 자동 라벨(무료). 같은 도메인
              (의류×의류)이라 지금 당장 임계 보정 가능 — 부트스트랩/검증용.
  · mongo   : insights_demo 의 해당 카테고리 실제 product → 씨앗. 사람이 검토 CSV 에 라벨(0/1).
              실 insight 데이터가 같은 도메인일 때(예: 의류 유입) 신뢰 가능한 보정.

서브커맨드:
  review    --category C [--source perturb|mongo] [--n 30] : 후보 매칭 샘플 → 검토 CSV(+perturb는 자동라벨 적재)
  ingest    --category C --file <검토csv>                  : 사람이 채운 label → identity_labels/<C>.jsonl 누적
  recommend --category C [--apply]                          : 라벨로 임계 sweep → P/R/F1 → 추천(+apply 시 config 기록)

  MONGO_URI=.. INSIGHTS_DB=insights_demo python3 db/identity_calibrate.py review --category 의류·신발 --source perturb --n 200
  python3 db/identity_calibrate.py recommend --category 의류·신발 --apply
"""
import os
import sys
import csv
import json
import random
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import food_price_backfill as fp
from identity_seed_match import _content_bigrams, _content_toks, domain_of, load_domain_map

LABELS_DIR = os.path.join(HERE, "identity_labels")
THRESH_CFG = os.path.join(HERE, "identity_name_thresh.json")
DEFAULT_EXTRACTED = os.path.join(os.path.dirname(os.path.dirname(HERE)), "identity", "outputs", "all_brands.csv")
SWEEP = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]


def _read_extracted(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _best(seed_disp, ext_bg):
    """seed content bigram 에 대해 최고 content recall 의 (idx, score)."""
    A = _content_bigrams(seed_disp)
    if not A:
        return None, 0.0
    bi, best = None, 0.0
    for i, bg in ext_bg:
        if bg:
            s = len(A & bg) / len(A)
            if s > best:
                bi, best = i, s
    return bi, best


def _perturb(name, rng):
    toks = (name or "").split()
    if len(toks) <= 2:
        return name
    keep = [t for t in toks if rng.random() > rng.uniform(0.3, 0.5)]
    return " ".join(keep if len(keep) >= 2 else toks[:2])


def _labels_path(cat):
    os.makedirs(LABELS_DIR, exist_ok=True)
    safe = cat.replace("/", "_").replace(" ", "_")
    return os.path.join(LABELS_DIR, f"{safe}.jsonl")


def _append_labels(cat, rows):
    path = _labels_path(cat)
    # dedup by (seed_disp, cand_name)
    seen = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                d = json.loads(line)
                seen.add((d["seed_disp"], d["cand_name"]))
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            k = (r["seed_disp"], r["cand_name"])
            if k in seen:
                continue
            seen.add(k)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n, path


def cmd_review(args):
    extracted = _read_extracted(args.extracted)
    ext_bg = [(i, _content_bigrams(r.get("name") or "")) for i, r in enumerate(extracted)]
    rng = random.Random(args.seed)

    rows = []
    if args.source == "perturb":
        pool = [i for i, r in enumerate(extracted) if r.get("style_code") and r.get("name")]
        rng.shuffle(pool)
        for i in pool[:args.n]:
            src = extracted[i]
            disp = _perturb(src["name"], rng)
            bi, score = _best(disp, ext_bg)
            if bi is None:
                continue
            cand = extracted[bi]
            label = 1 if cand.get("style_code") == src.get("style_code") else 0  # style_code 자동 정답
            rows.append({"seed_disp": disp, "cand_name": cand.get("name"), "cand_brand": cand.get("brand"),
                         "cand_style_code": cand.get("style_code"), "score": round(score, 3),
                         "label": label, "source": "perturb"})
        n, path = _append_labels(args.category, rows)
        print(f"[review/perturb] {len(rows)} 후보 자동라벨(style_code) → {n} 신규 적재 → {path}")
        pos = sum(1 for r in rows if r["label"] == 1)
        print(f"  정답쌍 {pos} · 오답쌍 {len(rows)-pos} (자동)")
    else:  # mongo: 실 product → 검토 CSV(사람 라벨)
        from pymongo import MongoClient
        db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
            os.environ.get("INSIGHTS_DB", "insights_demo")]
        prods = list(db.products.find({"category_l1": args.category, "type": "package"},
                                      {"keyword": 1}).limit(args.n))
        for p in prods:
            disp = p.get("keyword") or ""
            if len(_content_toks(disp)) < 1:
                continue
            bi, score = _best(disp, ext_bg)
            if bi is None:
                continue
            cand = extracted[bi]
            rows.append({"seed_disp": disp, "cand_name": cand.get("name"), "cand_brand": cand.get("brand"),
                         "cand_style_code": cand.get("style_code"), "score": round(score, 3),
                         "label": "", "source": "mongo"})
        out = args.file or os.path.join(LABELS_DIR, f"{args.category.replace('/','_').replace(' ','_')}.review.csv")
        os.makedirs(LABELS_DIR, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["seed_disp", "cand_name", "cand_brand", "cand_style_code",
                                              "score", "label", "source"])
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"[review/mongo] 검토 CSV {len(rows)}건 → {out}")
        print(f"  → label 열에 1(맞음)/0(틀림) 채운 뒤: identity_calibrate.py ingest --category '{args.category}' --file '{out}'")


def cmd_ingest(args):
    rows = []
    with open(args.file, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lab = (r.get("label") or "").strip()
            if lab not in ("0", "1"):
                continue
            r["label"] = int(lab)
            r["score"] = float(r.get("score") or 0)
            rows.append(r)
    n, path = _append_labels(args.category, rows)
    print(f"[ingest] 라벨 {len(rows)}건 중 {n} 신규 적재 → {path}")


def _load_labels(cat):
    path = _labels_path(cat)
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def cmd_recommend(args):
    labels = _load_labels(args.category)
    if not labels:
        sys.exit(f"✗ 라벨 없음: {_labels_path(args.category)} — 먼저 review/ingest")
    pos = sum(1 for d in labels if d["label"] == 1)
    print(f"[recommend] '{args.category}' 라벨 {len(labels)}건 (정답 {pos} / 오답 {len(labels)-pos})")
    print(f"  {'임계':>5} {'P':>7} {'R':>7} {'F1':>7} {'TP':>4} {'FP':>4} {'FN':>4}")
    best_thr, best_f1 = None, -1.0
    precs = []
    for thr in SWEEP:
        tp = sum(1 for d in labels if d["label"] == 1 and d["score"] >= thr)
        fp_ = sum(1 for d in labels if d["label"] == 0 and d["score"] >= thr)
        fn = sum(1 for d in labels if d["label"] == 1 and d["score"] < thr)
        P = tp / (tp + fp_) if (tp + fp_) else 0.0
        R = tp / (tp + fn) if (tp + fn) else 0.0
        F1 = 2 * P * R / (P + R) if (P + R) else 0.0
        precs.append(P)
        mark = ""
        if F1 > best_f1 or (F1 == best_f1 and best_thr is not None and thr > best_thr):
            best_thr, best_f1, mark = thr, F1, "  ←"
        print(f"  {thr:>5} {P:>7.1%} {R:>7.1%} {F1:>7.1%} {tp:>4} {fp_:>4} {fn:>4}{mark}")
    if len(labels) < 10:
        print(f"  ⚠ 라벨 {len(labels)}건 — 신뢰 위해 30+ 권장. 추천은 잠정.")

    # 평탄 sweep 감지: precision 이 임계로 안 갈리면 튜닝 무효(변별자 부족) — 강키 필요 신호.
    prec_range = max(precs) - min(precs)
    ineffective = prec_range < 0.05
    if ineffective:
        print(f"\n  ⚠ 튜닝 무효: precision {max(precs):.0%} 평탄(범위 {prec_range:.1%}) — 임계로 정답/오답 분리 불가.")
        print(f"    원인: 변형충돌(같은 이름 다른 style_code). 해법: 씨앗에 color/style_code/barcode(강키) 필요(옵션 C).")
        print(f"    → 이름 임계만으론 이 카테고리 precision 못 올림. 카테고리별 임계 미설정(default 사용) 권장.")
        if args.apply:
            cfg = json.load(open(THRESH_CFG, encoding="utf-8")) if os.path.exists(THRESH_CFG) else {}
            removed = cfg.pop(args.category, "없음")
            json.dump(cfg, open(THRESH_CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"  ✅ identity_name_thresh.json['{args.category}'] 제거(이전 {removed}) → default 사용")
        return
    print(f"\n  추천 임계('{args.category}') = {best_thr}  (F1 {best_f1:.1%})")
    if args.apply:
        cfg = json.load(open(THRESH_CFG, encoding="utf-8")) if os.path.exists(THRESH_CFG) else {}
        old = cfg.get(args.category)
        cfg[args.category] = best_thr
        json.dump(cfg, open(THRESH_CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  ✅ identity_name_thresh.json['{args.category}'] {old} → {best_thr} 기록")
    else:
        print("  (--apply 로 identity_name_thresh.json 에 기록)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("review", "ingest", "recommend"):
        s = sub.add_parser(name)
        s.add_argument("--category", required=True)
        s.add_argument("--extracted", default=DEFAULT_EXTRACTED)
        s.add_argument("--source", choices=["perturb", "mongo"], default="perturb")
        s.add_argument("--n", type=int, default=30)
        s.add_argument("--file", default=None)
        s.add_argument("--apply", action="store_true")
        s.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    {"review": cmd_review, "ingest": cmd_ingest, "recommend": cmd_recommend}[args.cmd](args)


if __name__ == "__main__":
    main()
