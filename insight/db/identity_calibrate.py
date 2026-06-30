#!/usr/bin/env python3
"""identity 매칭 가이드라인 보정 루프 — run → validate → calibrate → repeat (사람 인더루프).

보정값/라벨/이력은 Mongo(identity_guidelines_db) 가 source of truth. identity_name_thresh.json 은
DB export(매처 캐시). 정답 소스 2가지:
  · perturb : identity 산출 이름 교란 → 씨앗, style_code 자동 라벨(무료). 같은 도메인 보정/부트스트랩.
  · mongo   : insights_demo 의 해당 카테고리 실 product → 씨앗. 사람이 검토 CSV 에 라벨(0/1).

서브커맨드:
  review    --category C [--source perturb|mongo] [--n N] : 후보 샘플 → perturb는 DB 자동라벨 / mongo는 검토 CSV
  ingest    --category C --file <csv> [--by 이름]          : 사람이 채운 label → DB 라벨 누적(overwrite)
  recommend --category C [--apply] [--by 이름]              : DB 라벨로 sweep → P/R/F1 → DB 가이드라인/이력 기록(+apply export)

  MONGO_URI=.. INSIGHTS_DB=insights_demo python3 db/identity_calibrate.py review --category 의류·신발 --source perturb --n 200
  python3 db/identity_calibrate.py recommend --category 의류·신발 --apply
"""
import os
import sys
import csv
import random
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from identity_seed_match import _content_bigrams, _content_toks
import identity_guidelines_db as gdb

THRESH_CFG = os.path.join(HERE, "identity_name_thresh.json")
DEFAULT_EXTRACTED = os.path.join(os.path.dirname(os.path.dirname(HERE)), "identity", "outputs", "all_brands.csv")
REVIEW_DIR = os.path.join(HERE, "identity_labels")
SWEEP = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]


def _read_extracted(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _best(seed_disp, ext_bg):
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


def cmd_review(args):
    db = gdb.get_db()
    extracted = _read_extracted(args.extracted)
    ext_bg = [(i, _content_bigrams(r.get("name") or "")) for i, r in enumerate(extracted)]
    rng = random.Random(args.seed)

    if args.source == "perturb":
        pool = [i for i, r in enumerate(extracted) if r.get("style_code") and r.get("name")]
        rng.shuffle(pool)
        rows = []
        for i in pool[:args.n]:
            src = extracted[i]
            disp = _perturb(src["name"], rng)
            bi, score = _best(disp, ext_bg)
            if bi is None:
                continue
            cand = extracted[bi]
            rows.append({"seed_disp": disp, "seed_uid": None, "cand_name": cand.get("name"),
                         "cand_brand": cand.get("brand"), "cand_style_code": cand.get("style_code"),
                         "score": round(score, 3),
                         "label": 1 if cand.get("style_code") == src.get("style_code") else 0,
                         "source": "perturb"})
        n = gdb.add_labels(db, args.category, rows, source="perturb", by="auto")
        pos = sum(1 for r in rows if r["label"] == 1)
        print(f"[review/perturb] 후보 {len(rows)} 자동라벨(style_code) → DB 신규 {n} (정답 {pos}/오답 {len(rows)-pos})")
    else:  # mongo → 검토 CSV(사람 라벨)
        prods = list(db.products.find({"category_l1": args.category, "type": "package"},
                                      {"keyword": 1}).limit(args.n))
        rows = []
        for p in prods:
            disp = p.get("keyword") or ""
            if len(_content_toks(disp)) < 1:
                continue
            bi, score = _best(disp, ext_bg)
            if bi is None:
                continue
            cand = extracted[bi]
            rows.append({"seed_uid": p["_id"], "seed_disp": disp, "cand_name": cand.get("name"),
                         "cand_brand": cand.get("brand"), "cand_style_code": cand.get("style_code"),
                         "score": round(score, 3), "label": "", "source": "mongo"})
        os.makedirs(REVIEW_DIR, exist_ok=True)
        out = args.file or os.path.join(REVIEW_DIR, f"{args.category.replace('/','_').replace(' ','_')}.review.csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["seed_uid", "seed_disp", "cand_name", "cand_brand",
                                              "cand_style_code", "score", "label", "source"])
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"[review/mongo] 검토 CSV {len(rows)}건 → {out}")
        print(f"  → label 열 1(맞음)/0(틀림) 채운 뒤: identity_calibrate.py ingest --category '{args.category}' --file '{out}'")


