#!/usr/bin/env python3
"""
네이버 쇼핑검색 API 실데이터(outputs/naver_*.json)로 크로스마켓 도시에 생성.
개당가(per-unit) 정규화 + 몰별 비교 + 동일제품 후보 가격 스프레드.

    python3 naver_pull.py 싸이닉 "싸이닉 토너" "싸이닉 선에센스"   # 먼저 수집
    python3 naver_dossier.py                                  # 도시에 생성
"""
import glob
import html
import json
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
PACK_RE = re.compile(r"(\d+)\s*개(?!월)")
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|g)\b", re.I)


def load_items():
    seen, items = set(), []
    for path in glob.glob(os.path.join(OUT, "naver_*.json")):
        if path.endswith("crossmarket.json"):
            continue
        for it in json.load(open(path, encoding="utf-8")):
            key = (it["title"], it["mallName"], it["lprice"])
            if key in seen or not it["lprice"]:
                continue
            seen.add(key)
            m = PACK_RE.search(it["title"])
            pack = int(m.group(1)) if m else 1
            it["pack"] = pack
            it["unit_price"] = round(it["lprice"] / pack)
            sz = SIZE_RE.search(it["title"])
            it["size"] = (sz.group(1) + sz.group(2).lower()) if sz else None
            items.append(it)
    return items


def mall_coverage(items):
    by = defaultdict(list)
    for it in items:
        by[it["mallName"] or "(미상)"].append(it)
    rows = []
    for mall, its in by.items():
        ups = sorted(i["unit_price"] for i in its)
        rows.append({"mall": mall, "n": len(its), "min_unit": ups[0], "max_unit": ups[-1],
                     "official": ("공식" in mall)})
    rows.sort(key=lambda r: -r["n"])
    return rows


# 동일제품 후보: 핵심 토큰 + 사이즈로 묶어 몰 간 개당가 비교
TARGETS = [
    ("엔조이 슈퍼 마일드 썬 에센스 50ml", ["슈퍼", "마일드", "에센스"], ["썬", "선"], "50ml"),
    ("더 심플 카밍 토너 300ml", ["심플", "카밍", "토너"], None, "300ml"),
    ("퍼스트 트리트먼트 에센스 215ml", ["퍼스트", "트리트먼트", "에센스"], None, "215ml"),
    ("더 심플 카밍 토너 500ml", ["심플", "카밍", "토너"], None, "500ml"),
]


def match_target(it, must, anyof, size):
    t = it["title"]
    if size and it["size"] != size:
        return False
    if not all(w in t for w in must):
        return False
    if anyof and not any(w in t for w in anyof):
        return False
    return True


def compare(items):
    out = []
    for name, must, anyof, size in TARGETS:
        cand = [i for i in items if match_target(i, must, anyof, size)]
        if not cand:
            continue
        by_mall = {}
        for i in sorted(cand, key=lambda x: x["unit_price"]):
            by_mall.setdefault(i["mallName"] or "(미상)", i)  # cheapest per mall
        listings = sorted(by_mall.values(), key=lambda x: x["unit_price"])
        ups = [l["unit_price"] for l in listings]
        out.append({"name": name, "size": size, "n_malls": len(listings),
                    "min_unit": min(ups), "max_unit": max(ups),
                    "spread_pct": round((max(ups) - min(ups)) / max(ups) * 100) if max(ups) else 0,
                    "listings": listings})
    return out


