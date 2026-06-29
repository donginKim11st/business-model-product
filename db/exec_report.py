#!/usr/bin/env python3
"""경영진/투자자용 단일 HTML 리포트 — 라이트 코퍼레이트 + 카탈로그 클릭 모달(전부 실측·외부의존0).

· 라이트 톤(흰/소프트그레이, 정제 타이포, 절제 액센트) — IR 자료 느낌.
· 캔버스 차트(라이브러리/CDN 없음): 추출현황 도넛 · 카테고리 분포 · 언급량 Top · 속성 히트맵 · 가격분포 · 산점도.
· 번들 브라우저: 카드의 카탈로그(SKU) 칩을 클릭하면 모달 — 그 카탈로그의 가격사다리·판매처·실측 인사이트.
· 데이터는 JSON 임베드 → 모달은 client-side(서버 불필요). 카탈로그 인사이트는 catalog_insight_backfill 산출.

  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/exec_report.py --html data/exec_report.html
"""
import os
import sys
import json
import html
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import representative_view as rv
import report_site as rs
from pymongo import MongoClient


def gather_bundles(db, per_cat, max_cards, skus):
    bases = []
    for cat in sorted(c for c in db.products.distinct("representative.category") if c):
        q = {"representative.category": cat, "type": "package", "representative.dims.0": {"$exists": True}}
        cur = list(db.products.find(q, {"_id": 1, "n_catalogs": 1, "catalogs.price_summary.min": 1}).limit(400))
        cur.sort(key=lambda d: (-int(any((c.get("price_summary") or {}).get("min")
                                          for c in d.get("catalogs") or [])), -(d.get("n_catalogs") or 0)))
        bases += [d["_id"] for d in cur[:per_cat]]
    bases = bases[:max_cards]
    out = []
    for uid in bases:
        base = db.products.find_one({"_id": uid})
        if not base:
            continue
        repv = rv.customer_view(base.get("representative") or {})
        cats = []
        n_ins = 0
        for c in (base.get("catalogs") or [])[:skus]:
            ps = c.get("price_summary") or {}
            offers = []
            if ps.get("min"):
                offers = [{"mall": o.get("mall"), "price": o.get("price"), "used": o.get("used"), "url": o.get("url")}
                          for o in db.offers.find({"product_uid": str(c.get("ctlg_no"))},
                                                  {"mall": 1, "price": 1, "used": 1, "url": 1}).sort("price", 1).limit(8)]
            ins = c.get("insight")
            insv = rv.customer_view(ins) if ins and ins.get("dims") else None
            if insv:
                n_ins += 1
            cats.append({
                "id": str(c.get("ctlg_no")), "name": c.get("disp"),
                "count": c.get("count"), "size": c.get("size"),
                "ps": ({k: ps.get(k) for k in ("min", "max", "median", "n_malls", "low_mall", "spread_pct")}
                       if ps.get("min") else None),
                "offers": offers, "insv": insv, "n_sources": (ins or {}).get("n_sources"),
            })
        b = base.get("buzz") or {}
        yt = base.get("youtube") or {}
        ytv = (rv.customer_view({"dims": rv.taxonomy_to_dims(yt.get("taxonomy") or {})})
               if yt.get("status") == "done" and yt.get("taxonomy") else None)
        has_price = any(c.get("ps") for c in cats)
        src = {"naver": bool(repv.get("sections")), "price": has_price,
               "youtube": bool(ytv and ytv.get("sections")),
               "skuins": n_ins > 0}
        out.append({"id": base["_id"], "kw": base.get("keyword"),
                    "cat": base.get("category_l1") or base.get("category"),
                    "buzz": {"blog": b.get("naver_blog"), "shop": b.get("naver_shop")},
                    "repv": repv, "ytv": ytv, "src": src, "n_cat_ins": n_ins, "catalogs": cats})
    return out


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f5f7fa;--card:#ffffff;--ink:#10233b;--ink2:#5b6b80;--line:#e6eaf0;--brand:#0f766e;--brand2:#2563eb;
      --pos:#15803d;--warn:#b45309;--soft:#eef2f7}
body{font-family:'Inter','Pretendard',-apple-system,'Apple SD Gothic Neo',sans-serif;background:var(--bg);color:var(--ink);
     -webkit-font-smoothing:antialiased;line-height:1.5}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px 60px}
