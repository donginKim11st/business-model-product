#!/usr/bin/env python3
"""비정형 인사이트(products.catalogs[].insight) 규칙 검증 + 무조건 autofix.

규칙(RULES)을 products.catalogs[] 전체에 순회 적용한다. 각 규칙은 detect(ctx)로
위반을 판정하고 fix(ctx)로 mongo update 스펙을 반환한다. 프레임워크가 순회·리포트·
--dry-run·--rules 필터를 공통 처리한다. catalog_insight_backfill 등 무거운 의존은 import하지 않는다.

  INSIGHTS_DB=insights_demo python3 db/validate_insights.py --limit 500 --dry-run
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _af(ctlg_no):
    return [{"c.ctlg_no": ctlg_no}]


class Rule:
    def __init__(self, id, severity, detect, fix=None):
        self.id = id
        self.severity = severity
        self.detect = detect
        self.fix = fix


# --- R1: flag_drift -------------------------------------------------------
def detect_flag_drift(ctx):
    actual = bool((ctx["catalog"].get("insight") or {}).get("dims"))
    flag = bool(ctx["catalog"].get("has_insight"))
    if actual != flag:
        return f"has_insight={flag} but insight present={actual}"
    return None


def fix_flag_drift(ctx):
    actual = bool((ctx["catalog"].get("insight") or {}).get("dims"))
    return {"filter": {"_id": ctx["pkg_uid"]},
            "update": {"$set": {"catalogs.$[c].has_insight": actual}},
            "array_filters": _af(ctx["ctlg_no"])}


_KG = re.compile(r'(\d+(?:\.\d+)?)\s*kg', re.I)
_G = re.compile(r'(\d+(?:\.\d+)?)\s*g(?![a-z])', re.I)
_L = re.compile(r'(\d+(?:\.\d+)?)\s*l(?![a-z])', re.I)
_ML = re.compile(r'(\d+(?:\.\d+)?)\s*ml', re.I)
_COUNT = re.compile(r'(?:[x×]\s*)?(\d+)\s*(?:개입|개|입|팩|포|매)', re.I)
_COUNT_X = re.compile(r'[x×]\s*(\d+)', re.I)


def parse_qty(text):
    """자유 텍스트에서 질량(g)·부피(ml)·개수를 정규화 추출."""
    t = text or ""
    mass = vol = count = None
    m = _KG.search(t)
    if m:
        mass = float(m.group(1)) * 1000.0
    else:
        m = _G.search(t)
        if m:
            mass = float(m.group(1))
    m = _ML.search(t)
    if m:
        vol = float(m.group(1))
    else:
        m = _L.search(t)
        if m:
            vol = float(m.group(1)) * 1000.0
    m = _COUNT.search(t) or _COUNT_X.search(t)
    if m:
        count = int(m.group(1))
    return {"mass": mass, "vol": vol, "count": count}


def catalog_qty(catalog):
    """구조화된 size/count 우선, 없으면 disp 파싱."""
    q = parse_qty((catalog.get("size") or "") + " " + (catalog.get("count") or ""))
    if q["mass"] is None and q["vol"] is None and q["count"] is None:
        q = parse_qty(catalog.get("disp") or "")
    return q


# --- R2: source_mismatch -----------------------------------------------------
from collections import Counter

_TOL = 1e-9  # 질량/부피 동일성은 정규화 후 사실상 정확 비교(92 != 96 → mismatch)


def evidence_texts(insight):
    out = []
    for d in insight.get("dims") or []:
        for p in d.get("points") or []:
            for e in p.get("evidence") or []:
                out.append(f"{e.get('title', '')} {e.get('quote', '')}")
    for f in insight.get("faqs") or []:
        for e in (f.get("answer_evidence") or []) + (f.get("question_evidence") or []):
            out.append(f"{e.get('title', '')} {e.get('quote', '')}")
    return out


def _dominant(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None, 0.0
    val, n = Counter(vals).most_common(1)[0]
    return val, n / len(vals)


def detect_source_mismatch(ctx):
    ins = ctx["insight"] or {}
    if not (ins.get("dims")):          # 빈/부재 insight는 대상 아님
        return None
    cat = catalog_qty(ctx["catalog"])
    texts = evidence_texts(ins)
    if not texts:
        return None
    evq = [parse_qty(t) for t in texts]
    # 1) 휴리스틱: catalog에 값이 있고 evidence 다수(과반)가 명확히 다른 값이면 mismatch.
    for dim in ("mass", "vol"):   # count는 리뷰 문구 노이즈 → 파괴적 트리거에서 제외
        cv = cat.get(dim)
        if cv is None:
            continue
        dom, frac = _dominant([q[dim] for q in evq])
        if dom is not None and frac >= 0.5 and abs(dom - cv) > _TOL:
            return f"{dim}: catalog={cv} vs evidence dominant={dom} ({frac:.0%})"
    # 2) 애매(catalog 값에 대응하는 evidence 숫자가 전무) → LLM 게이트.
    has_evidence_qty = any(q["mass"] or q["vol"] or q["count"] for q in evq)
    if not has_evidence_qty and ctx["opts"].get("llm_gate"):
        gate = ctx["opts"].get("gate_fn")
        if gate is not None:
            try:
                if gate(ctx["disp"], texts) is False:
                    return "llm-gate: different product"
            except Exception:
                return None            # 게이트 실패 → 보수적 통과
    return None


def fix_source_mismatch(ctx):
    prev = (ctx["insight"] or {}).get("attempts") or 0
    empty = {"dims": [], "faqs": [], "n_sources": 0, "attempts": prev + 1,
             "invalidated": "source_mismatch", "fetched_at": now_iso(),
             "source": "naver_review"}
    return {"filter": {"_id": ctx["pkg_uid"]},
            "update": {"$set": {"catalogs.$[c].insight": empty,
                                "catalogs.$[c].has_insight": False}},
            "array_filters": _af(ctx["ctlg_no"])}


# --- R3: stale_schema (감지만, autofix 없음) --------------------------------
def detect_stale_schema(ctx):
    ins = ctx["insight"] or {}
    if not ins.get("dims"):            # 비어있지 않은 insight만 대상
        return None
    missing = [k for k in ("fetched_at", "source") if not ins.get(k)]
    if missing:
        return f"missing fields: {','.join(missing)}"
    return None


RULES = [
    Rule("flag_drift", "low", detect_flag_drift, fix_flag_drift),
    Rule("source_mismatch", "high", detect_source_mismatch, fix_source_mismatch),
    Rule("stale_schema", "low", detect_stale_schema, None),
]


def iter_contexts(db):
    for p in db.products.find({"type": "package"}, {"_id": 1, "catalogs": 1}):
        for c in p.get("catalogs") or []:
            if not c.get("ctlg_no"):
                continue
            yield {"db": db, "pkg_uid": p["_id"], "ctlg_no": c.get("ctlg_no"),
                   "disp": c.get("disp"), "catalog": c, "insight": c.get("insight"),
                   "opts": None}


def make_gate(model):
    """gpt-4o-mini 게이트 (disp, texts)->bool. 실패 시 예외를 올린다."""
    from openai import OpenAI
    client = OpenAI()

    def gate(disp, texts):
        joined = "\n".join(f"- {t[:200]}" for t in texts[:8])
        r = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "system",
                       "content": "제품명과 리뷰 근거가 같은 상품인지 판정. yes 또는 no만 답해라."},
                      {"role": "user",
                       "content": f"제품명: {disp}\n근거:\n{joined}\n\n같은 상품인가? yes/no"}])
        return r.choices[0].message.content.strip().lower().startswith("y")
    return gate


def run(db, opts):
    enabled = [r for r in RULES if not opts.get("rules") or r.id in opts["rules"]]
    limit = opts.get("limit") or 0
    violations = []
    summary = Counter()
    n = 0
    for ctx in iter_contexts(db):
        if limit and n >= limit:
            break
        n += 1
        ctx["opts"] = opts
        for rule in enabled:
            detail = rule.detect(ctx)
            if not detail:
                continue
            fixed = False
            if rule.fix and not opts.get("dry_run"):
                spec = rule.fix(ctx)
                db.products.update_one(spec["filter"], spec["update"],
                                       array_filters=spec["array_filters"])
                fixed = True
            summary[rule.id] += 1
            violations.append({"rule_id": rule.id, "severity": rule.severity,
                               "pkg_uid": ctx["pkg_uid"], "ctlg_no": ctx["ctlg_no"],
                               "disp": ctx["disp"], "detail": detail, "fixed": fixed})
    return {"summary": {"scanned": n, "by_rule": dict(summary),
                        "total": len(violations)},
            "violations": violations}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="처리 카탈로그 수(0=전체)")
    ap.add_argument("--dry-run", action="store_true", help="감지만, 수정 안 함")
    ap.add_argument("--rules", default="", help="쉼표구분 규칙 id (기본: 전체)")
    ap.add_argument("--llm-gate", dest="llm_gate", action="store_true", default=True)
    ap.add_argument("--no-llm-gate", dest="llm_gate", action="store_false")
    args = ap.parse_args()

    from pymongo import MongoClient
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]

    opts = {"limit": args.limit, "dry_run": args.dry_run,
            "rules": [r for r in args.rules.split(",") if r] or None,
            "llm_gate": args.llm_gate}
    if args.llm_gate and not args.dry_run and os.environ.get("OPENAI_API_KEY"):
        opts["gate_fn"] = make_gate(os.environ.get("INSIGHT_MODEL", "gpt-4o-mini"))

    rep = run(db, opts)

    here = os.path.dirname(os.path.abspath(__file__))
    exdir = os.path.join(here, "exports")
    os.makedirs(exdir, exist_ok=True)
    ts = now_iso().replace(":", "").replace("-", "")[:15]
    out = os.path.join(exdir, f"validation_report_{ts}.json")
    with open(out, "w", encoding="utf-8") as w:
        json.dump(rep, w, ensure_ascii=False, indent=2)
    rep["report"] = out
    print(json.dumps(rep["summary"] | {"report": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
