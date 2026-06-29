#!/usr/bin/env python3
"""셀러 인텔리전스 대시보드 — 약점·갭 분석(A) + 몰별 가격 경쟁력(B) 결합. 신뢰기반·전부 근거.

지금 적재된 데이터(insights_demo)로:
  ① 몰별 가격 경쟁력 히트맵 — 판매처×카테고리, 카탈로그 중앙값 대비 % (녹색=싸다 / 빨강=비싸다)
  ② 카테고리별 공통 약점 — 그 카테고리 후기에서 반복되는 불만(verdict.weaknesses 등)
  ③ 상품별 진단 카드 — 약점(정직) · 카테고리 갭(이 카테고리가 중시하는데 내 제품은 약한 속성) · 가격 위치 + 📎근거

단일 HTML(외부 의존 0). 라이트 코퍼레이트.
  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/seller_dashboard.py --html data/seller_dashboard.html
"""
import os
import sys
import json
import html
import argparse
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import load_mongo
from pymongo import MongoClient

WEAK = ("verdict.weaknesses", "context.why.negative_concern")

# 약점 테마 클러스터링(규칙 기반) — 무엇이 불만의 주요 축인지
THEMES = [
    ("가격", ["비싸", "비쌈", "가격", "고가", "가성비", "부담"]),
    ("양·용량", ["양이", "양은", "양에", "적다", "적음", "소량", "용량", "1인분", "작다", "작아"]),
    ("맛·향", ["맛이", "맛은", "싱겁", "짜다", "짭", "달다", "매운맛", "풍미", "향이", "느끼"]),
    ("품질·원료", ["품질", "원료", "첨가", "인공", "위생", "수질", "성분", "위반", "신뢰"]),
    ("식감", ["식감", "질감", "퍽퍽", "딱딱", "물러", "질겨", "눅눅"]),
    ("보관·포장", ["보관", "포장", "유통기한", "냉동", "해동", "녹", "공간"]),
    ("구성·배송", ["배송", "구성", "낱개", "개수", "묶음", "포장지"]),
]


def theme_of(text):
    t = text or ""
    for name, kws in THEMES:
        if any(k in t for k in kws):
            return name
    return "기타"


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _won(n):
    return f"₩{int(n):,}" if isinstance(n, (int, float)) else "—"


