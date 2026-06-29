#!/usr/bin/env python3
"""
1번: 네이버 실데이터(outputs/naver_*.json)를 ER 엔진에 넣어 정밀 클러스터링.
임시 토큰 규칙(naver_dossier.py)과 달리 실제 파이프라인으로 몰 간 동일 SKU를 묶고
사이즈/카테고리 변형을 가른다 — 그리고 어디서 깨지는지 정직하게 드러낸다.

정책(price-monitoring 기준): '2개'·'3개입'·'1+1' 같은 수량은 '제품 정체성'이 아니라
'구매 단위'로 본다 → 클러스터링 전에 제목에서 수량 토큰을 제거하고 개당가로 정규화.

    python3 naver_resolve.py
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
sys.path.insert(0, HERE)

from pig.blocking import HybridBlocker
from pig.resolve import resolve
from pig.normalize import extract_attributes

QTY_RE = re.compile(r",?\s*(\d+)\s*개입?|(\d+)\s*매입?")
PLUS_RE = re.compile(r"(\d+)\s*\+\s*(\d+)")


def parse_qty_and_clean(title):
    """수량(구매단위) 추출 + 제목에서 제거. 반환 (clean_title, qty)."""
    qty = 1
    m = PLUS_RE.search(title)
    if m:
        qty = int(m.group(1)) + int(m.group(2))
        title = PLUS_RE.sub(" ", title)
    m = QTY_RE.search(title)
    if m:
        qty = max(qty, int(m.group(1) or m.group(2)))
        title = QTY_RE.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(" ,")
    return title, qty


def load_records():
    seen, recs = set(), []
    for path in glob.glob(os.path.join(OUT, "naver_*.json")):
        data = json.load(open(path, encoding="utf-8"))
        if not isinstance(data, list):  # skip our own outputs (crossmarket/resolved)
            continue
        for it in data:
            if not it.get("lprice"):
                continue
            key = (it["title"], it["mallName"], it["lprice"])
            if key in seen:
                continue
            seen.add(key)
            clean, qty = parse_qty_and_clean(it["title"])
            recs.append({
                "id": f"NV{len(recs):03d}", "title": clean, "raw_title": it["title"],
                "marketplace": it["mallName"] or "(미상)", "gtin": "",
                "price": it["lprice"], "qty": qty, "unit_price": round(it["lprice"] / qty),
                "mall": it["mallName"] or "(미상)",
            })
    return recs


def main():
    recs = load_records()
    by_id = {r["id"]: r for r in recs}
    run = resolve(recs, HybridBlocker(), cluster_guard=True)
    clusters = sorted(run["clusters"], key=lambda c: -len(c))

    multi = [c for c in clusters if len(c) > 1]
    print(f"리스팅 {len(recs)} → 클러스터 {len(clusters)} (다중몰 {len(multi)})")
    print("=" * 70)

    report = []
    for cl in multi[:14]:
        items = [by_id[i] for i in cl]
        a = extract_attributes(items[0])
        ups = sorted(items, key=lambda r: r["unit_price"])
        malls = sorted({r["mall"] for r in items})
        # 휴리스틱 품질 점검: 핵심 제품군 토큰이 섞였는지(오병합 의심)
        lines_tok = set()
        for r in items:
            for t in ("슈퍼마일드", "uv", "엑스퍼트", "아쿠아", "퍼펙트", "톤업", "리페어", "병풀", "pdrn", "피디알엔"):
                if t in r["title"].replace(" ", "").lower():
                    lines_tok.add(t)
        suspect = "  ⚠오병합의심(이종 라인 혼합)" if len(lines_tok) > 1 else ""
        print(f"\n[{len(items)}몰] cat={a['category']} size={a['size'] if 'size' in a else a.get('volume_ml')} "
              f"개당 {ups[0]['unit_price']:,}~{ups[-1]['unit_price']:,}원{suspect}")
        for r in ups[:6]:
            print(f"   {r['mall'][:14]:16} {r['price']:>7,}원 ×{r['qty']} = 개당 {r['unit_price']:,}  | {r['raw_title'][:38]}")
        report.append({"size_cat": f"{a['category']}/{a.get('volume_ml')}", "n_malls": len(items),
                       "unit_min": ups[0]["unit_price"], "unit_max": ups[-1]["unit_price"],
                       "suspect_mixed_lines": sorted(lines_tok),
                       "members": [{"mall": r["mall"], "unit_price": r["unit_price"], "title": r["raw_title"]} for r in ups]})

    singles = len(clusters) - len(multi)
    suspects = sum(1 for r in report if len(r["suspect_mixed_lines"]) > 1)
    print("\n" + "=" * 70)
    print(f"요약: {len(recs)} 리스팅 · {len(clusters)} 클러스터 · 단일 {singles} · 다중몰 {len(multi)}")
    print(f"상위 14개 중 이종라인 혼합(오병합) 의심: {suspects}개  ← 실데이터에서 엔진이 깨지는 지점")
    with open(os.path.join(OUT, "naver_resolved.json"), "w", encoding="utf-8") as f:
        json.dump({"n_listings": len(recs), "n_clusters": len(clusters),
                   "n_multi": len(multi), "top_clusters": report}, f, ensure_ascii=False, indent=2)
    print("저장: outputs/naver_resolved.json")


if __name__ == "__main__":
    main()
