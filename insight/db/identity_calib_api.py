#!/usr/bin/env python3
"""identity 보정 API (Phase 3) — 트리거서버 /calib/* 가 호출하는 인프로세스 로직 + 라벨링 UI.

함수:
  status(db)                          → 가이드라인 일람
  queue(db, category, n, source, ext) → 라벨링 후보(미라벨만). perturb=교란 자동제안 / mongo=실 product
  save_label(db, payload)             → 사람 라벨 DB 적재(overwrite)
  recommend(db, category, apply, by)  → recommend_core 위임(sweep+DB 기록)
UI: PAGE(인터랙티브 HTML) — /calib/ui 로 서빙, same-origin fetch 로 위 엔드포인트 호출.
"""
import os
import sys
import random

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import identity_guidelines_db as gdb
import identity_calibrate as cal
from identity_seed_match import _content_bigrams, _content_toks

_EXT = None
_EXTBG = None


def _load_ext(path):
    global _EXT, _EXTBG
    if _EXT is None:
        _EXT = cal._read_extracted(path)
        _EXTBG = [(i, _content_bigrams(r.get("name") or "")) for i, r in enumerate(_EXT)]
    return _EXT, _EXTBG


def status(db):
    return {"guidelines": gdb.all_guidelines(db)}


def queue(db, category, n, source, extracted_path):
    ext, extbg = _load_ext(extracted_path)
    labeled = {(l["seed_disp"], l.get("cand_name")) for l in gdb.get_labels(db, category)}
    rng = random.Random(42)
    out = []
    if source == "perturb":
        pool = [i for i, r in enumerate(ext) if r.get("style_code") and r.get("name")]
        rng.shuffle(pool)
        for i in pool:
            if len(out) >= n:
                break
            disp = cal._perturb(ext[i]["name"], rng)
            bi, score = cal._best(disp, extbg)
            if bi is None:
                continue
            cand = ext[bi]
            if (disp, cand.get("name")) in labeled:
                continue
            out.append({"category": category, "seed_uid": None, "seed_disp": disp,
                        "cand_name": cand.get("name"), "cand_brand": cand.get("brand"),
                        "cand_style_code": cand.get("style_code"), "score": round(score, 3),
                        "suggested": 1 if cand.get("style_code") == ext[i].get("style_code") else 0})
    else:  # mongo: 실 product
        prods = list(db.products.find({"category_l1": category, "type": "package"},
                                      {"keyword": 1}).limit(n * 4))
        for p in prods:
            if len(out) >= n:
                break
            disp = p.get("keyword") or ""
            if len(_content_toks(disp)) < 1:
                continue
            bi, score = cal._best(disp, extbg)
            if bi is None:
                continue
            cand = ext[bi]
            if (disp, cand.get("name")) in labeled:
                continue
            out.append({"category": category, "seed_uid": p["_id"], "seed_disp": disp,
                        "cand_name": cand.get("name"), "cand_brand": cand.get("brand"),
                        "cand_style_code": cand.get("style_code"), "score": round(score, 3),
                        "suggested": None})
    return {"category": category, "source": source, "candidates": out}


def save_label(db, payload):
    cat = payload.get("category")
    if not cat or "label" not in payload or "seed_disp" not in payload:
        return {"error": "category/seed_disp/label 필요"}
    n = gdb.add_labels(db, cat, [payload], source="human",
                       by=payload.get("by", "web"), overwrite=True)
    return {"saved": n, "category": cat}


def recommend(db, category, apply, by):
    return cal.recommend_core(db, category, apply=apply, by=by)


