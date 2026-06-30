#!/usr/bin/env python3
"""identity 보정 콕핏 대시보드 (Phase 2, 읽기 전용).

identity_guidelines / identity_calib_runs / identity_labels (Mongo) → 단일 HTML.
카테고리별 상태(임계·판정·정확도·라벨수) 표 + 선택 시 최근 sweep/판정/이력.
Phase 3 에서 같은 페이지에 라벨링 UI(/calib/*)를 얹는다.

  MONGO_URI=.. INSIGHTS_DB=insights_demo python3 db/identity_dashboard.py [--out db/exports/identity_calib.html]
"""
import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import identity_guidelines_db as gdb

PAGE = """<!doctype html><html lang=ko><meta charset=utf-8>
<title>identity 보정 콕핏</title>
<style>
 body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f7f9;color:#1c1e21}
 header{background:#1c2733;color:#fff;padding:16px 24px}
 header h1{margin:0;font-size:18px} header .sub{opacity:.7;font-size:12px;margin-top:4px}
 .wrap{display:flex;gap:16px;padding:20px;align-items:flex-start;flex-wrap:wrap}
 .card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .kpis{display:flex;gap:12px;padding:16px 24px;flex-wrap:wrap}
 .kpi{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:12px 18px;min-width:120px}
 .kpi b{display:block;font-size:22px} .kpi span{font-size:12px;color:#65676b}
 table{border-collapse:collapse;width:100%} th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #eef0f2}
 th{font-size:12px;color:#65676b;font-weight:600} tbody tr{cursor:pointer} tbody tr:hover{background:#f0f7ff}
 tbody tr.sel{background:#e7f1ff}
 .chip{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600}
 .ok{background:#e3f6e8;color:#1a7f37} .warn{background:#fdeceb;color:#b42318} .mut{background:#eef0f2;color:#65676b}
 .num{font-variant-numeric:tabular-nums} .right{text-align:right}
 #detail{flex:1;min-width:340px} #detail h3{margin:0 0 4px} #detail .meta{color:#65676b;font-size:12px;margin-bottom:12px}
 .pad{padding:16px 20px} .empty{color:#9aa0a6;padding:40px;text-align:center}
 .bar{height:6px;border-radius:4px;background:#e3e6ea;overflow:hidden;width:80px;display:inline-block;vertical-align:middle}
 .bar i{display:block;height:100%;background:#2d7ff9}
 .hist{font-size:12px;color:#65676b} .hist div{padding:2px 0;border-bottom:1px dashed #eef0f2}
</style>
<header><h1>identity 매칭 보정 콕핏</h1><div class=sub>__SUB__</div></header>
<div class=kpis>__KPIS__</div>
<div class=wrap>
 <div class="card pad" style="flex:1;min-width:520px">
  <table><thead><tr><th>카테고리</th><th>판정</th><th class=right>임계</th><th class=right>P</th>
   <th class=right>R</th><th class=right>라벨</th><th>갱신</th></tr></thead><tbody id=rows></tbody></table>
 </div>
 <div class="card pad" id=detail><div class=empty>카테고리를 선택하세요</div></div>
</div>
<script>
const DATA = __DATA__;
const pct=v=>v==null?'-':(v*100).toFixed(0)+'%';
const chip=s=>({effective:['ok','튜닝가능'],needs_strong_key:['warn','강키필요'],imported:['mut','미보정'],unset:['mut','미설정']}[s]||['mut',s||'-']);
function rows(){
 document.getElementById('rows').innerHTML = DATA.guidelines.map((g,i)=>{
  const [c,t]=chip(g.status);
  return `<tr data-i="${i}" onclick="sel(${i})"><td>${g._id}</td>
   <td><span class="chip ${c}">${t}</span></td>
   <td class="right num">${g.name_thresh==null?'<span style=color:#9aa0a6>default</span>':g.name_thresh}</td>
   <td class="right num">${pct(g.precision)}</td><td class="right num">${pct(g.recall)}</td>
   <td class="right num">${g.n_labels||0}</td><td class=hist>${(g.updated_at||'').slice(0,10)}</td></tr>`;
 }).join('');
}
function sel(i){
 document.querySelectorAll('#rows tr').forEach(r=>r.classList.toggle('sel',r.dataset.i==i));
 const g=DATA.guidelines[i], runs=DATA.runs[g._id]||[], last=runs[0];
 let h=`<h3>${g._id}</h3><div class=meta>판정 ${chip(g.status)[1]} · 임계 ${g.name_thresh==null?'default':g.name_thresh} · 라벨 ${g.n_labels||0} · by ${g.updated_by||'-'}</div>`;
 if(last){
  h+=`<table><thead><tr><th class=right>임계</th><th class=right>P</th><th class=right>R</th><th class=right>F1</th><th class=right>TP</th><th class=right>FP</th><th class=right>FN</th></tr></thead><tbody>`;
  h+=last.sweep.map(s=>`<tr><td class="right num">${s.thr}</td><td class="right num">${pct(s.p)}</td><td class="right num">${pct(s.r)}</td><td class="right num">${pct(s.f1)}</td><td class="right num">${s.tp}</td><td class="right num">${s.fp}</td><td class="right num">${s.fn}</td></tr>`).join('');
  h+=`</tbody></table>`;
  if(g.status=='needs_strong_key') h+=`<p style="color:#b42318;font-size:13px;margin-top:10px">⚠ precision 평탄 — 임계 튜닝 무효. 씨앗에 color/style_code/barcode(강키) 필요.</p>`;
  h+=`<div class=hist style=margin-top:14px><b>보정 이력</b>`+runs.map(r=>`<div>${(r.ts||'').slice(0,16).replace('T',' ')} · ${r.verdict} · 추천 ${r.recommended==null?'-':r.recommended} · 라벨 ${r.n_labels}${r.applied?' · ✅적용':''}</div>`).join('')+`</div>`;
 } else h+=`<div class=empty>보정 이력 없음 — review/recommend 실행</div>`;
 document.getElementById('detail').innerHTML=h;
}
rows();
</script></html>"""


def build(db):
    guidelines = sorted(gdb.all_guidelines(db), key=lambda g: g["_id"])
    runs = {}
    for g in guidelines:
        rs = gdb.get_calib_runs(db, g["_id"], limit=10)
        for r in rs:
            r.pop("_id", None)
        runs[g["_id"]] = rs
    # guidelines 의 _id(=category) 는 JS 가 g._id 로 쓰므로 보존(string → json 직렬화 OK)
    n_eff = sum(1 for g in guidelines if g.get("status") == "effective")
    n_nsk = sum(1 for g in guidelines if g.get("status") == "needs_strong_key")
    n_lab = sum(g.get("n_labels") or 0 for g in guidelines)
    kpis = [("카테고리", len(guidelines)), ("튜닝가능", n_eff), ("강키필요", n_nsk),
            ("총 라벨", n_lab)]
    kpis_html = "".join(f"<div class=kpi><b>{v}</b><span>{k}</span></div>" for k, v in kpis)
    data = {"guidelines": guidelines, "runs": runs}
    sub = f"insights_demo · 가이드라인 {len(guidelines)} · 라벨 {n_lab} · 강키필요 {n_nsk}"
    return (PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False, default=str))
                .replace("__KPIS__", kpis_html).replace("__SUB__", sub))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "exports", "identity_calib.html"))
    args = ap.parse_args()
    db = gdb.get_db()
    html = build(db)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"보정 대시보드 → {args.out} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