def cmd_ingest(args):
    db = gdb.get_db()
    rows = []
    with open(args.file, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lab = (r.get("label") or "").strip()
            if lab not in ("0", "1"):
                continue
            r["label"] = int(lab)
            rows.append(r)
    n = gdb.add_labels(db, args.category, rows, source="human", by=args.by, overwrite=True)
    print(f"[ingest] 사람 라벨 {len(rows)}건 → DB {n} 반영 (by={args.by})")


def compute_recommendation(labels):
    """순수 함수: 라벨 list → sweep/판정/추천. API·CLI 공유. 평탄 sweep → needs_strong_key."""
    sweep_rows, precs, best_thr, best_f1 = [], [], None, -1.0
    for thr in SWEEP:
        tp = sum(1 for d in labels if d["label"] == 1 and d["score"] >= thr)
        fp_ = sum(1 for d in labels if d["label"] == 0 and d["score"] >= thr)
        fn = sum(1 for d in labels if d["label"] == 1 and d["score"] < thr)
        P = tp / (tp + fp_) if (tp + fp_) else 0.0
        R = tp / (tp + fn) if (tp + fn) else 0.0
        F1 = 2 * P * R / (P + R) if (P + R) else 0.0
        precs.append(P)
        sweep_rows.append({"thr": thr, "p": round(P, 3), "r": round(R, 3), "f1": round(F1, 3),
                           "tp": tp, "fp": fp_, "fn": fn})
        if F1 > best_f1 or (F1 == best_f1 and best_thr is not None and thr > best_thr):
            best_thr, best_f1 = thr, F1
    ineffective = (max(precs) - min(precs)) < 0.05
    best_row = next(r for r in sweep_rows if r["thr"] == best_thr)
    verdict = "needs_strong_key" if ineffective else "effective"
    return {"n_labels": len(labels), "pos": sum(1 for d in labels if d["label"] == 1),
            "sweep": sweep_rows, "best_thr": best_thr, "best_f1": best_f1, "best_row": best_row,
            "ineffective": ineffective, "verdict": verdict, "status": verdict,
            "recommended": None if ineffective else best_thr}


def recommend_core(db, category, apply=False, by="api"):
    """라벨 → 추천 계산 + DB 기록(가이드라인 메트릭 + 보정이력). apply 시 활성 임계 반영 + JSON export.
    API·CLI 공유. 반환=compute_recommendation dict + {applied}."""
    labels = gdb.get_labels(db, category)
    if not labels:
        return {"error": "no_labels", "category": category}
    rec = compute_recommendation(labels)
    br = rec["best_row"]
    gdb.upsert_guideline(db, category, status=rec["status"], recommended=rec["recommended"],
                         precision=br["p"], recall=br["r"], f1=br["f1"],
                         n_labels=rec["n_labels"], updated_by=by)
    gdb.add_calib_run(db, category, n_labels=rec["n_labels"], sweep=rec["sweep"],
                      recommended=rec["recommended"], verdict=rec["verdict"], applied=bool(apply), by=by)
    if apply:
        gdb.upsert_guideline(db, category, name_thresh=rec["recommended"], updated_by=by)
        gdb.export_thresh_json(db, THRESH_CFG)
    rec["applied"] = bool(apply)
    return rec


def cmd_recommend(args):
    db = gdb.get_db()
    labels = gdb.get_labels(db, args.category)
    if not labels:
        sys.exit(f"✗ DB 라벨 없음(category={args.category}) — 먼저 review/ingest")
    rec = recommend_core(db, args.category, apply=args.apply, by=args.by)
    print(f"[recommend] '{args.category}' DB 라벨 {rec['n_labels']}건 (정답 {rec['pos']}/오답 {rec['n_labels']-rec['pos']})")
    print(f"  {'임계':>5} {'P':>7} {'R':>7} {'F1':>7} {'TP':>4} {'FP':>4} {'FN':>4}")
    for s in rec["sweep"]:
        mark = "  ←" if s["thr"] == rec["best_thr"] else ""
        print(f"  {s['thr']:>5} {s['p']:>7.1%} {s['r']:>7.1%} {s['f1']:>7.1%} {s['tp']:>4} {s['fp']:>4} {s['fn']:>4}{mark}")
    if rec["n_labels"] < 10:
        print(f"  ⚠ 라벨 {rec['n_labels']}건 — 신뢰 위해 30+ 권장. 추천 잠정.")
    if rec["ineffective"]:
        print(f"\n  ⚠ 튜닝 무효: precision {rec['best_row']['p']:.0%} 평탄 — 변형충돌. 강키(color/style_code/barcode) 필요(옵션 C).")
    else:
        print(f"\n  추천 임계('{args.category}') = {rec['best_thr']} (F1 {rec['best_f1']:.1%})")
    if args.apply:
        act = rec["recommended"] if rec["recommended"] is not None else "미설정(default)"
        print(f"  ✅ DB 가이드라인 name_thresh='{args.category}' → {act} · JSON export")
    else:
        print("  (DB 이력/메트릭 기록됨. --apply 로 활성 임계 반영 + JSON export)")


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
        s.add_argument("--by", default=os.environ.get("USER", "unknown"))
        s.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    {"review": cmd_review, "ingest": cmd_ingest, "recommend": cmd_recommend}[args.cmd](args)


if __name__ == "__main__":
    main()