PAGE = """<!doctype html><html lang=ko><meta charset=utf-8><title>identity 보정 라벨러</title>
<style>
 body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f7f9;color:#1c1e21}
 header{background:#1c2733;color:#fff;padding:14px 22px;display:flex;justify-content:space-between;align-items:center}
 header h1{margin:0;font-size:17px}
 .wrap{display:flex;gap:16px;padding:18px;align-items:flex-start;flex-wrap:wrap}
 .card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px 18px}
 table{border-collapse:collapse;width:100%} th,td{padding:8px 11px;border-bottom:1px solid #eef0f2;text-align:left}
 th{font-size:12px;color:#65676b} tbody tr{cursor:pointer} tbody tr:hover{background:#f0f7ff} tbody tr.sel{background:#e7f1ff}
 .chip{padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}
 .ok{background:#e3f6e8;color:#1a7f37}.warn{background:#fdeceb;color:#b42318}.mut{background:#eef0f2;color:#65676b}
 .right{text-align:right;font-variant-numeric:tabular-nums}
 .qcard{border:1px solid #e3e6ea;border-radius:10px;padding:12px 14px;margin-bottom:10px}
 .qcard .s{color:#65676b;font-size:12px} .qcard .vs{margin:6px 0;font-size:15px}
 .btn{border:0;border-radius:8px;padding:7px 16px;font-weight:600;cursor:pointer;margin-right:8px}
 .yes{background:#1a7f37;color:#fff}.no{background:#b42318;color:#fff}.go{background:#2d7ff9;color:#fff}
 .sug{font-size:12px;color:#9aa0a6} #q{flex:1;min-width:360px} #cats{min-width:420px}
 .empty{color:#9aa0a6;padding:24px;text-align:center}
</style>
<header><h1>identity 매칭 보정 라벨러</h1><span id=hd class=sug></span></header>
<div class=wrap>
 <div class="card" id=cats>
  <div style=margin-bottom:8px><b>카테고리</b>
   <select id=src style=float:right><option value=perturb>perturb(자동제안)</option><option value=mongo>mongo(실상품)</option></select></div>
  <table><thead><tr><th>카테고리</th><th>판정</th><th class=right>임계</th><th class=right>P</th><th class=right>라벨</th></tr></thead><tbody id=rows></tbody></table>
 </div>
 <div class="card" id=q><div class=empty>카테고리를 선택하면 라벨링 큐가 뜹니다</div></div>
</div>
<script>
const pct=v=>v==null?'-':(v*100).toFixed(0)+'%';
const chip=s=>({effective:['ok','튜닝가능'],needs_strong_key:['warn','강키필요'],imported:['mut','미보정']}[s]||['mut',s||'-']);
let CUR=null;
async function loadStatus(){
 const d=await (await fetch('/calib/status')).json();
 document.getElementById('rows').innerHTML=d.guidelines.sort((a,b)=>a._id<b._id?-1:1).map(g=>{
  const[c,t]=chip(g.status);
  return `<tr onclick="pick('${g._id.replace(/'/g,"\\'")}')"><td>${g._id}</td><td><span class="chip ${c}">${t}</span></td>
   <td class=right>${g.name_thresh==null?'default':g.name_thresh}</td><td class=right>${pct(g.precision)}</td><td class=right>${g.n_labels||0}</td></tr>`;
 }).join('');
}
async function pick(cat){
 CUR=cat;
 const src=document.getElementById('src').value;
 document.getElementById('q').innerHTML='<div class=empty>큐 로딩…</div>';
 const d=await (await fetch(`/calib/queue?category=${encodeURIComponent(cat)}&n=15&source=${src}`)).json();
 renderQ(cat,d.candidates||[]);
}
function renderQ(cat,cands){
 let h=`<div style=margin-bottom:10px><b>${cat}</b> · 라벨링 큐 ${cands.length}건
   <button class="btn go" style=float:right onclick="recommend('${cat.replace(/'/g,"\\'")}')">추천 임계 계산</button></div>`;
 if(!cands.length) h+='<div class=empty>미라벨 후보 없음</div>';
 cands.forEach((c,i)=>{ h+=`<div class=qcard id=qc${i}>
   <div class=s>score ${c.score} ${c.suggested!=null?'· 제안 '+(c.suggested?'맞음':'틀림'):''}</div>
   <div class=vs>씨앗: <b>${c.seed_disp}</b></div>
   <div class=vs>산출: ${c.cand_name||''} <span class=sug>[${c.cand_brand||''} / ${c.cand_style_code||''}]</span></div>
   <button class="btn yes" onclick='lab(${i},1)'>맞음</button>
   <button class="btn no" onclick='lab(${i},0)'>틀림</button></div>`; });
 document.getElementById('q').innerHTML=h;
 window._Q=cands;
}
async function lab(i,label){
 const c=window._Q[i];
 await fetch('/calib/label',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({...c,label,by:'web'})});
 const el=document.getElementById('qc'+i); el.style.opacity=.4;
 el.querySelector('.s').innerHTML+=` · 저장 ${label?'맞음':'틀림'} ✓`;
}
async function recommend(cat){
 const r=await (await fetch(`/calib/recommend?category=${encodeURIComponent(cat)}&apply=1&by=web`,{method:'POST'})).json();
 if(r.error){alert('라벨 필요: '+r.error);return;}
 alert(`${cat} 추천: ${r.recommended==null?'미설정(default) — '+r.verdict:r.recommended} (F1 ${pct(r.best_f1)}) · 라벨 ${r.n_labels}`);
 loadStatus();
}
loadStatus();
</script></html>"""
