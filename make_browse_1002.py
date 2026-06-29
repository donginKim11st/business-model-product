"""insights_1002.jsonl → browse_1002.html (출처+인용 부착 비정형 인사이트 열람).
직관적 UI v2: 인사이트 우선 · 인용은 접어두고 클릭 시 펼침 · 변형(구성)은 별도 접이식.
사용: python3 make_browse_1002.py [입력.jsonl]"""
import sys, json, html
from collections import Counter

SRC = sys.argv[1] if len(sys.argv) > 1 else "insights_1002.jsonl"
TREES = "trees_food.jsonl"
SRC_LABEL = {"naver": "블로그", "youtube": "유튜브", "danawa": "다나와"}
SRC_CLS = {"naver": "sb", "youtube": "sy", "danawa": "sd"}
ASPECT_LABEL = {"taste": "맛", "texture": "식감·질감", "spec": "스펙", "size": "크기·용량",
                "care": "관리·보관", "price_range": "가격", "routine": "사용법", "sensory": "향·감각"}

TREE_BY_UID = {}
try:
    for _l in open(TREES, encoding="utf-8"):
        _d = json.loads(_l)
        TREE_BY_UID[f"P{_d['bndl_grp']}"] = _d
except FileNotFoundError:
    pass


def ev_html(evs):
    """근거(출처+인용) — 포인트 안에서 접이식으로 펼쳐짐."""
    if not evs:
        return ""
    rows = []
    for e in evs:
        s = e.get("source", "")
        lb, cls = SRC_LABEL.get(s, s), SRC_CLS.get(s, "")
        meta = " · ".join(x for x in [e.get("author", ""), (e.get("date") or "")[:8]] if x)
        rate = f' ★{e["rating"]:.0f}' if e.get("rating") else ""
        q = html.escape(e.get("quote", ""))
        url = html.escape(e.get("url", "") or "")
        ad = ' <span class="ad">광고</span>' if e.get("is_ad") else ""
        a = f'<a href="{url}" target="_blank" rel="noopener">' if url else "<span>"
        aend = "</a>" if url else "</span>"
        rows.append(f'<div class="ev"><span class="sb-tag {cls}">{lb}{rate}</span>{ad} '
                    f'{a}“{q}”{aend} <span class="evm">{html.escape(meta)}</span></div>')
    return f'<details class="evd"><summary>근거 {len(evs)}</summary>{"".join(rows)}</details>'


def point_row(p):
    """인사이트 1줄: 포인트 텍스트 + (접힌) 근거."""
    return f'<div class="pt"><span class="pt-t">{html.escape(p.get("point",""))}</span>{ev_html(p.get("evidence"))}</div>'


def section(title, cls, points):
    if not points:
        return ""
    body = "".join(point_row(p) for p in points)
    return f'<div class="sec {cls}"><div class="sec-h">{title} <span class="n">{len(points)}</span></div>{body}</div>'


def aspects_html(asp):
    """속성: 카테고리(맛·식감·가격…)별로 묶어 표시."""
    blocks = []
    for k, v in (asp or {}).items():
        if isinstance(v, list) and v:
            lbl = ASPECT_LABEL.get(k, k)
            rows = "".join(point_row(p) for p in v)
            blocks.append(f'<div class="asp-g"><span class="asp-l">{lbl}</span><div>{rows}</div></div>')
    if not blocks:
        return ""
    return f'<div class="sec asp"><div class="sec-h">🔎 속성 <span class="n">{sum(len(v) for v in asp.values() if isinstance(v,list))}</span></div>{"".join(blocks)}</div>'


def faqs_html(faqs):
    if not faqs:
        return ""
    rows = []
    for f in faqs:
        rows.append(f'<div class="faq-i"><div class="q">Q. {html.escape(f.get("question",""))}</div>'
                    f'<div class="a">A. {html.escape(f.get("short_answer",""))}</div>'
                    f'{ev_html(f.get("answer_evidence"))}</div>')
    return f'<div class="sec faq"><div class="sec-h">❓ 자주 묻는 질문 <span class="n">{len(faqs)}</span></div>{"".join(rows)}</div>'


# ── 변형(구성) — 접이식 ─────────────────────────────────────────────────────
def _delta_rows(b):
    out = []
    tax = (b or {}).get("taxonomy", {})
    for grp in ("verdict", "aspect", "context"):
        for v in (tax.get(grp, {}) or {}).values():
            if isinstance(v, list):
                out += [p for p in v if isinstance(p, dict) and p.get("point")]
    return "".join(f'<div class="pt sub"><span class="pt-t">▸ {html.escape(p.get("point",""))}</span>'
                   f'{ev_html(p.get("evidence"))}</div>' for p in out)


