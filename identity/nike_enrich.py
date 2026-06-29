#!/usr/bin/env python3
"""
나이키 카탈로그(시장가) × nike.com 공식가 조인 → undercut%(공식가 대비 침해율).

입력: outputs/nike_live.json (시장 크로스마켓 카탈로그)
      outputs/nike_official.json (nike.com 공식가, 스타일코드 정확일치 18건)
출력: outputs/nike_official_enriched.html, outputs/nike_official_enriched.csv

핵심: 공식가가 들어오면 가격폭(spread)만 보던 것을 넘어,
  · market_min < 공식가  → 공식가보다 싸게 = 병행수입/가품/덤핑 의심
  · market_max > 공식가  → 리셀 프리미엄
스타일코드가 시장↔공식 조인 키.
"""
import csv
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")


def load():
    cat = json.load(open(os.path.join(OUT, "nike_live.json"), encoding="utf-8"))
    off_raw = json.load(open(os.path.join(OUT, "nike_official.json"), encoding="utf-8"))
    official = {r[0]: {"price": r[1], "msrp": r[2], "disc": bool(r[3]), "color": r[4]}
                for r in off_raw["records"]}
    return cat, official, off_raw


def build(cat, official):
    rows = []
    for p in cat["products"]:
        if p["n_malls"] < 2:
            continue
        o = official.get(p["code"])
        rec = {
            "code": p["code"], "name": p["name"], "n_malls": p["n_malls"],
            "n_listings": p["n_listings"], "market_min": p["min"],
            "market_median": p["median"], "market_max": p["max"], "spread_pct": p["spread_pct"],
            "official": bool(o),
        }
        if o:
            ref = o["price"]  # 현재 공식 판매가 기준
            rec.update({
                "official_price": o["price"], "official_msrp": o["msrp"],
                "official_discounted": o["disc"], "official_color": o["color"],
                # +면 시장 최저가가 공식가보다 그만큼 쌈(병행/가품 의심), -면 비쌈
                "undercut_min_pct": round((ref - p["min"]) / ref * 100),
                "premium_max_pct": round((p["max"] - ref) / ref * 100),
                "median_vs_official_pct": round((p["median"] - ref) / ref * 100),
            })
        rows.append(rec)
    # 공식가 있는 것 먼저, undercut 큰 순(가장 의심스러운 것 위로)
    rows.sort(key=lambda r: (-r["official"], -(r.get("undercut_min_pct") or -999)))
    return rows


def severity(r):
    """undercut_min 기준 위험 라벨."""
    u = r.get("undercut_min_pct")
    if u is None:
        return ("", "")
    if u >= 40:
        return ("심각", "sev-hi")
    if u >= 20:
        return ("주의", "sev-mid")
    if u > 0:
        return ("경미", "sev-lo")
    return ("정상", "sev-ok")


