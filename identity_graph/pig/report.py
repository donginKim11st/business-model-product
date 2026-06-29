"""Render results to a human-readable Markdown report and a styled HTML page."""
import html
import json


def _pct(x):
    return f"{100 * x:.1f}%"


def _bar(x, width=20):
    filled = int(round(x * width))
    return "█" * filled + "░" * (width - filled)


def build_markdown(ctx):
    records = ctx["records"]
    by_id = {r["id"]: r for r in records}
    blk = ctx["blocking_comparison"]
    run = ctx["run"]
    prf = ctx["prf"]
    guard = ctx["variant_guard"]
    cl = ctx["cluster_stats"]
    funnel = run["funnel"]

    L = []
    L.append("# 크로스마켓 상품 정체성 그래프 — 검증 리포트\n")
    L.append(f"- 입력 리스팅: **{len(records)}개** (마켓: amazon / naver / coupang / 11st)")
    L.append(f"- 정답 엔티티(진짜 상품): **{cl['true_entities']}개**")
    L.append(f"- 판정 엔진(LLM 단계): **{run['adjudicator']}**\n")

    L.append("> ⚠️ **읽는 법(중요).** 이 결과물의 일반화 가능한 헤드라인은 **§1의 MinHash 재현율 격차**"
             " 하나입니다(스크립트가 분리돼 토큰을 공유 못 하는 구조적 사실 — 데이터셋과 무관하게 성립).")
    L.append("> §2의 P/R/F1과 하이브리드 재현율은 **이 43개 셋에 임계값·이중언어 사전을 맞춰 튜닝**한 결과라"
             " *정확도 추정치가 아니라*, **설계된 하드 케이스 유형을 아키텍처가 처리함**을 보이는 새너티 체크입니다."
             " 운영 정확도는 held-out 데이터 + 실제 다국어 인코더로 별도 측정해야 합니다(§6).\n")

    L.append("## 1. 블로킹 재현율 — 제안안(MinHash) vs 하이브리드  ⭐핵심 결과")
    L.append("> 블로킹은 재현율 천장입니다. 여기서 놓친 쌍은 어떤 후속 단계도 복구 못 합니다.\n")
    L.append("| 블로커 | 후보쌍 | 정답쌍 커버 | 재현율 | |")
    L.append("|---|---:|---:|---:|---|")
    for b in blk:
        L.append(f"| {b['name']} | {b['candidate_pairs']} | {b['covered']}/{b['true_pairs']} | "
                 f"**{_pct(b['recall'])}** | `{_bar(b['recall'])}` |")
    L.append("")
    # show what MinHash missed
    mh = next(b for b in blk if b["is_minhash"])
    if mh["missed_pairs"]:
        L.append(f"**MinHash 단독이 놓친 {len(mh['missed_pairs'])}개 정답쌍** (대부분 한↔영 cross-lingual):\n")
        L.append("| 쌍 | A | B |")
        L.append("|---|---|---|")
        for a, b in mh["missed_pairs"]:
            L.append(f"| {a}–{b} | {by_id[a]['title']} | {by_id[b]['title']} |")
        L.append("")
        L.append("→ 한글/영문 제목은 토큰을 거의 공유하지 않아 MinHash가 후보로조차 못 올립니다. "
                 "하이브리드는 **결정적 모델키 + 브랜드 정규화 char-ngram**으로 이를 복구합니다.\n")

    L.append("## 2. 엔드투엔드 결과 (하이브리드 블로킹 기준) — *튜닝된 셋, 정확도 주장 아님*")
    L.append(f"- Precision {_pct(prf['precision'])}, Recall {_pct(prf['recall'])}, "
             f"F1 {_pct(prf['f1'])} → **설계된 케이스 유형(§아래)을 모두 처리**한다는 의미이지, "
             "운영 정확도 추정치가 아닙니다.")
    L.append(f"- 정답쌍 {prf['tp']+prf['fn']}개 중 {prf['tp']}개 정확 병합, "
             f"오병합 {prf['fp']}개, 누락 {prf['fn']}개")
    L.append(f"- 해소된 클러스터 **{cl['resolved_clusters']}개** vs 정답 엔티티 {cl['true_entities']}개")
    L.append("- 처리됨을 보인 케이스 유형: 한↔영 cross-lingual · 회색시장(병행수입) 난독화 · "
             "셀러 스팸 키워드 · 색상/용량/팩수/커넥터/사이즈 변형 · 번들 · GTIN 재사용 · "
             "리퍼비시(의미적 모호성)\n")

    L.append("### 단계별 퍼널 (cascade)")
    L.append("| 단계 | 건수 |")
    L.append("|---|---:|")
    L.append(f"| 블로킹 후보쌍 | {funnel['candidates']} |")
    L.append(f"| → 자동 병합 (고신뢰) | {funnel['auto_merge']} |")
    L.append(f"| → 경계 케이스 = LLM 호출 | {funnel['boundary_llm']} |")
    L.append(f"|    · LLM '같음' | {funnel['llm_same']} |")
    L.append(f"|    · LLM '다름' | {funnel['llm_different']} |")
    L.append(f"| → 자동 기각 | {funnel['auto_reject']} |")
    share = funnel["boundary_llm"] / funnel["candidates"] if funnel["candidates"] else 0
    L.append(f"\n→ 전체 후보쌍의 **{_pct(share)}만 LLM에 도달** (나머지는 값싼 단계에서 결정). "
             "이것이 'cascade' 비용 구조입니다.\n")

    L.append("## 3. Variant 가드 — 변형/번들/GTIN재사용을 안 합치는가")
    L.append("> 텍스트는 거의 동일하지만 SKU가 다른 함정들. 하나라도 합치면 리프라이싱 사고로 직결됩니다.\n")
    L.append("| 함정쌍 | 케이스 | 결과 |")
    L.append("|---|---|---|")
    for row in guard["rows"]:
        mark = "✅ 분리 유지" if row["kept_separate"] else "❌ **오병합**"
        L.append(f"| {row['pair'][0]}–{row['pair'][1]} | {row['label']} | {mark} |")
    L.append(f"\n→ 변형 함정 {guard['total_traps']}개 중 오병합 **{guard['false_merges']}개**. "
             "(이들은 모두 깔끔한 속성 충돌로 **값싸게 결정적 분리** — LLM 호출 없음.)\n")

    sem = ctx["semantic_guard"]
    L.append("## 3b. 의미적 모호성 — LLM이 실제로 일하는 케이스")
    L.append("> 브랜드·모델·색상이 **완전히 동일**해서 어떤 속성으로도 못 가르는 쌍. "
             "값싼 규칙으로는 분리 불가 → **경계 밴드로 보내 LLM이 판정**. 이것이 '경계만 LLM' 설계의 핵심입니다.\n")
    L.append("| 쌍 | 케이스 | 결과 |")
    L.append("|---|---|---|")
    for row in sem["rows"]:
        mark = "✅ LLM이 분리" if row["kept_separate"] else "❌ **오병합**"
        L.append(f"| {row['pair'][0]}–{row['pair'][1]} | {row['label']} | {mark} |")
    L.append(f"\n→ 의미적 함정 {sem['total_traps']}개 중 오병합 **{sem['false_merges']}개**. "
             "리퍼비시(L045)는 신품 E001과 추출 속성이 같지만, LLM 단계가 'condition mismatch'로 분리합니다. "
             "(오프라인 스탠드인은 규칙 프록시이며, 실제 claude-haiku-4-5는 단종/병행/위조 등 "
             "열거하지 않은 의미 구분으로 일반화됩니다.)\n")

    L.append("## 4. 경계 케이스 LLM 판정 로그")
    L.append("| 쌍 | 점수 | 판정 | 근거 |")
    L.append("|---|---:|---|---|")
    for d in run["decisions"]:
        if d["stage"] == "llm":
            a, b = d["pair"]
            L.append(f"| {a}–{b} | {d['score']} | {d['decision']} | {d['reason']} |")
    L.append("")

    L.append("## 5. 해소된 정체성 그래프 (샘플)")
    multi = [c for c in run["clusters"] if len(c) > 1]
    multi.sort(key=lambda c: -len(c))
    for cl_ids in multi[:6]:
        members = sorted(cl_ids)
        head = by_id[members[0]]["title"]
        L.append(f"- **{head}**  ⟵ {len(members)}개 리스팅")
        for m in members:
            r = by_id[m]
            L.append(f"    - `{r['marketplace']}` {r['title']}  ({m})")
    L.append("")
    L.append("## 6. 한계 — 이것이 증명하는 것과 아닌 것 (정직하게)")
    L.append(f"- **튜닝 공개:** 임계값(경계 밴드)과 이중언어 사전은 이 **{len(records)}개 셋에 맞춰 조정**됐고, "
             "사전은 이 데이터의 브랜드를 정확히 커버합니다. 따라서 §2의 P/R/F1과 하이브리드 재현율은 "
             "**같은 데이터로 측정한 in-sample 값 → 일반화 추정치가 아닙니다.** "
             "신뢰할 정확도는 **held-out 데이터**로만 측정해야 합니다.")
    L.append("- **일반화되는 결과는 §1의 MinHash 재현율 격차 하나**입니다: 한↔영 제목은 스크립트가 달라 "
             "토큰을 거의 공유하지 않으므로 MinHash 단독 블로킹은 cross-lingual을 구조적으로 놓칩니다 "
             "— 이건 어떤 데이터셋에서도 성립합니다.")
    L.append("- 실제 마켓 데이터의 하드 variant 밴드에서는 SOTA(Ditto/LLM-select)도 **F1 72~85%로 하락**"
             "(WDC Products 벤치마크). 운영에는 실측 골든셋 + 저신뢰 페어 휴먼리뷰 큐가 필요합니다.")
    L.append("- cross-lingual 브리지는 데모용 **소형 이중언어 사전**으로 구현했습니다. 운영에서는 이 레그를 "
             "**다국어 bi-encoder(BGE-M3) + ANN(HNSW/FAISS)** 으로 교체해야 하며, 그때 재현율은 달라집니다.")
    L.append("- LLM 판정은 같은 구조화 속성을 보는 **오프라인 결정적 스탠드인**입니다. "
             "`PIG_USE_CLAUDE=1` + `ANTHROPIC_API_KEY` 로 실제 claude-haiku-4-5 호출로 전환됩니다.")
    L.append("- 변형(500ml↔1.5L 등)은 **속성 충돌로 값싸게 결정적 분리**되고, LLM은 *난독화/회색시장/"
             "cross-lingual로 흐려진 진짜 매칭*에만 씁니다 — 변형 구분에 LLM 비용을 쓰지 않는 설계입니다.")
    L.append("- **데이터 수집(크롤/공식 API)과 법적 리스크는 이 PoC 범위 밖**입니다(특히 쿠팡 Akamai/DB권). "
             "별도 검증 필요 — 이 결과물은 *매칭 엔진*만 증명합니다.")
    L.append("")
    L.append("---")
    L.append("_생성: PoC 파이프라인 (의존성 없는 순수 Python). "
             "실제 운영에서 char-ngram 레그는 다국어 bi-encoder + ANN으로, "
             "LLM 스탠드인은 `PIG_USE_CLAUDE=1`로 실제 claude-haiku-4-5 교체 가능._")
    return "\n".join(L)