def variants_html(tree):
    if not tree:
        return ""
    sizes = tree.get("sizes") or []
    n_sku = sum(len(s.get("counts") or []) for s in sizes)
    if not n_sku:
        return ""
    rows = []
    multi = len(sizes) > 1 or any((s.get("value") and s["value"] != "용량 단일") for s in sizes)
    for s in sizes:
        cnodes = {cn.get("value"): cn for cn in (s.get("count_nodes") or [])}
        if multi:
            rows.append(f'<div class="vsize">📐 {html.escape(s.get("value") or "용량 단일")}</div>')
            rows.append(_delta_rows(s.get("block")))
        for c in (s.get("counts") or []):
            rows.append(f'<div class="vsku">{html.escape(c.get("disp") or "")} '
                        f'<span class="ct">{html.escape(c.get("count") or "단품")}</span></div>')
            b3 = c.get("block")
            if b3 is None and cnodes:
                cn = cnodes.get(c.get("count")); b3 = cn.get("block") if cn else None
            rows.append(_delta_rows(b3))
    return (f'<details class="varbox"><summary>📦 구성 카탈로그 {n_sku}개 · 변형별 인사이트</summary>'
            f'<div class="varbody">{"".join(rows)}</div></details>')


rows = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
types = Counter(r.get("type", "?") for r in rows if r.get("block"))
cards = []
for r in sorted(rows, key=lambda x: x.get("type", "")):
    b = r.get("block")
    if not b:
        continue
    tax = b.get("taxonomy", {})
    vd = tax.get("verdict", {}) or {}
    rec = vd.get("overall_recommendation", "")
    src = b.get("sources", {})
    typ = r.get("type", "")
    tlabel = "패키지" if typ == "package" else "단독"
    srcchips = "".join(f'<span class="sc {SRC_CLS[k]}">{SRC_LABEL[k]} {src.get(k,0)}</span>'
                       for k in ("naver", "youtube", "danawa") if src.get(k))
    body = []
    if rec:
        body.append(f'<div class="rec">💡 {html.escape(rec)}</div>')
    body.append(section("👍 강점", "good", vd.get("strengths")))
    body.append(section("👎 약점", "bad", vd.get("weaknesses")))
    body.append(aspects_html(tax.get("aspect", {})))
    body.append(faqs_html(b.get("faqs")))
    if typ == "package":
        body.append(variants_html(r.get("tree") or TREE_BY_UID.get(r["uid"])))
    cards.append(
        f'<details class="u" data-name="{html.escape(r["keyword"].lower())}" data-type="{typ}">'
        f'<summary><span class="tb {typ}">{tlabel}</span>'
        f'<span class="nm">{html.escape(r["keyword"])}</span>'
        f'<span class="srcs">{srcchips}</span></summary>'
        f'<div class="ubody">{"".join(x for x in body if x)}</div></details>')

chips = "".join(f'<button class="chip" data-type="{t}">{("패키지" if t=="package" else "단독")} {n:,}</button>'
                for t, n in types.most_common())
CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:#f4f5f7;color:#1f2430;margin:0;padding:0}
.wrap{max-width:920px;margin:0 auto;padding:18px 16px 60px}
h1{font-size:21px;margin:0 0 2px}.sub{color:#6b7280;font-size:13px;margin-bottom:14px}
.bar{position:sticky;top:0;background:#f4f5f7;padding:10px 0 8px;z-index:5}
#q{width:100%;padding:12px 15px;border-radius:10px;border:1px solid #d3d8e0;background:#fff;font-size:15px}
.chips{display:flex;gap:7px;margin-top:9px}
.chip{background:#fff;border:1px solid #d3d8e0;color:#566;border-radius:18px;padding:5px 13px;font-size:13px;cursor:pointer}
.chip.on{background:#1c7ed6;border-color:#1c7ed6;color:#fff}
#cnt{color:#6b7280;font-size:12px;margin:6px 2px 10px}
.u{background:#fff;border:1px solid #e6e8ec;border-radius:12px;margin:9px 0;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.u>summary{cursor:pointer;padding:14px 16px;list-style:none;display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.u>summary::-webkit-details-marker{display:none}
.u[open]>summary{border-bottom:1px solid #eef0f3}
.tb{font-size:11px;border-radius:6px;padding:2px 8px;color:#fff;font-weight:600;white-space:nowrap}
.tb.package{background:#2b8a3e}.tb.standalone{background:#7048e8}
.nm{font-size:15px;font-weight:700;flex:1;min-width:160px}
.srcs{display:flex;gap:4px;flex-wrap:wrap}
.sc{font-size:10.5px;border-radius:5px;padding:1px 6px;color:#fff;opacity:.9}
.sc.sb{background:#2f6f3e}.sc.sy{background:#c0392b}.sc.sd{background:#b9770e}
.ubody{padding:6px 16px 16px}
.rec{background:#eef6ff;border:1px solid #cfe3fb;color:#1b4f80;border-radius:9px;padding:11px 13px;font-size:14px;line-height:1.55;margin:10px 0}
.sec{margin:12px 0}
.sec-h{font-size:13px;font-weight:700;color:#374151;margin-bottom:5px}
.sec-h .n{font-size:11px;color:#9aa3af;font-weight:500}
.sec.good{border-left:3px solid #2b8a3e;padding-left:11px}
.sec.bad{border-left:3px solid #e8590c;padding-left:11px}
.sec.asp{border-left:3px solid #1c7ed6;padding-left:11px}
.sec.faq{border-left:3px solid #868e96;padding-left:11px}
.pt{margin:4px 0;font-size:13.5px;line-height:1.5}
.pt-t{color:#2b3140}
.pt.sub .pt-t{color:#5a6270;font-size:12.5px}
.asp-g{display:flex;gap:9px;margin:5px 0;align-items:baseline}
.asp-l{font-size:11px;font-weight:700;color:#1c7ed6;background:#eaf3fe;border-radius:5px;padding:2px 8px;white-space:nowrap;min-width:62px;text-align:center}
.faq-i{margin:8px 0}
.faq-i .q{font-size:13.5px;font-weight:600;color:#2b3140}
.faq-i .a{font-size:13px;color:#475063;margin:2px 0 0 0}
.evd{margin:3px 0 2px}
.evd>summary{cursor:pointer;list-style:none;font-size:11px;color:#1c7ed6;display:inline-block;padding:1px 0}
.evd>summary::-webkit-details-marker{display:none}
.evd>summary::before{content:"근거 보기 ▸";}
.evd[open]>summary::before{content:"근거 숨기기 ▾";}
.evd>summary{font-size:0}
.evd>summary::before{font-size:11px}
.ev{font-size:12px;color:#5a6270;margin:4px 0 4px 6px;line-height:1.55;padding-left:8px;border-left:2px solid #eef0f3}
.ev a{color:#475063;text-decoration:none}.ev a:hover{color:#1c7ed6;text-decoration:underline}
.sb-tag{font-size:10px;border-radius:4px;padding:0 5px;color:#fff}
.sb-tag.sb{background:#2f6f3e}.sb-tag.sy{background:#c0392b}.sb-tag.sd{background:#b9770e}
.evm{color:#9aa3af;font-size:10.5px}
.ad{font-size:10px;color:#b9770e;border:1px solid #e8d3a8;border-radius:4px;padding:0 4px}
.varbox{margin:12px 0 2px;border-top:1px dashed #e6e8ec;padding-top:8px}
.varbox>summary{cursor:pointer;list-style:none;font-size:13px;font-weight:700;color:#2b8a3e}
.varbox>summary::-webkit-details-marker{display:none}
.varbox>summary::after{content:" ▸";color:#9aa3af}.varbox[open]>summary::after{content:" ▾"}
.varbody{padding:8px 0 2px}
.vsize{font-size:12px;font-weight:700;color:#1c7ed6;margin:8px 0 3px}
.vsku{font-size:12.5px;color:#2b3140;margin:6px 0 1px;font-weight:600}
.vsku .ct{font-size:10.5px;background:#eef0f3;color:#6b7280;border-radius:8px;padding:0 7px;font-weight:500;margin-left:4px}
"""
JS = """
const q=document.getElementById('q'),chips=[...document.querySelectorAll('.chip')],us=[...document.querySelectorAll('.u')],cnt=document.getElementById('cnt');
let ty=null;
function f(){const s=q.value.trim().toLowerCase();let n=0;us.forEach(u=>{const ok=(!s||u.dataset.name.includes(s))&&(!ty||u.dataset.type===ty);u.style.display=ok?'':'none';if(ok)n++});cnt.textContent=n.toLocaleString()+'개 표시';}
q.addEventListener('input',f);
chips.forEach(c=>c.addEventListener('click',()=>{if(ty===c.dataset.type){ty=null;c.classList.remove('on')}else{ty=c.dataset.type;chips.forEach(x=>x.classList.remove('on'));c.classList.add('on')}f()}));
f();
"""
n_total = sum(types.values())
out = (f'<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
       f'<meta name="viewport" content="width=device-width,initial-scale=1">'
       f'<title>1002 비정형 인사이트</title><style>{CSS}</style></head><body><div class="wrap">'
       f'<h1>🍱 1002 카탈로그 인사이트 <span style="font-size:14px;color:#9aa3af;font-weight:400">{n_total:,}건</span></h1>'
       f'<div class="sub">네이버 블로그 · 유튜브 · 다나와 후기에서 추출 · 근거 인용 클릭 시 원문 이동</div>'
       f'<div class="bar"><input id="q" placeholder="🔍 상품명 검색">'
       f'<div class="chips">{chips}</div></div><div id="cnt"></div>'
       + "".join(cards)
       + f'<script>{JS}</script></div></body></html>')
with open("browse_1002.html", "w", encoding="utf-8") as f:
    f.write(out)
print(f"완료 → browse_1002.html ({n_total:,}건 · {dict(types)})")
