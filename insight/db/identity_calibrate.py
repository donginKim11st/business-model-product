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


def cmd_recommend(args):
    db = gdb.get_db()
    labels = gdb.get_labels(db, args.category)
    if not labels:
        sys.exit(f"✗ DB 라벨 없음(category={args.category}) — 먼저 review/ingest")
    pos = sum(1 for d in labels if d["label"] == 1)
    print(f"[recommend] '{args.category}' DB 라벨 {len(labels)}건 (정답 {pos}/오답 {len(labels)-pos})")
    print(f"  {'임계':>5} {'P':>7} {'R':>7} {'F1':>7} {'TP':>4} {'FP':>4} {'FN':>4}")
    best_thr, best_f1, sweep_rows, precs = None, -1.0, [], []
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
        mark = ""
        if F1 > best_f1 or (F1 == best_f1 and best_thr is not None and thr > best_thr):
            best_thr, best_f1, mark = thr, F1, "  ←"
        print(f"  {thr:>5} {P:>7.1%} {R:>7.1%} {F1:>7.1%} {tp:>4} {fp_:>4} {fn:>4}{mark}")
    if len(labels) < 10:
        print(f"  ⚠ 라벨 {len(labels)}건 — 신뢰 위해 30+ 권장. 추천 잠정.")

    ineffective = (max(precs) - min(precs)) < 0.05
    best_row = next(r for r in sweep_rows if r["thr"] == best_thr)
    if ineffective:
        verdict, status, recommended = "needs_strong_key", "needs_strong_key", None
        print(f"\n  ⚠ 튜닝 무효: precision {max(precs):.0%} 평탄 — 변형충돌. 강키(color/style_code/barcode) 필요(옵션 C).")
    else:
        verdict, status, recommended = "effective", "effective", best_thr
        print(f"\n  추천 임계('{args.category}') = {best_thr} (F1 {best_f1:.1%})")

    # DB 기록: 가이드라인 메트릭 + 보정 이력(항상). name_thresh 활성값은 --apply 때만.
    gdb.upsert_guideline(db, args.category, status=status, recommended=recommended,
                         precision=best_row["p"], recall=best_row["r"], f1=best_row["f1"],
                         n_labels=len(labels), updated_by=args.by)
    gdb.add_calib_run(db, args.category, n_labels=len(labels), sweep=sweep_rows,
                      recommended=recommended, verdict=verdict, applied=bool(args.apply), by=args.by)

    if args.apply:
        gdb.upsert_guideline(db, args.category, name_thresh=recommended, updated_by=args.by)
        tm = gdb.export_thresh_json(db, THRESH_CFG)
        act = recommended if recommended is not None else "미설정(default)"
        print(f"  ✅ DB 가이드라인 name_thresh='{args.category}' → {act} · JSON export({len(tm)}개)")
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