def build_html(markdown_text, ctx):
    blk = ctx["blocking_comparison"]
    prf = ctx["prf"]
    guard = ctx["variant_guard"]
    mh = next(b for b in blk if b["is_minhash"])
    hy = next(b for b in blk if not b["is_minhash"])
    rows = "".join(
        f"<tr><td>{html.escape(b['name'])}</td><td>{_pct(b['recall'])}</td>"
        f"<td><div class='bar'><div style='width:{b['recall']*100:.0f}%'></div></div></td></tr>"
        for b in blk
    )
    body = html.escape(markdown_text)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>상품 정체성 그래프 검증 리포트</title>
<style>
body{{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;max-width:920px;margin:40px auto;padding:0 20px;color:#1a1a1a;line-height:1.6}}
h1{{border-bottom:3px solid #2d6cdf;padding-bottom:8px}}
.kpi{{display:flex;gap:16px;flex-wrap:wrap;margin:20px 0}}
.card{{flex:1;min-width:150px;background:#f5f7fb;border-radius:12px;padding:16px;text-align:center}}
.card b{{font-size:28px;color:#2d6cdf;display:block}}
.card.muted b{{color:#888;font-size:22px}}
.note{{background:#fff7e6;border:1px solid #ffd591;border-radius:10px;padding:12px 16px;font-size:13px;margin:8px 0}}
table{{width:100%;border-collapse:collapse;margin:12px 0}}
td,th{{border-bottom:1px solid #e3e3e3;padding:6px 8px;text-align:left;font-size:14px}}
.bar{{background:#e3e8f5;border-radius:6px;height:14px;overflow:hidden}}
.bar>div{{background:#2d6cdf;height:100%}}
pre{{white-space:pre-wrap;background:#fafafa;border:1px solid #eee;border-radius:10px;padding:18px;font-size:13px}}
</style></head><body>
<h1>크로스마켓 상품 정체성 그래프 — 검증 리포트</h1>
<p>핵심 결과 — cross-lingual 블로킹 재현율(데이터셋과 무관하게 일반화되는 유일한 수치):</p>
<div class="kpi">
  <div class="card"><b>{_pct(mh['recall'])}</b>MinHash 단독(제안)</div>
  <div class="card"><b>{_pct(hy['recall'])}</b>하이브리드(수정안)*</div>
  <div class="card muted"><b>{guard['false_merges']}</b>Variant 오병합</div>
</div>
<div class="note">⚠️ P/R/F1 = {_pct(prf['precision'])}/{_pct(prf['recall'])}/{_pct(prf['f1'])} 및
하이브리드 재현율(*)은 이 43개 셋에 임계값·이중언어 사전을 맞춘 <b>in-sample 값</b>입니다.
<b>정확도 추정치가 아니라</b> 설계된 하드 케이스 유형을 처리함을 보이는 새너티 체크입니다(§6 참고).</div>
<h3>블로킹 재현율: MinHash(제안) vs 하이브리드(수정안)</h3>
<table><tr><th>블로커</th><th>재현율</th><th></th></tr>{rows}</table>
<h3>전체 리포트</h3>
<pre>{body}</pre>
</body></html>"""


def build_graph_json(ctx):
    run = ctx["run"]
    by_id = {r["id"]: r for r in ctx["records"]}
    nodes = []
    for i, cl in enumerate(run["clusters"], 1):
        members = sorted(cl)
        nodes.append({
            "canonical_id": f"PIG-{i:04d}",
            "title": by_id[members[0]]["title"],
            "listing_count": len(members),
            "marketplaces": sorted({by_id[m]["marketplace"] for m in members}),
            "members": [
                {"id": m, "marketplace": by_id[m]["marketplace"],
                 "title": by_id[m]["title"], "price": by_id[m]["price"],
                 "currency": by_id[m]["currency"]}
                for m in members
            ],
        })
    nodes.sort(key=lambda n: -n["listing_count"])
    return {"canonical_products": nodes, "total": len(nodes)}