header.top{display:flex;align-items:center;gap:12px;padding:22px 0 18px}
.logo{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--brand),var(--brand2))}
.top h1{font-size:19px;font-weight:800;letter-spacing:-.3px}
.top .meta{margin-left:auto;color:var(--ink2);font-size:12.5px;text-align:right}
.hero{background:linear-gradient(135deg,#0f766e0d,#2563eb0d);border:1px solid var(--line);border-radius:18px;padding:22px 24px;margin-bottom:18px}
.hero h2{font-size:22px;font-weight:800;letter-spacing:-.4px;margin-bottom:4px}
.hero p{color:var(--ink2);font-size:13.5px}
.srcline{font-size:12px;color:var(--ink2);margin-top:9px;background:#0f766e0a;border:1px solid var(--line);border-radius:9px;padding:8px 12px}.srcline b{color:var(--ink)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:0 1px 2px #10233b08}
.kpi .v{font-size:27px;font-weight:800;letter-spacing:-.5px;color:var(--ink)}
.kpi .v small{font-size:14px;color:var(--ink2);font-weight:700}
.kpi .l{font-size:12px;color:var(--ink2);margin-top:3px}
.tabs{display:flex;gap:8px;margin:22px 0 16px;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:20}
.tab{background:var(--card);border:1px solid var(--line);color:var(--ink2);font-size:13.5px;font-weight:700;
     border-radius:10px;padding:9px 18px;cursor:pointer;transition:.15s}
.tab.active{background:var(--ink);color:#fff;border-color:var(--ink)}
.view{display:none}.view.active{display:block}
.section-title{font-size:14px;font-weight:800;color:var(--ink);margin:0 0 2px;display:flex;align-items:center;gap:7px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 2px #10233b08}
.panel .d{color:var(--ink2);font-size:12.5px;margin:2px 0 14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.grid2{grid-template-columns:1fr}}
.donuts{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:8px}
canvas{width:100%;display:block}
select{background:var(--card);border:1px solid var(--line);color:var(--ink);border-radius:9px;padding:7px 11px;font-size:13px;margin-bottom:10px;font-weight:600}
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
#q{flex:1;min-width:200px;background:var(--card);border:1px solid var(--line);border-radius:11px;color:var(--ink);font-size:14px;padding:11px 14px}
.chips{display:flex;gap:7px;flex-wrap:wrap}
.chip{background:var(--card);border:1px solid var(--line);color:var(--ink2);font-size:12.5px;font-weight:600;border-radius:999px;padding:6px 13px;cursor:pointer}
.chip b{color:var(--brand);margin-left:3px}.chip.active{background:var(--brand);color:#fff;border-color:var(--brand)}.chip.active b{color:#cffafe}
.bcard{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:16px 18px;margin-bottom:13px;box-shadow:0 1px 3px #10233b0a}
.bhead{display:flex;align-items:flex-start;gap:10px;flex-wrap:wrap}
.bcat{background:var(--soft);color:var(--brand2);font-size:11px;font-weight:800;border-radius:7px;padding:3px 9px;white-space:nowrap}
.bkw{font-size:16.5px;font-weight:800;letter-spacing:-.3px}
.bbuzz{margin-left:auto;color:var(--ink2);font-size:12px;white-space:nowrap}
.bhl{margin:9px 0 12px;font-size:14px;color:#0b3b39;background:linear-gradient(90deg,#0f766e10,transparent);border-left:3px solid var(--brand);border-radius:0 8px 8px 0;padding:8px 12px;font-weight:600}
.clabel{font-size:11.5px;color:var(--ink2);font-weight:700;margin-bottom:7px}
.cchips{display:flex;gap:8px;flex-wrap:wrap}
.cchip{border:1px solid var(--line);background:#fbfcfe;border-radius:11px;padding:9px 12px;cursor:pointer;transition:.15s;min-width:120px}
.cchip:hover{border-color:var(--brand);box-shadow:0 2px 8px #0f766e1f;transform:translateY(-1px)}
.cchip .cn{font-size:12.5px;font-weight:700;color:var(--ink);line-height:1.35}
.cchip .cp{font-size:12px;color:var(--brand);font-weight:700;margin-top:3px}
.cchip .ci{font-size:10.5px;color:var(--pos);margin-top:2px}.cchip .cx{font-size:10.5px;color:var(--ink2);margin-top:2px}
/* 출처 필터 + 뱃지 + 앞단 인사이트 */
.srcfilter{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:10px}
.sflbl{font-size:12px;color:var(--ink2);font-weight:700}
.sf{background:var(--card);border:1px solid var(--line);color:var(--ink2);font-size:12.5px;font-weight:600;border-radius:999px;padding:6px 13px;cursor:pointer}
.sf.active{background:var(--brand2);color:#fff;border-color:var(--brand2)}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 2px}
.sb{font-size:11px;font-weight:700;border-radius:6px;padding:2px 8px}
.sb.nv{background:#e8f0fe;color:#1a56db}.sb.yt{background:#fde8e8;color:#c81e1e}.sb.pr{background:#e6f4ea;color:#15803d}.sb.sk{background:#f3eefe;color:#6d28d9}
.replead{font-size:12px;font-weight:800;color:var(--ink);margin:11px 0 5px}
.fsec{margin-bottom:5px}.fst{font-size:12.5px;font-weight:700;color:#0b3b39}
.fsec ul{margin:2px 0 0;padding-left:17px}.fsec li{font-size:13px;color:#26405e;margin:2px 0}
.ytbox{margin:7px 0;border:1px solid #fde0e0;background:#fff8f8;border-radius:9px;padding:8px 11px;font-size:12.5px}
.ytbox summary{cursor:pointer;color:#c81e1e;font-weight:700;font-size:12.5px}.ytbox .fst{color:#9a1c1c}
/* modal */
.modal{position:fixed;inset:0;background:#0a1626aa;backdrop-filter:blur(3px);display:none;align-items:flex-start;justify-content:center;z-index:100;padding:38px 16px;overflow:auto}
.modal.open{display:flex}
.sheet{background:#fff;border-radius:18px;max-width:680px;width:100%;box-shadow:0 24px 60px #0a162655;overflow:hidden;animation:pop .18s ease}
@keyframes pop{from{transform:translateY(10px);opacity:.6}to{transform:none;opacity:1}}
.shead{padding:20px 24px;border-bottom:1px solid var(--line);position:relative;background:linear-gradient(135deg,#0f766e0a,#2563eb0a)}
.shead .x{position:absolute;top:16px;right:18px;cursor:pointer;color:var(--ink2);font-size:22px;line-height:1}
.shead .sc{font-size:11px;color:var(--brand2);font-weight:800}
.shead h3{font-size:18px;font-weight:800;margin:3px 0;letter-spacing:-.3px}
.shead .sm{color:var(--ink2);font-size:12.5px}
.sbody{padding:18px 24px 24px;max-height:64vh;overflow:auto}
.priceband{display:flex;align-items:baseline;gap:10px;margin:2px 0 8px}
.priceband .big{font-size:24px;font-weight:800;color:var(--ink)}.priceband .sub{color:var(--ink2);font-size:12.5px}
.ladder{height:8px;background:var(--soft);border-radius:5px;position:relative;margin:6px 0 14px}
.ladder .bar{position:absolute;height:8px;background:linear-gradient(90deg,var(--pos),var(--warn));border-radius:5px}
.offrow{display:flex;align-items:center;gap:10px;font-size:13px;padding:6px 0;border-bottom:1px solid #f1f4f8}
.offrow a{color:var(--brand2);text-decoration:none;font-weight:600;min-width:120px}
.offrow .pp{margin-left:auto;font-weight:800;color:var(--pos)}.offrow .tg{font-size:10px;color:var(--ink2);border:1px solid var(--line);border-radius:4px;padding:0 5px}
.isec{margin:14px 0 0}.isec h4{font-size:13.5px;font-weight:800;color:var(--ink);margin-bottom:6px}
.isec ul{list-style:none}.isec li{font-size:13.5px;color:#26405e;padding:4px 0 4px 16px;position:relative}
.isec li:before{content:'•';position:absolute;left:2px;color:var(--brand)}
.iproof{margin-top:8px;font-size:12px;color:var(--pos);background:#0f766e0d;border-radius:8px;padding:7px 11px;font-weight:600}
.evlink{font-size:11px;color:var(--brand2);text-decoration:none;font-weight:600;white-space:nowrap;margin-left:4px;
        border:1px solid var(--line);border-radius:5px;padding:0 5px}.evlink:hover{background:#eef2f7}
.muted{color:var(--ink2);font-size:12.5px}
.foot{text-align:center;color:var(--ink2);font-size:11.5px;margin-top:24px}
"""

CHART_JS = r"""
const D=__CHART__, B=__BUNDLES__;
function _cv(id,h){const c=document.getElementById(id);if(!c)return null;
  const W=(c.getBoundingClientRect().width)||c.parentElement.clientWidth-40,dpr=devicePixelRatio||1;
  c.style.height=h+'px';c.width=W*dpr;c.height=h*dpr;const x=c.getContext('2d');x.scale(dpr,dpr);return {x,W,H:h};}
const GRID='#e6eaf0',TXT='#5b6b80',INK='#10233b';
function bar(id,labels,vals,c1,c2){const o=_cv(id,300);if(!o)return;const{x,W,H}=o;
  const pl=36,pb=60,pt=16,max=Math.max(...vals,1),n=vals.length,step=(W-pl-6)/n,bw=Math.min(step*0.64,46);
  x.clearRect(0,0,W,H);x.strokeStyle=GRID;x.beginPath();x.moveTo(pl,H-pb);x.lineTo(W,H-pb);x.stroke();
  vals.forEach((v,i)=>{const h=(H-pb-pt)*v/max,px=pl+i*step+(step-bw)/2,py=H-pb-h;
    const g=x.createLinearGradient(0,py,0,H-pb);g.addColorStop(0,c1);g.addColorStop(1,c2);
    x.fillStyle=g;roundRect(x,px,py,bw,Math.max(h,1),4);
    x.fillStyle=INK;x.font='600 10px Inter,sans-serif';x.textAlign='center';x.fillText(v.toLocaleString(),px+bw/2,py-5);
    x.save();x.translate(px+bw/2,H-pb+10);x.rotate(-0.5);x.fillStyle=TXT;x.textAlign='right';x.fillText(labels[i],0,0);x.restore();});}
function roundRect(x,a,b,w,h,r){x.beginPath();x.moveTo(a+r,b);x.arcTo(a+w,b,a+w,b+h,r);x.arcTo(a+w,b+h,a,b+h,r);x.arcTo(a,b+h,a,b,r);x.arcTo(a,b,a+w,b,r);x.fill();}
function hbar(id,items,c1,c2,h){const o=_cv(id,h||(items.length*27+14));if(!o)return;const{x,W,H}=o;
  const pl=Math.min(176,W*0.42),pr=70,rh=(H-10)/items.length,max=Math.max(...items.map(i=>i.value),1);
  x.clearRect(0,0,W,H);x.font='12px Inter,sans-serif';
  items.forEach((it,i)=>{const y=6+i*rh,bw=(W-pl-pr)*it.value/max;
    const g=x.createLinearGradient(pl,0,pl+bw,0);g.addColorStop(0,c1);g.addColorStop(1,c2);
    x.fillStyle=g;roundRect(x,pl,y+rh*0.16,Math.max(bw,3),rh*0.62,3);
    x.fillStyle=INK;x.textAlign='right';x.fillText(it.label.slice(0,16),pl-7,y+rh*0.58);
    x.fillStyle='#0f766e';x.font='700 12px Inter,sans-serif';x.textAlign='left';x.fillText(it.disp||it.value.toLocaleString(),pl+Math.max(bw,3)+6,y+rh*0.58);x.font='12px Inter,sans-serif';});}
function donut(id,pct,label,sub){const o=_cv(id,128);if(!o)return;const{x,W,H}=o;
  const cx=W/2,cy=H/2-8,r=Math.min(W,H-22)/2-6;x.clearRect(0,0,W,H);x.lineWidth=10;x.lineCap='round';
  x.strokeStyle='#eef2f7';x.beginPath();x.arc(cx,cy,r,0,7);x.stroke();
  x.strokeStyle=pct>=66?'#0f766e':(pct>=25?'#b45309':'#dc2626');
  x.beginPath();x.arc(cx,cy,r,-Math.PI/2,-Math.PI/2+Math.max(pct,0.1)/100*2*Math.PI);x.stroke();
  x.fillStyle=INK;x.textAlign='center';x.font='800 17px Inter,sans-serif';x.fillText(pct.toFixed(0)+'%',cx,cy+2);
  x.fillStyle=TXT;x.font='600 10px Inter,sans-serif';x.fillText(sub,cx,cy+16);x.fillText(label,cx,cy+r+15);}
function heatmap(id,data){const rows=data.cats,cols=data.dims,m=data.matrix;const o=_cv(id,rows.length*27+66);if(!o)return;const{x,W,H}=o;
  const pl=Math.min(140,W*0.28),pt=60,cw=(W-pl-6)/cols.length,rh=(H-pt-6)/rows.length;x.clearRect(0,0,W,H);x.font='10px Inter,sans-serif';
  cols.forEach((c,j)=>{x.save();x.translate(pl+j*cw+cw/2,pt-8);x.rotate(-0.6);x.fillStyle=TXT;x.textAlign='left';x.fillText(c.slice(0,8),0,0);x.restore();});
  rows.forEach((r,i)=>{x.fillStyle=INK;x.textAlign='right';x.fillText(r.slice(0,11),pl-5,pt+i*rh+rh*0.62);
    cols.forEach((c,j)=>{const v=m[i][j],px=pl+j*cw,py=pt+i*rh,a=v/100;
      x.fillStyle='rgba(15,118,110,'+(0.06+a*0.9).toFixed(2)+')';roundRect(x,px+1.5,py+1.5,cw-3,rh-3,3);
      if(v>0){x.fillStyle=a>0.5?'#fff':INK;x.textAlign='center';x.fillText(v,px+cw/2,py+rh*0.62);}});});}
function scatter(id,pts){const o=_cv(id,300);if(!o)return;const{x,W,H}=o;const pl=48,pb=36,pt=14,pr=14;
  const xs=pts.map(p=>Math.log10(Math.max(p.x,1))),ys=pts.map(p=>p.y),xmin=Math.min(...xs,0),xmax=Math.max(...xs,1),ymax=Math.max(...ys,1);
  x.clearRect(0,0,W,H);x.strokeStyle=GRID;x.beginPath();x.moveTo(pl,H-pb);x.lineTo(W-pr,H-pb);x.moveTo(pl,pt);x.lineTo(pl,H-pb);x.stroke();
  x.fillStyle=TXT;x.font='10px Inter,sans-serif';x.textAlign='center';x.fillText('언급량(블로그, log) →',W/2,H-4);
  x.save();x.translate(14,H/2);x.rotate(-Math.PI/2);x.fillText('평균가(원)',0,0);x.restore();
  pts.forEach((p,i)=>{const px=pl+(xs[i]-xmin)/((xmax-xmin)||1)*(W-pl-pr),py=H-pb-(ys[i]/ymax)*(H-pb-pt);
    x.fillStyle='rgba(37,99,235,0.5)';x.beginPath();x.arc(px,py,3.6,0,7);x.fill();});}
function _num(n){return n>=10000?(n/10000).toFixed(1)+'만':(n||0).toLocaleString();}
function drawAll(){
  D.extraction.forEach((e,i)=>donut('don'+i,e.total?e.done/e.total*100:0,e.label,e.done.toLocaleString()+'/'+e.total.toLocaleString()));
  bar('catBar',D.cat_dist.map(c=>c.name),D.cat_dist.map(c=>c.n_pkg),'#2563eb','#2563eb33');
  hbar('buzzBar',D.buzz_top.map(b=>({label:b.kw,value:b.blog,disp:_num(b.blog)})),'#7c3aed','#7c3aed33');
  heatmap('heat',D.heatmap);
  bar('priceHist',D.price_hist.labels,D.price_hist.values,'#0f766e','#0f766e33');
  scatter('scat',D.scatter);drawCat();
}
function drawCat(){const s=document.getElementById('catSel');const c=s?s.value:Object.keys(D.cat_rank)[0];const dims=D.cat_rank[c]||[];
  hbar('catDims',dims.map(d=>({label:d.label,value:d.coverage,disp:d.coverage+'%'})),'#0f766e','#0f766e33',dims.length*29+14);}
"""

APP_JS = r"""
const byId=Object.fromEntries(B.map(b=>[b.id,b]));
function won(n){return n==null?'—':'₩'+Number(n).toLocaleString();}
function openCat(bid,cid){const b=byId[bid];if(!b)return;const c=b.catalogs.find(x=>x.id===cid);if(!c)return;
  const ps=c.ps||{};let lad='';
  if(ps.min){const lo=ps.min,hi=ps.max||ps.min;
    lad=`<div class=priceband><span class=big>${won(ps.min)}</span><span class=sub>~ ${won(ps.max)} · ${ps.n_malls||0}개 몰 · 최저 ${ps.low_mall||''}</span></div><div class=ladder><span class=bar style="left:0;width:100%"></span></div>`;
    lad+='<div>'+(c.offers||[]).slice(0,6).map(o=>`<div class=offrow><a href="${o.url||'#'}" target=_blank>${o.mall||''}</a><span class=tg>${o.used?'중고':'새'}</span><span class=pp>${won(o.price)}</span></div>`).join('')+'</div>';
  } else lad='<div class=muted>가격 정보 없음</div>';
  const view=c.insv||b.repv; const src=c.insv?('실측 후기 '+(c.n_sources||view.review_count||0)+'건 · 이 카탈로그'):('제품 대표 인사이트 · 후기 '+(b.repv.review_count||0)+'건');
  let ins='';
  if(view&&view.sections&&view.sections.length){
    if(view.headline) ins+=`<div class=bhl style="margin:0 0 12px">“${esc(view.headline)}”</div>`;
    ins+=view.sections.map(s=>`<div class=isec><h4>${s.emoji} ${esc(s.title)}</h4><ul>${s.items.map(it=>`<li>${esc(it.text)}${evlinks(it.evs)}</li>`).join('')}</ul></div>`).join('');
    ins+=`<div class=iproof>🗣️ ${esc(src)} · 📎 = 원문(블로그/영상) 근거 링크</div>`;
  } else ins='<div class=muted>이 카탈로그 인사이트 추출 예정</div>';
  document.getElementById('msc').textContent=b.cat;
  document.getElementById('mtitle').textContent=c.name||b.kw;
  document.getElementById('msub').textContent=[c.size,c.count].filter(Boolean).join(' · ')+(c.id?(' · ctlg '+c.id):'');
  document.getElementById('mbody').innerHTML=lad+ins;
  document.getElementById('modal').classList.add('open');
}
function closeM(){document.getElementById('modal').classList.remove('open');}
function esc(s){return (s||'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
function evlinks(evs){if(!evs||!evs.length)return '';
  return ' '+evs.map(e=>{if(!e||!e.url)return '';const lbl=e.src==='youtube'?'영상':(e.src==='danawa'?'다나와':'블로그');
    return `<a class=evlink href="${esc(e.url)}" target=_blank rel=noopener title="${esc(e.quote||'')}">📎${lbl}</a>`;}).join('');}
let drawn=false;
function show(v){document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
  document.getElementById('view-'+v).classList.add('active');document.getElementById('tab-'+v).classList.add('active');
  if(v==='charts'){drawAll();drawn=true;}}
// 브라우저 필터
window.addEventListener('load',()=>{
  document.getElementById('modal').addEventListener('click',e=>{if(e.target.id==='modal')closeM();});
  const cards=[...document.querySelectorAll('.bcard')],chips=[...document.querySelectorAll('.chip')],sfs=[...document.querySelectorAll('.sf')];
  let cat='all',q='',srcf='all';
  function apply(){let n=0;cards.forEach(c=>{const ok=(cat==='all'||c.dataset.cat===cat)&&(q===''||c.dataset.kw.includes(q))&&(srcf==='all'||c.dataset[srcf]==='1');c.style.display=ok?'':'none';if(ok)n++;});document.getElementById('cnt').textContent=n;}
  chips.forEach(ch=>ch.onclick=()=>{chips.forEach(x=>x.classList.remove('active'));ch.classList.add('active');cat=ch.dataset.c;apply();});
  sfs.forEach(s=>s.onclick=()=>{sfs.forEach(x=>x.classList.remove('active'));s.classList.add('active');srcf=s.dataset.s;apply();});
  document.getElementById('q').oninput=e=>{q=e.target.value.trim().toLowerCase();apply();};
  show('charts');
});
window.addEventListener('resize',()=>{if(document.getElementById('view-charts').classList.contains('active'))drawAll();});
"""


def _e(s):
    return html.escape(str(s or ""))


def _evlinks_html(evs):
    """카드 앞단 근거 링크(서버 렌더). 모달은 JS evlinks 사용."""
    if not evs:
        return ""
    out = []
    for e in evs:
        if not e.get("url"):
            continue
        lbl = "영상" if e.get("src") == "youtube" else ("다나와" if e.get("src") == "danawa" else "블로그")
        out.append(f"<a class=evlink href='{_e(e['url'])}' target=_blank rel=noopener "
                   f"title='{_e(e.get('quote') or '')}'>📎{lbl}</a>")
    return " " + "".join(out)


def _sections_html(view, max_sec=3, max_items=2):
    """대표 비정형 섹션 앞단 렌더(근거 링크 포함)."""
    if not view or not view.get("sections"):
        return "<div class=muted style='font-size:12px'>표시할 인사이트 없음</div>"
    out = []
    for s in view["sections"][:max_sec]:
        items = "".join(f"<li>{_e(it['text'])}{_evlinks_html(it.get('evs'))}</li>" for it in s["items"][:max_items])
        out.append(f"<div class=fsec><span class=fst>{s['emoji']} {_e(s['title'])}</span><ul>{items}</ul></div>")
    return "".join(out)


def bundle_card(b):
    hl = (b["repv"].get("headline") or "")
    blog = (b["buzz"] or {}).get("blog")
    src = b.get("src") or {}
    badges = []
    if src.get("naver"):
        badges.append("<span class='sb nv'>📝 블로그 근거</span>")
    if src.get("youtube"):
        badges.append("<span class='sb yt'>🎬 유튜브</span>")
    if src.get("price"):
        badges.append("<span class='sb pr'>💰 가격</span>")
    if src.get("skuins"):
        badges.append("<span class='sb sk'>● SKU 인사이트</span>")
    buzz = f"📣 {rs_num(blog)}" if blog is not None else ""
    rep_html = _sections_html(b["repv"])
    yt_html = (f"<details class=ytbox><summary>🎬 유튜브 댓글에서 나온 이야기</summary>{_sections_html(b['ytv'], 4, 2)}</details>"
               if b.get("ytv") and b["ytv"].get("sections") else "")
    chips = []
    for c in b["catalogs"]:
        ps = c.get("ps") or {}
        price = f"<div class=cp>₩{ps['min']:,}~{ps['max']:,}</div>" if ps.get("min") else "<div class=cp style='color:#9aa3af'>가격 없음</div>"
        ins = "<div class=ci>● 실측 인사이트</div>" if c.get("insv") else "<div class=cx>○ 제품 인사이트</div>"
        nm = _e((c.get("name") or "")[:34])
        chips.append(f"<div class=cchip onclick=\"openCat('{_e(b['id'])}','{_e(c['id'])}')\">"
                     f"<div class=cn>{nm}</div>{price}{ins}</div>")
    return (f"<div class=bcard data-cat=\"{_e(b['cat'])}\" data-kw=\"{_e((b['kw'] or '').lower())}\" "
            f"data-yt=\"{1 if src.get('youtube') else 0}\" data-price=\"{1 if src.get('price') else 0}\" "
            f"data-sku=\"{1 if src.get('skuins') else 0}\">"
            f"<div class=bhead><span class=bcat>{_e(b['cat'])}</span><span class=bkw>{_e(b['kw'])}</span>"
            f"<span class=bbuzz>{buzz}</span></div>"
            f"<div class=badges>{''.join(badges)}</div>"
            f"{f'<div class=bhl>“{_e(hl)}”</div>' if hl else ''}"
            f"<div class=replead>🛒 대표 비정형 인사이트 <span class=muted>· 출처 📎 클릭</span></div>{rep_html}{yt_html}"
            f"<div class=clabel>📦 하위 카탈로그 {len(b['catalogs'])}개 · 클릭하면 가격·판매처·카탈로그별 인사이트</div>"
            f"<div class=cchips>{''.join(chips)}</div></div>")


def rs_num(n):
    if not isinstance(n, (int, float)):
        return "—"
    return f"{n/10000:.1f}만" if n >= 10000 else f"{int(n):,}"


def render(bundles, chart):
    s = chart["summary"]
    _yt = s.get("yt_done", 0)
    yt_txt = (f"유튜브 {_yt}개 패키지 수집·분석(쿼터 한도)" if _yt else "유튜브는 쿼터 한도로 미수집(0%)")
    cat_txt = (f"카테고리 {s.get('cat_real', 0)}/{s['packages']} 네이버 실측"
               + (f"·{s.get('cat_demo', 0)}개 데모폴백(미분류)" if s.get("cat_demo") else ""))
    cards = sorted(bundles, key=lambda b: (b["cat"] or "", b["kw"] or ""))
    cat_list = sorted({b["cat"] for b in cards if b["cat"]})
    kpis = "".join(f"<div class=kpi><div class=v>{v}</div><div class=l>{l}</div></div>" for l, v in [
        ("패키지(상품)", f"{s['packages']:,}"), ("하위 카탈로그(SKU)", f"{s['catalogs']:,}"),
        ("크로스몰 판매처 offers", f"{s['offers']:,}"), ("카테고리", f"{s['categories']:,}"),
        ("가격 수집", f"{s['priced']:,}<small>/{s['packages']}</small>"),
        ("비정형 인사이트", f"{s['pkg_ins']:,}<small>/{s['packages']}</small>")])
    donuts = "".join(f"<div><canvas id=don{i}></canvas></div>" for i in range(len(chart["extraction"])))
    cat_opts = "".join(f"<option>{_e(c)}</option>" for c in chart["cat_rank"])
    chips = (f"<button class='chip active' data-c=all>전체 <b>{len(cards)}</b></button>"
             + "".join(f"<button class=chip data-c=\"{_e(c)}\">{_e(c)} <b>{sum(1 for b in cards if b['cat']==c)}</b></button>" for c in cat_list))
    charts = f"""
    <div class=panel><div class=section-title>📊 직접 수집 현황 <span class=muted style="font-weight:600">— 전부 실측</span></div>
      <div class=d>어디까지 직접 뽑혔는지 (도넛 = 완료율)</div><div class=donuts>{donuts}</div></div>
    <div class=grid2>
      <div class=panel><div class=section-title>📦 카테고리별 패키지 수</div><div class=d>&nbsp;</div><canvas id=catBar></canvas></div>
      <div class=panel><div class=section-title>📣 네이버 블로그 매칭 글 수 Top 15</div><div class=d>풀네임 검색 매칭 글 수 — 일반어(예: 김치찌개) 포함 광범위 매칭이라 정확한 '상품 언급량'은 아님</div><canvas id=buzzBar></canvas></div></div>
    <div class=panel><div class=section-title>🏷️ 카테고리별 대표 속성 coverage</div>
      <div class=d>그 카테고리 번들 중 몇 %가 그 속성을 언급하나</div><select id=catSel onchange=drawCat()>{cat_opts}</select><canvas id=catDims></canvas></div>
    <div class=panel><div class=section-title>🌡️ 카테고리 × 속성 히트맵</div>
      <div class=d>행=카테고리 · 열=속성 · 진할수록 coverage 높음</div><canvas id=heat></canvas></div>
    <div class=grid2>
      <div class=panel><div class=section-title>💰 가격대 분포</div><div class=d>카탈로그 median (개수매칭·이상치 정정 후)</div><canvas id=priceHist></canvas></div>
      <div class=panel><div class=section-title>🔀 블로그 매칭 글 수 ↔ 가격</div><div class=d>블로그 매칭 글 수 상위 150 패키지 vs 평균가 (전체 아님)</div><canvas id=scat></canvas></div></div>
    """
    srcfilter = ("<div class=srcfilter><span class=sflbl>출처</span>"
                 "<button class='sf active' data-s=all>전체</button>"
                 "<button class=sf data-s=yt>🎬 유튜브</button>"
                 "<button class=sf data-s=price>💰 가격</button>"
                 "<button class=sf data-s=sku>● SKU 인사이트</button></div>")
    browser = (f"<div class=controls><input id=q placeholder='상품명 검색…'><div class=muted>표시 <b id=cnt>{len(cards)}</b>개</div></div>"
               f"{srcfilter}"
               f"<div class=chips style='margin-bottom:14px'>{chips}</div>"
               + "".join(bundle_card(b) for b in cards))
    chart_js = (CHART_JS.replace("__CHART__", json.dumps(chart, ensure_ascii=False).replace("</", "<\\/"))
                .replace("__BUNDLES__", json.dumps(bundles, ensure_ascii=False).replace("</", "<\\/")))
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=robots content='noindex,nofollow,noarchive'>"
            f"<title>상품 인사이트 리포트</title><style>{CSS}</style></head><body><div class=wrap>"
            f"<header class=top><div class=logo></div><h1>상품 인사이트 인텔리전스</h1>"
            f"<div class=meta>비정형 인사이트 · 크로스몰 가격 · 언급량<br>네이버 리뷰·쇼핑 직접 수집 실측 "
            f"{('· 유튜브 '+format(_yt,',')+'건' if _yt else '(유튜브 미수집)')}</div></header>"
            f"<div class=hero><h2>상품 한 노드 = 정성 인사이트 + 가격 정체성 + 시장 관심</h2>"
            f"<p>네이버 리뷰·쇼핑을 직접 수집해 비정형 인사이트와 크로스몰 가격을 한 화면에. "
            f"<b>{yt_txt}</b> · {cat_txt} · 추출 데이터 합성 없음.</p>"
            f"<div class=srcline>📎 데이터 출처: <b>인사이트</b>=네이버 블로그 리뷰·유튜브 댓글(카탈로그 클릭→각 항목 📎 원문 링크) · "
            f"<b>가격</b>=네이버 쇼핑 크로스몰(판매처별 링크) · <b>언급량</b>=네이버 블로그/쇼핑 검색 API</div>"
            f"<div class=kpis>{kpis}</div></div>"
            f"<div class=tabs><button class=tab id=tab-charts onclick=show('charts')>📊 대시보드</button>"
            f"<button class=tab id=tab-browser onclick=show('browser')>🗂️ 상품 브라우저</button></div>"
            f"<div class='view' id=view-charts>{charts}</div>"
            f"<div class='view' id=view-browser>{browser}</div>"
            f"<div class=foot>네이버 리뷰·쇼핑 직접 수집 실측 · {cat_txt} · {yt_txt} · 합성 0 · 단일 HTML</div></div>"
            f"<div class=modal id=modal><div class=sheet><div class=shead><span class=x onclick=closeM()>✕</span>"
            f"<div class=sc id=msc></div><h3 id=mtitle></h3><div class=sm id=msub></div></div>"
            f"<div class=sbody id=mbody></div></div></div>"
            f"<script>{chart_js}\n{APP_JS}</script></body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="data/exec_report.html")
    ap.add_argument("--per-cat", type=int, default=30)
    ap.add_argument("--max-cards", type=int, default=250)
    ap.add_argument("--skus", type=int, default=10)
    args = ap.parse_args()
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]
    chart = rs.gather_charts(db)
    bundles = gather_bundles(db, args.per_cat, args.max_cards, args.skus)
    n_ci = sum(b["n_cat_ins"] for b in bundles)
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(render(bundles, chart))
    print(f"리포트 생성 → {args.html} · 번들 {len(bundles)} · 카탈로그 인사이트 {n_ci}개 · 단일 HTML")


if __name__ == "__main__":
    main()
