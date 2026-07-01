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
    actual = bool(ctx["catalog"].get("insight"))
    flag = bool(ctx["catalog"].get("has_insight"))
    if actual != flag:
        return f"has_insight={flag} but insight present={actual}"
    return None


def fix_flag_drift(ctx):
    actual = bool(ctx["catalog"].get("insight"))
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