def render(items, malls, comps):
    coupang = [i for i in items if "쿠팡" in (i["mallName"] or "")]
    mall_rows = "".join(
        f"<tr class='{'off' if m['official'] else ''}'><td>{html.escape(m['mall'])}"
        f"{' <span class=p green>공식</span>' if m['official'] else ''}</td>"
        f"<td class=num>{m['n']}</td><td class=num>{m['min_unit']:,}~{m['max_unit']:,}원</td></tr>"
        for m in malls)
    comp_blocks = []
    for c in comps:
        rows = "".join(
            f"<tr><td>{html.escape(l['mallName'] or '(미상)')}"
            f"{' <span class=p green>공식</span>' if '공식' in (l['mallName'] or '') else ''}</td>"
            f"<td class=num>{l['lprice']:,}원</td><td class=num>×{l['pack']}</td>"
            f"<td class=num><b>{l['unit_price']:,}원</b></td></tr>"
            for l in c["listings"])
        comp_blocks.append(f"""
        <div class=card><h3>{html.escape(c['name'])} <span class=mut>· {c['n_malls']}개 몰 · 개당가 스프레드 {c['spread_pct']}%</span></h3>
        <table><tr><th>몰</th><th>표시가</th><th>수량</th><th>개당가</th></tr>{rows}</table></div>""")
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>SCINIC 크로스마켓 도시에 — 네이버 API 실데이터</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--brand-d:#1e4fa8;--red:#d23b3b;--green:#1a9d57;--green-bg:#e6f7ee;--amber:#b9770b;--amber-bg:#fff6e6;--card:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:920px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(244,246,250,.93);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#03c75a;color:#fff;font-weight:900;padding:5px 10px;border-radius:8px}}
h1{{font-size:16px;margin:0;font-weight:800}}h2{{font-size:14px;margin:22px 0 8px}}h3{{font-size:13.5px;margin:0 0 8px}}
.live{{background:var(--green-bg);color:var(--green);font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}
@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.key{{background:linear-gradient(135deg,#fff,#f0fff6);border:1.5px solid #9ee3bd;border-radius:12px;padding:13px 16px;margin:14px 0;font-size:13px}}.key b{{color:var(--green)}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px}}th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-size:10.5px;text-transform:uppercase;letter-spacing:.3px;font-weight:700;background:#fafbfe}}tr:last-child td{{border-bottom:none}}td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
tr.off{{background:#f7fcf9}}.p{{display:inline-block;font-size:9.5px;font-weight:800;padding:1px 6px;border-radius:999px}}.p.green{{background:var(--green-bg);color:var(--green)}}
.mut{{color:var(--mut);font-weight:500}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.65}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>N</span><h1>SCINIC 크로스마켓 도시에 — 네이버 쇼핑검색 API 실데이터</h1>
<span class=live>REAL · 정식 API</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{len(items)}</div><div class=l>수집 리스팅(정규화 후)</div></div>
    <div class=tile><div class=b>{len(malls)}</div><div class=l>판매 몰(마켓)</div></div>
    <div class=tile><div class=b>{len(comps)}</div><div class=l>몰 간 가격비교 제품</div></div>
    <div class=tile><div class=b>{len(coupang)}</div><div class=l>쿠팡 리스팅(네이버 가격비교)</div></div>
  </div>

  <div class=key>🔑 <b>쿠팡 가격이 네이버 가격비교 API로 잡힙니다</b>({len(coupang)}건) — 게이트된 쿠팡을 직접 크롤하지 않고
    <b>합법적으로 일부 수집</b>하는 우회 경로. 전수는 아니지만 GATE 리스크를 크게 낮춥니다.</div>

  <h2>🌐 몰별 커버리지 (개당가 기준)</h2>
  <div class=card><table><tr><th>몰</th><th>리스팅</th><th>개당가 범위</th></tr>{mall_rows}</table></div>

  <h2>💸 동일제품 몰 간 가격 비교 (개당가 정규화)</h2>
  {''.join(comp_blocks)}

  <p class=foot><b>실데이터 / 정직성.</b> 네이버 쇼핑검색 <b>정식 API</b>(openapi.naver.com/v1/search/shop.json) 실호출 결과 ·
    키워드 3개 · 2026-06-22. 몰명은 네이버 가격비교가 집계한 판매처. 거의 모든 리스팅이 <b>2개입</b>이라 개당가로 정규화함
    (제목 파싱 기반 — 일부 세트/기획은 오차 가능). 동일제품 묶음은 핵심토큰+사이즈 규칙(경량) — 운영은 ER 엔진으로 정밀화.
    네이버 외 직접 채널(쿠팡 전수·옥션 등)은 별도 연동/게이트 필요.</p>
</div></body></html>"""


def main():
    items = load_items()
    malls = mall_coverage(items)
    comps = compare(items)
    with open(os.path.join(OUT, "naver_crossmarket.html"), "w", encoding="utf-8") as f:
        f.write(render(items, malls, comps))
    with open(os.path.join(OUT, "naver_crossmarket.json"), "w", encoding="utf-8") as f:
        json.dump({"n_items": len(items), "malls": malls, "comparisons": comps}, f, ensure_ascii=False, indent=2)
    print(f"리스팅 {len(items)} · 몰 {len(malls)} · 비교제품 {len(comps)}")
    print("몰별:", ", ".join(f"{m['mall']}({m['n']})" for m in malls[:10]))
    for c in comps:
        print(f"\n[{c['name']}] {c['n_malls']}몰 개당가 {c['min_unit']:,}~{c['max_unit']:,} (스프레드 {c['spread_pct']}%)")
        for l in c["listings"]:
            print(f"   {l['mallName'][:14]:16} {l['lprice']:>7,}원 ×{l['pack']} = 개당 {l['unit_price']:,}원")
    print("\noutputs/naver_crossmarket.html, naver_crossmarket.json 생성")


if __name__ == "__main__":
    main()