def gather(db, min_listings=8):
    pkgs = {p["_id"]: p for p in db.products.find(
        {"type": "package"},
        {"category_l1": 1, "category": 1, "keyword": 1, "taxonomy": 1,
         "representative.dims.dim": 1, "catalogs.ctlg_no": 1,
         "catalogs.price_summary": 1, "buzz.naver_blog": 1})}

    def cat_of(pid):
        p = pkgs[pid]
        return p.get("category_l1") or p.get("category") or "(미분류)"

    # 카탈로그 → (중앙값, 카테고리)
    ctlg_med = {}
    for pid, p in pkgs.items():
        for c in p.get("catalogs") or []:
            m = (c.get("price_summary") or {}).get("median")
            if c.get("ctlg_no") and m:
                ctlg_med[str(c["ctlg_no"])] = (m, cat_of(pid))

    # ① 몰 × 카테고리 가격 인덱스 — 카탈로그별 '몰 최저가'를 모아 그 중앙값 대비(%).
    # (전체 offer 중앙값은 군소셀러 초저가에 끌려 대형몰이 다 +로 보임 → 몰 단위로 정규화해 균형있게)
    cat_offers = defaultdict(lambda: defaultdict(list))   # ctlg → mall → [prices]
    for o in db.offers.find({}, {"mall": 1, "price": 1, "ctlg_no": 1}):
        if o.get("price") and o.get("mall"):
            cat_offers[str(o.get("ctlg_no"))][o["mall"]].append(o["price"])
    mc = defaultdict(lambda: defaultdict(list))
    mall_n, cat_n = Counter(), Counter()
    for ctlg, malls_p in cat_offers.items():
        info = ctlg_med.get(ctlg)
        if not info or len(malls_p) < 2:
            continue
        cat = info[1]
        mall_min = {m: min(ps) for m, ps in malls_p.items()}     # 카탈로그당 몰별 최저가 1개
        vals = sorted(mall_min.values()); n = len(vals)
        typical = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        if not typical:
            continue
        for m, mp in mall_min.items():
            mc[m][cat].append(mp / typical)                       # 그 몰이 '보통가' 대비 몇 배
            mall_n[m] += 1; cat_n[cat] += 1
    top_malls = [m for m, _ in mall_n.most_common(14)]
    top_cats = [c for c, _ in cat_n.most_common(10)]
    matrix = []
    for m in top_malls:
        row = []
        for c in top_cats:
            r = mc[m].get(c)
            row.append(round((sum(r) / len(r) - 1) * 100) if r and len(r) >= min_listings else None)
        matrix.append({"mall": m, "vals": row, "n": mall_n[m]})

    # 카테고리 대표 속성(갭 판정용)
    cat_top = {}
    for r in db.category_attribute_rank.find({}, {"top_dims": 1, "ranked_dims": 1}):
        td = set(r.get("top_dims") or [])
        cat_top[r["_id"]] = [{"dim": d["dim"], "label": d["label"]}
                             for d in (r.get("ranked_dims") or []) if d["dim"] in td]

    # ②③ 약점 클러스터 + 상품 진단
    cat_weak = defaultdict(Counter)
    cat_weak_ev = {}
    cat_theme = defaultdict(Counter)
    prod = []
    for pid, p in pkgs.items():
        cat = cat_of(pid)
        tax = p.get("taxonomy") or {}
        covered = set()
        weaks = []
        for dp, pts in load_mongo.walk_points(tax):
            if pts:
                covered.add(dp)
            if dp.startswith(WEAK):
                for pt in pts:
                    txt = (pt.get("point") or "").strip()
                    if not txt:
                        continue
                    ev = [{"url": e.get("url"), "quote": (e.get("quote") or "")[:80]}
                          for e in (pt.get("evidence") or []) if e.get("url")][:2]
                    weaks.append({"point": txt, "n": pt.get("cited_examples") or 0, "ev": ev})
        for w in weaks:
            cat_weak[cat][w["point"]] += (w["n"] or 1)
            cat_weak_ev.setdefault((cat, w["point"]), w["ev"])
            cat_theme[cat][theme_of(w["point"])] += (w["n"] or 1)
        weaks.sort(key=lambda x: -x["n"])
        # 가격 위치
        prs = [c.get("price_summary") or {} for c in (p.get("catalogs") or []) if (c.get("price_summary") or {}).get("min")]
        price = None
        if prs:
            lo = min(prs, key=lambda x: x["min"])
            price = {"min": min(x["min"] for x in prs), "max": max(x["max"] for x in prs),
                     "low_mall": lo.get("low_mall"), "n_malls": max((x.get("n_malls") or 0) for x in prs),
                     "spread": round(sum((x.get("spread_pct") or 0) for x in prs) / len(prs))}
        tops = cat_top.get(cat) or []
        gap = [d["label"] for d in tops if d["dim"] not in covered]
        cov = [d["label"] for d in tops if d["dim"] in covered]
        prod.append({"id": pid, "kw": p.get("keyword"), "cat": cat,
                     "weak": [{"t": w["point"], "n": w["n"], "ev": w["ev"]} for w in weaks[:3]],
                     "n_weak": len(weaks), "gap": gap[:5], "cov": cov[:6], "price": price,
                     "buzz": (p.get("buzz") or {}).get("naver_blog")})

    # 카테고리별 공통 약점 Top
    cat_weak_top = {}
    for cat, cnt in cat_weak.items():
        cat_weak_top[cat] = [{"t": t, "n": n, "ev": cat_weak_ev.get((cat, t)) or []}
                             for t, n in cnt.most_common(6)]
    cat_theme_out = {}
    for cat, cnt in cat_theme.items():
        tot = sum(cnt.values()) or 1
        cat_theme_out[cat] = [{"t": t, "n": n, "pct": round(n / tot * 100)} for t, n in cnt.most_common()]
    prod.sort(key=lambda x: (-(x["n_weak"]), -(x["buzz"] or 0)))
    return {"price": {"malls": matrix, "cats": top_cats}, "weak": cat_weak_top, "theme": cat_theme_out,
            "prod": prod, "kpi": {"n_pkg": len(pkgs), "n_cat": len(cat_n), "n_mall": len(mall_n),
                                  "n_offer": db.offers.count_documents({})}}


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f5f7fa;--card:#fff;--ink:#10233b;--ink2:#5b6b80;--line:#e6eaf0;--brand:#0f766e;--brand2:#2563eb;--pos:#15803d;--neg:#c81e1e;--warn:#b45309}
body{font-family:'Inter','Pretendard',-apple-system,'Apple SD Gothic Neo',sans-serif;background:var(--bg);color:var(--ink);line-height:1.5}
.wrap{max-width:1200px;margin:0 auto;padding:0 22px 60px}
header{display:flex;align-items:center;gap:12px;padding:22px 0 14px}.logo{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--brand),var(--brand2))}
header h1{font-size:19px;font-weight:800}.hmeta{margin-left:auto;color:var(--ink2);font-size:12px;text-align:right}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:14px 16px}.kpi .v{font-size:24px;font-weight:800}.kpi .l{font-size:12px;color:var(--ink2)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:18px 20px;margin-bottom:16px}
.panel h2{font-size:15px;font-weight:800;margin-bottom:3px}.panel .d{color:var(--ink2);font-size:12.5px;margin-bottom:13px}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:6px 7px;text-align:center;border:1px solid #eef2f7}
th{background:#f7f9fc;color:var(--ink2);font-weight:700}th.ml,td.ml{text-align:left;white-space:nowrap;font-weight:700;color:var(--ink)}
td.cell{font-weight:700;color:#fff;border-radius:0}
.legend{font-size:11.5px;color:var(--ink2);margin-top:9px}.legend b{padding:1px 7px;border-radius:4px;color:#fff}
select{background:var(--card);border:1px solid var(--line);color:var(--ink);border-radius:9px;padding:7px 11px;font-size:13px;font-weight:600;margin-bottom:10px}
.wlist{list-style:none}.wlist li{font-size:13.5px;padding:6px 0;border-bottom:1px solid #f1f4f8;display:flex;gap:8px;align-items:baseline}
.wn{font-size:11px;color:var(--neg);font-weight:800;min-width:34px}
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
#q{flex:1;min-width:200px;background:var(--card);border:1px solid var(--line);border-radius:11px;color:var(--ink);font-size:14px;padding:10px 13px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.chip{background:var(--card);border:1px solid var(--line);color:var(--ink2);font-size:12px;font-weight:600;border-radius:999px;padding:5px 11px;cursor:pointer}.chip.active{background:var(--brand);color:#fff;border-color:var(--brand)}
.pcard{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:14px 16px;margin-bottom:11px}
.ph{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;margin-bottom:8px}.pcat{background:#eef2f7;color:var(--brand2);font-size:11px;font-weight:800;border-radius:6px;padding:2px 8px}
.pkw{font-size:15px;font-weight:800}.pprice{margin-left:auto;font-size:12.5px;color:var(--ink2)}.pprice b{color:var(--ink)}
.pgrid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px}@media(max-width:720px){.pgrid{grid-template-columns:1fr}}
.blk h4{font-size:12px;font-weight:800;margin-bottom:5px}.blk.weak h4{color:var(--neg)}.blk.gap h4{color:var(--warn)}
.blk ul{list-style:none}.blk li{font-size:13px;color:#26405e;padding:3px 0 3px 15px;position:relative}.blk li:before{content:'•';position:absolute;left:2px}
.blk.weak li:before{color:var(--neg)}
.tag{display:inline-block;font-size:11.5px;background:#fff7ed;color:var(--warn);border:1px solid #fde9c8;border-radius:6px;padding:2px 8px;margin:2px 4px 2px 0}
.tagc{background:#ecfdf5;color:var(--pos);border-color:#c6f0d8}
.evlink{font-size:11px;color:var(--brand2);text-decoration:none;font-weight:600;border:1px solid var(--line);border-radius:5px;padding:0 5px;margin-left:4px}
.muted{color:var(--ink2);font-size:12px}
.csort{background:var(--card);border:1px solid var(--line);color:var(--ink2);font-size:12px;font-weight:600;border-radius:8px;padding:5px 11px;cursor:pointer}.csort.active{background:var(--ink);color:#fff;border-color:var(--ink)}
.tlbl{font-size:11.5px;color:var(--ink2);font-weight:700;margin:4px 0 6px}
.themebar{display:flex;align-items:center;gap:8px;margin:3px 0}
.tl{font-size:12px;color:var(--ink);min-width:72px;font-weight:600}
.tbar{flex:1;height:9px;background:#f1f4f8;border-radius:5px;overflow:hidden}.tbar span{display:block;height:9px;background:linear-gradient(90deg,var(--neg),#f59e0b);border-radius:5px}
.tp{font-size:11.5px;color:var(--ink2);font-weight:700;min-width:34px;text-align:right}
.prow{cursor:pointer}.prow:hover td{background:#f7fbff}.selrow td{background:#eaf4ff!important;font-weight:700}
.selrow td.ml{box-shadow:inset 3px 0 0 var(--brand2)}
.csummary{background:linear-gradient(135deg,#0f766e0a,#2563eb0a);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.sumtitle{font-size:14px;font-weight:800;margin-bottom:8px}
.sumline{font-size:13px;color:#26405e;padding:3px 0}.sumline b{color:var(--ink)}
.verdict{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}@media(max-width:600px){.verdict{grid-template-columns:1fr}}
.vg{background:#ecfdf5;border:1px solid #c6f0d8;border-radius:9px;padding:8px 11px;font-size:12.5px;color:#0c3d22}
.vb{background:#fff7ed;border:1px solid #fde9c8;border-radius:9px;padding:8px 11px;font-size:12.5px;color:#7a3d0c}
.vg b,.vb b{font-weight:800}
.cmptab{margin-top:4px}.cmptab th{background:#eef3fb;color:var(--ink);font-weight:800;font-size:12px}
.cmptab td{font-size:12.5px;color:#26405e;vertical-align:top}.cmptab td.ml{color:var(--ink2);font-weight:700;background:#fafbfd}
.bestcell{background:#ecfdf5!important;color:#0c3d22!important;font-weight:800;box-shadow:inset 0 0 0 1px #c6f0d8}
.rk{font-size:10.5px;color:var(--ink2);font-weight:700;background:#eef2f7;border-radius:4px;padding:0 4px;margin-left:3px}
"""

JS = r"""
const D=__DATA__;
function cellColor(v){if(v==null)return ['#f3f5f8','#9aa3af'];
  const a=Math.min(Math.abs(v)/30,1);
  if(v>2)return ['rgba(200,30,30,'+(0.2+a*0.7)+')', a>0.4?'#fff':'#7a1010'];
  if(v<-2)return ['rgba(21,128,61,'+(0.2+a*0.7)+')', a>0.4?'#fff':'#0c3d22'];
  return ['#f3f5f8','#5b6b80'];}
function priceTable(){const P=D.price;let h='<tr><th class=ml>판매처 \\ 카테고리</th>'+P.cats.map(c=>`<th>${c}</th>`).join('')+'</tr>';
  P.malls.forEach(m=>{h+=`<tr><td class=ml>${m.mall} <span class=muted>(${m.n})</span></td>`+
    m.vals.map(v=>{const[bg,fg]=cellColor(v);return `<td class=cell style="background:${bg};color:${fg}">${v==null?'·':(v>0?'+':'')+v+'%'}</td>`;}).join('')+'</tr>';});
  document.getElementById('ptab').innerHTML=h;}
function evl(ev){return (ev||[]).map(e=>e&&e.url?`<a class=evlink href="${e.url}" target=_blank rel=noopener title="${(e.quote||'').replace(/"/g,'&quot;')}">📎</a>`:'').join('');}
function weakList(){const c=document.getElementById('wcat').value;const ws=D.weak[c]||[];const th=D.theme[c]||[];
  const mx=Math.max(...th.map(t=>t.pct),1);
  document.getElementById('themes').innerHTML = th.length?('<div class=tlbl>불만 테마 분포</div>'+th.map(t=>`<div class=themebar><span class=tl>${esc(t.t)}</span><span class=tbar><span style="width:${(t.pct/mx*100).toFixed(0)}%"></span></span><span class=tp>${t.pct}%</span></div>`).join('')):'';
  document.getElementById('wlist').innerHTML = ws.length?ws.map(w=>`<li><span class=wn>${w.n}</span><span>${esc(w.t)} ${evl(w.ev)}</span></li>`).join(''):'<li class=muted>이 카테고리 공통 약점 데이터 없음</li>';}
let cmpSort='weak', cmpSet=[];
function _peers(c){return D.prod.filter(p=>p.cat===c);}
function cmpTable(){const c=document.getElementById('ccat').value;let rows=_peers(c);
  rows.sort((a,b)=>cmpSort==='weak'?(b.n_weak-a.n_weak):cmpSort==='cov'?(b.cov.length-a.cov.length):((a.price?a.price.min:9e9)-(b.price?b.price.min:9e9)));
  let h='<tr><th></th><th class=ml>상품</th><th>약점</th><th>갭</th><th>충족</th><th>최저가</th><th>최저몰</th><th>가격차</th><th>📣블로그</th></tr>';
  h+=rows.slice(0,60).map(p=>{const on=cmpSet.indexOf(p.id)>=0;
    return `<tr class="prow${on?' selrow':''}" onclick="selProd('${p.id}')"><td style="font-weight:800;color:${on?'#2563eb':'#9aa3af'}">${on?'✓':'＋'}</td><td class=ml>${esc(p.kw)}</td><td style="color:#c81e1e;font-weight:700">${p.n_weak}</td><td>${p.gap.length}</td><td style="color:#15803d">${p.cov.length}</td><td>${p.price?won(p.price.min):'—'}</td><td class=ml style="font-weight:400">${esc(p.price?p.price.low_mall:'')}</td><td>${p.price?p.price.spread+'%':'—'}</td><td>${p.buzz?p.buzz.toLocaleString():'—'}</td></tr>`;}).join('');
  document.getElementById('ctab').innerHTML=h;}
function selProd(id){const i=cmpSet.indexOf(id);if(i>=0)cmpSet.splice(i,1);else{if(cmpSet.length>=3)cmpSet.shift();cmpSet.push(id);}cmpTable();renderCompare();}
function renderCompare(){const c=document.getElementById('ccat').value,peers=_peers(c),box=document.getElementById('csummary');
  const items=cmpSet.map(id=>peers.find(p=>p.id===id)).filter(Boolean);
  if(items.length===0){box.innerHTML='<div class=muted>표에서 상품을 클릭하면 비교함에 담깁니다(최대 3개). 1개=경쟁군 대비 진단, 2~3개=나란히 비교.</div>';return;}
  box.innerHTML = items.length===1 ? vsCategory(items[0],peers) : sideBySide(items,peers);}
function vsCategory(me,peers){const N=peers.length,weakAvg=peers.reduce((a,b)=>a+b.n_weak,0)/N,covAvg=peers.reduce((a,b)=>a+b.cov.length,0)/N;
  const prices=peers.filter(p=>p.price).map(p=>p.price.min),buzzes=peers.map(p=>p.buzz||0);let lines=[],good=[],bad=[];
  if(me.price){const r=prices.filter(x=>x<me.price.min).length+1;
    lines.push(`💰 최저가 <b>${won(me.price.min)}</b> · 경쟁 ${prices.length}개 중 <b>${r}위</b>로 저렴 · 최저몰 ${esc(me.price.low_mall||'')} · 가격차 ${me.price.spread}%`);
    if(r<=Math.max(1,Math.ceil(prices.length*0.3)))good.push('가격 경쟁력 상위');else if(r>=Math.floor(prices.length*0.7))bad.push('가격이 경쟁군 대비 높은 편');}
  lines.push(`💡 약점 <b>${me.n_weak}개</b> · 카테고리 평균 ${weakAvg.toFixed(1)}개`);
  if(me.n_weak<weakAvg-0.5)good.push('약점 적음(후기 만족 양호)');else if(me.n_weak>weakAvg+0.5)bad.push('약점 많음'+(me.weak.length?` (${me.weak.slice(0,2).map(w=>w.t.slice(0,16)).join(', ')})`:''));
  lines.push(`🎯 충족 속성 <b>${me.cov.length}개</b> · 평균 ${covAvg.toFixed(1)}개 · 갭: ${me.gap.length?esc(me.gap.join(', ')):'없음'}`);
  if(me.cov.length>covAvg+0.5)good.push('카테고리 핵심 속성 충족 우수');
  if(me.gap.length)bad.push('속성 갭: '+esc(me.gap.join(', ')));
  lines.push(`📣 블로그 매칭 <b>${(me.buzz||0).toLocaleString()}</b> · 경쟁 ${N}개 중 ${buzzes.filter(x=>x>(me.buzz||0)).length+1}위`);
  return `<div class=sumtitle>📌 ${esc(me.kw)} — 경쟁군 ${N}개 대비</div>`+lines.map(l=>`<div class=sumline>${l}</div>`).join('')+
    `<div class=verdict><div class=vg>✅ <b>강점</b> · ${good.length?good.map(esc).join(' · '):'—'}</div><div class=vb>⚠️ <b>보완</b> · ${bad.length?bad.map(esc).join(' · '):'—'}</div></div>`;}
function sideBySide(items,peers){const prices=peers.filter(p=>p.price).map(p=>p.price.min);
  const minW=Math.min(...items.map(p=>p.n_weak)),maxB=Math.max(...items.map(p=>p.buzz||0)),
    pp=items.filter(p=>p.price).map(p=>p.price.min),minP=pp.length?Math.min(...pp):null,maxC=Math.max(...items.map(p=>p.cov.length));
  const rk=v=>v==null?'':` <span class=rk>${prices.filter(x=>x<v).length+1}위</span>`;
  function row(l,fn,best){return `<tr><td class=ml>${l}</td>`+items.map(p=>`<td class="${best&&best(p)?'bestcell':''}">${fn(p)}</td>`).join('')+'</tr>';}
  let h=`<div class=sumtitle>⚖️ 나란히 비교 (${items.length}개) <span class=muted style="font-weight:600">· 칸 클릭 해제</span></div><div style="overflow-x:auto"><table class=cmptab>`;
  h+='<tr><th class=ml>항목</th>'+items.map(p=>`<th onclick="selProd('${p.id}')" style="cursor:pointer">${esc(p.kw.slice(0,16))} ✕</th>`).join('')+'</tr>';
  h+=row('💰 최저가',p=>p.price?won(p.price.min)+rk(p.price.min):'—',p=>p.price&&p.price.min===minP);
  h+=row('최저몰',p=>esc(p.price?p.price.low_mall:'—'));
  h+=row('💡 약점수',p=>p.n_weak,p=>p.n_weak===minW);
  h+=row('약점 내용',p=>p.weak.length?p.weak.slice(0,2).map(w=>esc(w.t.slice(0,22))).join('<br>'):'—');
  h+=row('🎯 갭',p=>p.gap.length?esc(p.gap.join(', ')):'없음',p=>p.gap.length===0);
  h+=row('충족 속성',p=>p.cov.length,p=>p.cov.length===maxC);
  h+=row('📣 블로그',p=>(p.buzz||0).toLocaleString(),p=>(p.buzz||0)===maxB);
  return h+'</table></div>';}
function esc(s){return (s||'').replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}
function card(p){const pr=p.price;
  const price=pr?`💰 <b>${won(pr.min)}~${won(pr.max)}</b> · ${pr.n_malls}몰 · 최저 ${esc(pr.low_mall||'')} · 가격차 ${pr.spread}%`:'가격 정보 없음';
  const weak=p.weak.length?`<ul>${p.weak.map(w=>`<li>${esc(w.t)} <span class=wn>(${w.n})</span> ${evl(w.ev)}</li>`).join('')}</ul>`:'<div class=muted>두드러진 약점 없음</div>';
  const gap=p.gap.length?p.gap.map(g=>`<span class=tag>${esc(g)}</span>`).join(''):'<span class=muted>갭 없음(대표 속성 충족)</span>';
  const cov=p.cov.map(g=>`<span class="tag tagc">${esc(g)}</span>`).join('');
  return `<div class=pcard data-cat="${esc(p.cat)}" data-kw="${esc((p.kw||'').toLowerCase())}">
    <div class=ph><span class=pcat>${esc(p.cat)}</span><span class=pkw>${esc(p.kw)}</span><span class=pprice>${price}</span></div>
    <div class=pgrid>
      <div class="blk weak"><h4>💡 약점·불만 (후기 기반·정직)</h4>${weak}</div>
      <div class="blk gap"><h4>🎯 카테고리 갭 / 충족</h4><div>${gap}</div><div style="margin-top:6px">${cov}</div></div>
    </div></div>`;}
window.addEventListener('load',()=>{
  priceTable();
  const cats=[...new Set(D.prod.map(p=>p.cat))].sort();
  const wsel=document.getElementById('wcat'); wsel.innerHTML=Object.keys(D.weak).sort().map(c=>`<option>${c}</option>`).join(''); wsel.onchange=weakList; weakList();
  const csel=document.getElementById('ccat'); csel.innerHTML=cats.map(c=>`<option>${c}</option>`).join('');
  const psel=document.getElementById('cprod');
  function fillProd(){const peers=_peers(csel.value);psel.innerHTML='<option value="">— 내 제품 선택 —</option>'+peers.map(p=>`<option value="${p.id}">${esc(p.kw)}</option>`).join('');}
  csel.onchange=()=>{cmpSet=[];fillProd();cmpTable();renderCompare();};
  psel.onchange=()=>{if(psel.value)selProd(psel.value);psel.value='';};
  [...document.querySelectorAll('.csort')].forEach(b=>b.onclick=()=>{document.querySelectorAll('.csort').forEach(x=>x.classList.remove('active'));b.classList.add('active');cmpSort=b.dataset.s;cmpTable();});
  fillProd();cmpTable();renderCompare();
  const wrap=document.getElementById('cards'); wrap.innerHTML=D.prod.map(card).join('');
  const chipwrap=document.getElementById('chips');
  chipwrap.innerHTML=`<button class='chip active' data-c=all>전체 ${D.prod.length}</button>`+cats.map(c=>`<button class=chip data-c="${c}">${c} ${D.prod.filter(p=>p.cat===c).length}</button>`).join('');
  const cards=[...wrap.children], chips=[...chipwrap.children]; let cat='all',q='';
  function ap(){let n=0;cards.forEach(c=>{const ok=(cat==='all'||c.dataset.cat===cat)&&(q===''||c.dataset.kw.includes(q));c.style.display=ok?'':'none';if(ok)n++;});document.getElementById('cnt').textContent=n;}
  chips.forEach(ch=>ch.onclick=()=>{chips.forEach(x=>x.classList.remove('active'));ch.classList.add('active');cat=ch.dataset.c;ap();});
  document.getElementById('q').oninput=e=>{q=e.target.value.trim().toLowerCase();ap();};
});
function won(n){return n==null?'—':'₩'+Number(n).toLocaleString();}
"""


def render(d):
    k = d["kpi"]
    kpis = "".join(f"<div class=kpi><div class=v>{v:,}</div><div class=l>{l}</div></div>" for l, v in [
        ("상품(패키지)", k["n_pkg"]), ("카테고리", k["n_cat"]), ("판매처(몰)", k["n_mall"]), ("판매처 리스팅", k["n_offer"])])
    js = JS.replace("__DATA__", json.dumps(d, ensure_ascii=False).replace("</", "<\\/"))
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=robots content='noindex,nofollow,noarchive'>"
            f"<title>셀러 인텔리전스 대시보드</title><style>{CSS}</style></head><body><div class=wrap>"
            f"<header><div class=logo></div><h1>셀러 인텔리전스 — 약점·갭 + 몰별 가격 경쟁력</h1>"
            f"<div class=hmeta>신뢰기반 · 전부 실측·근거(📎)<br>네이버 리뷰·쇼핑 직접 수집</div></header>"
            f"<div class=kpis>{kpis}</div>"
            f"<div class=panel><h2>① 몰별 가격 경쟁력</h2>"
            f"<div class=d>판매처 × 카테고리 · 기준선은 <b>각 카탈로그에서 '몰별 최저가의 중앙값'(전형가)</b>, 그 대비 그 몰이 평균 몇 % 비싼/싼지. <b style='color:var(--pos)'>녹색=싸다</b> · <b style='color:var(--neg)'>빨강=비싸다</b> (리스팅 8건↑만)</div>"
            f"<div style='overflow-x:auto'><table id=ptab></table></div>"
            f"<div class=legend>예: +15% = 그 카테고리 전형가보다 평균 15% 비싸게 파는 몰 · −10% = 10% 싸게 파는 몰. 기준선=카탈로그별 '몰 최저가들의 중앙값'(군소셀러 초저가에 끌리지 않도록 몰 단위 정규화)</div></div>"
            f"<div class=panel><h2>② 카테고리별 공통 약점 + 불만 테마</h2>"
            f"<div class=d>그 카테고리 후기에서 반복되는 불만(verdict.weaknesses 등) — 숫자=근거수, 📎=원문. 테마=불만이 어느 축에 몰리나</div>"
            f"<select id=wcat></select><div id=themes></div><ul class=wlist id=wlist></ul></div>"
            f"<div class=panel><h2>③ 경쟁사 직접 비교 <span class=muted style='font-weight:600'>(같은 카테고리 한판)</span></h2>"
            f"<div class=d>카테고리 선택 → 그 안 상품들을 약점·갭·충족속성·가격·언급량으로 한 줄 비교. 내 제품이 어디쯤인지.</div>"
            f"<div class=controls><select id=ccat></select><select id=cprod></select>"
            f"<span class=muted>정렬</span>"
            f"<button class='csort active' data-s=weak>약점</button>"
            f"<button class=csort data-s=cov>충족</button>"
            f"<button class=csort data-s=price>최저가</button></div>"
            f"<div id=csummary class=csummary></div>"
            f"<div style='overflow-x:auto'><table id=ctab></table></div>"
            f"<div class=muted style='margin-top:6px'>표의 행을 클릭하면 그 상품이 선택돼 위에 요약이 뜹니다.</div></div>"
            f"<div class=panel><h2>④ 상품별 진단 <span class=muted style='font-weight:600'>(약점 많은 순)</span></h2>"
            f"<div class=d>약점(정직)·카테고리 갭(이 카테고리가 중시하는데 약한 속성)·가격 위치 — 셀러 개선 액션용</div>"
            f"<div class=controls><input id=q placeholder='상품명 검색…'><div class=muted>표시 <b id=cnt></b>개</div></div>"
            f"<div class=chips id=chips></div><div id=cards></div></div>"
            f"</div><script>{js}</script></body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="data/seller_dashboard.html")
    ap.add_argument("--min-listings", type=int, default=8)
    args = ap.parse_args()
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]
    d = gather(db, args.min_listings)
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(render(d))
    print(f"셀러 대시보드 → {args.html} · 상품 {d['kpi']['n_pkg']} · 카테고리 {d['kpi']['n_cat']} · "
          f"몰 {len(d['price']['malls'])}×카테 {len(d['price']['cats'])} 가격매트릭스 · 약점카테 {len(d['weak'])}")


if __name__ == "__main__":
    main()