def render(rows, off_raw):
    enr = [r for r in rows if r["official"]]
    noff = [r for r in rows if not r["official"]]
    flagged = [r for r in enr if (r.get("undercut_min_pct") or 0) > 0]
    worst = max((r["undercut_min_pct"] for r in enr), default=0)
    cards = []
    for r in enr:
        sev, cls = severity(r)
        u = r["undercut_min_pct"]
        disc = " <span class=dchip>공식 할인중</span>" if r["official_discounted"] else ""
        ucolor = "red" if u > 0 else "muted"
        q = html.escape(r["code"])
        cards.append(f"""<div class="card {cls}">
          <div class=top><h3>{html.escape(r['name'])}</h3><span class="sev {cls}">{sev}</span></div>
          <div class=codeln><code>{q}</code> · 공식 컬러 <b>{html.escape(r['official_color'])}</b>{disc}
            · <a href="https://www.nike.com/kr/w?q={q}" target=_blank>공식몰↗</a></div>
          <div class=prices>
            <div class=pcell><span class=pl>공식가(현재)</span><span class=pv>{r['official_price']:,}원</span>
              {f"<span class=msrp>정가 {r['official_msrp']:,}</span>" if r['official_discounted'] else ""}</div>
            <div class=pcell><span class=pl>시장 최저</span><span class="pv {ucolor}">{r['market_min']:,}원</span></div>
            <div class=pcell><span class=pl>시장 중앙</span><span class=pv>{r['market_median']:,}원</span></div>
            <div class=pcell><span class=pl>시장 최고</span><span class=pv>{r['market_max']:,}원</span></div>
          </div>
          <div class=metrics>
            <span class="m {('red' if u>0 else 'green')}">공식가 대비 최저 {'−' if u>0 else '+'}{abs(u)}%</span>
            <span class=m>중앙 {('−' if r['median_vs_official_pct']<0 else '+')}{abs(r['median_vs_official_pct'])}%</span>
            <span class=m>최고 +{r['premium_max_pct']}%(리셀)</span>
            <span class=m>{r['n_malls']}몰 · {r['n_listings']}리스팅</span>
          </div></div>""")
    # 공식 미확인 테이블(소수만)
    noff_rows = "".join(
        f"<tr><td><code>{html.escape(r['code'])}</code></td><td>{html.escape(r['name'])}</td>"
        f"<td class=num>{r['n_malls']}몰</td><td class=num>{r['market_min']:,}~{r['market_max']:,}원</td></tr>"
        for r in noff[:30])
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>나이키 시장가 × 공식가(nike.com) · undercut 모니터</title>
<style>
:root{{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--brand:#2d6cdf;--red:#d23b3b;--green:#1a9d57;--amber:#b9770b;--card:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}}
.wrap{{max-width:1040px;margin:0 auto;padding:0 20px 56px}}
header{{background:rgba(20,22,26,.96);color:#fff;padding:13px 0;position:sticky;top:0;z-index:5}}
header .wrap{{display:flex;align-items:center;gap:11px;flex-wrap:wrap}}
.logo{{background:#fff;color:#111;font-weight:900;padding:5px 11px;border-radius:8px;font-size:13px;letter-spacing:1px}}
h1{{font-size:16px;margin:0;font-weight:800}}h3{{font-size:13.5px;margin:0;font-weight:800}}
.live{{background:var(--green);color:#fff;font-weight:800;font-size:11px;padding:5px 10px;border-radius:999px;margin-left:auto}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}@media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}}.tile .b{{font-size:21px;font-weight:800;color:var(--brand)}}.tile .l{{font-size:11px;color:var(--mut)}}
.tile.warn .b{{color:var(--red)}}
.note{{background:#fff6e6;border:1px solid #ecd9a8;border-radius:11px;padding:11px 14px;font-size:12.5px;margin:12px 0}}.note b{{color:var(--amber)}}
.sect{{font-size:12px;color:var(--mut);font-weight:700;margin:20px 0 8px;text-transform:uppercase;letter-spacing:.4px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px;border-left:4px solid var(--line)}}
.card.sev-hi{{border-left-color:var(--red)}}.card.sev-mid{{border-left-color:var(--amber)}}.card.sev-lo{{border-left-color:#d9b441}}.card.sev-ok{{border-left-color:var(--green)}}
.top{{display:flex;justify-content:space-between;align-items:center;gap:8px}}
.sev{{font-size:10px;font-weight:800;padding:2px 8px;border-radius:999px}}
.sev.sev-hi{{background:#fde;color:var(--red);background:#fdecec}}.sev.sev-mid{{background:#fff3df;color:var(--amber)}}.sev.sev-lo{{background:#fbf6e3;color:#9a7d12}}.sev.sev-ok{{background:#e9f8f0;color:var(--green)}}
.codeln{{font-size:11.5px;color:var(--mut);margin:5px 0 9px}}.codeln a{{color:var(--brand);text-decoration:none}}
code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11px}}
.dchip{{background:#eef3fc;color:#2452a8;border-radius:5px;padding:1px 6px;font-size:10px;font-weight:700}}
.prices{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:8px}}
.pcell{{background:#fafbfe;border:1px solid var(--line);border-radius:8px;padding:6px 8px;display:flex;flex-direction:column}}
.pl{{font-size:9.5px;color:var(--mut);text-transform:uppercase}}.pv{{font-size:13px;font-weight:800;font-variant-numeric:tabular-nums}}
.pv.red{{color:var(--red)}}.pv.muted{{color:var(--ink)}}.msrp{{font-size:9.5px;color:var(--mut);text-decoration:line-through}}
.metrics{{display:flex;flex-wrap:wrap;gap:5px}}.m{{font-size:10.5px;background:#eef1f7;color:#465569;border-radius:6px;padding:2px 7px;font-weight:700}}
.m.red{{background:#fdecec;color:var(--red)}}.m.green{{background:#e9f8f0;color:var(--green)}}
table{{width:100%;border-collapse:collapse;font-size:12px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line)}}th{{color:var(--mut);font-size:10px;text-transform:uppercase;background:#fafbfe}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}tr:last-child td{{border-bottom:none}}
.foot{{color:var(--mut);font-size:11.5px;margin-top:18px;border-top:1px solid var(--line);padding-top:11px;line-height:1.6}}.foot b{{color:var(--ink)}}
</style></head><body>
<header><div class=wrap><span class=logo>NIKE×</span><h1>시장가 × 공식가(nike.com) · undercut 모니터</h1>
<span class=live>● 공식가 REAL · nike.com</span></div></header>
<div class=wrap>
  <div class=kpis>
    <div class=tile><div class=b>{len(enr)}</div><div class=l>공식가 매칭 SKU(정확일치)</div></div>
    <div class="tile warn"><div class=b>{len(flagged)}</div><div class=l>공식가보다 싼 SKU(의심)</div></div>
    <div class="tile warn"><div class=b>−{worst}%</div><div class=l>최대 공식가 대비 하락</div></div>
    <div class=tile><div class=b>{len(noff)}</div><div class=l>공식몰 미확인 SKU</div></div>
  </div>
  <div class=note>🔑 <b>스타일코드로 시장 카탈로그 ↔ nike.com 공식가를 조인</b>했습니다. 이제 가격폭이 아니라
    <b>공식가 대비 침해율(undercut%)</b>로 봅니다 — 공식가보다 크게 싸면(−%) 병행수입/가품/덤핑 의심, 크게 비싸면(+%) 리셀 프리미엄.
    공식가는 nike.com 현재 판매가(할인 적용 시 정가 별도 표기). 공식가 매칭은 <b>스타일코드 정확일치 {len(enr)}건</b>만(나머지는 공식몰 검색에서 동일코드 미발견 → 단종/한정/미판매로 추정, 아래 별도 표).</div>
  <div class=sect>공식가 매칭 SKU — undercut 큰 순</div>
  <div class=grid>{''.join(cards)}</div>
  <div class=sect>공식몰 미확인 SKU ({len(noff)}건, 단종/한정/미판매 추정) — 상위 30</div>
  <table><tr><th>스타일코드</th><th>제품</th><th>몰</th><th>시장가</th></tr>{noff_rows}</table>
  <p class=foot><b>실데이터/정직성.</b> 시장가 = 네이버 쇼핑 정식 API 라이브({off_raw['queried']} 다중몰 SKU 질의).
    공식가 = nike.com/kr 실제 브라우저 세션의 <code>__NEXT_DATA__.Wall</code>(스타일코드 검색, 정확일치만 채택, {off_raw['matched_exact']}/{off_raw['queried']}).
    nike.com은 Akamai 봇차단으로 서버측 스크립트 불가 → 실제 브라우저 경로로 수집. 공식가가 할인 중이면 정가(MSRP)도 함께 표기.
    공식몰 미확인 {len(noff)}건은 '공식 가격 없음' 자체가 신호(단종/리셀 시장).</p>
</div></body></html>"""


def write_csv(rows):
    cols = ["code", "name", "n_malls", "n_listings", "market_min", "market_median",
            "market_max", "official", "official_price", "official_msrp",
            "official_discounted", "official_color", "undercut_min_pct",
            "median_vs_official_pct", "premium_max_pct"]
    path = os.path.join(OUT, "nike_official_enriched.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def main():
    cat, official, off_raw = load()
    rows = build(cat, official)
    enr = [r for r in rows if r["official"]]
    with open(os.path.join(OUT, "nike_official_enriched.html"), "w", encoding="utf-8") as f:
        f.write(render(rows, off_raw))
    write_csv(rows)
    flagged = [r for r in enr if (r.get("undercut_min_pct") or 0) > 0]
    print(f"공식가 매칭 {len(enr)} SKU / 다중몰 {len(rows)} · 공식가보다 싼(의심) {len(flagged)}")
    print("=" * 70)
    for r in enr:
        u = r["undercut_min_pct"]
        print(f"  {r['code']:12} {r['name'][:22]:24} 공식 {r['official_price']:>7,} | "
              f"시장 {r['market_min']:>7,}~{r['market_max']:>7,} | "
              f"최저 {'−' if u>0 else '+'}{abs(u)}% 최고 +{r['premium_max_pct']}%")
    print("→ outputs/nike_official_enriched.html, .csv")


if __name__ == "__main__":
    main()
