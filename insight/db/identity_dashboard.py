#!/usr/bin/env python3
"""identity 보정 콕핏 대시보드 (Phase 2, 읽기 전용) — crypto-dashboard 다크 테마.

identity_guidelines / identity_calib_runs / identity_labels (Mongo) → 단일 HTML.
카테고리별 상태(임계·판정·정확도·라벨수) + 선택 시 최근 sweep/판정/이력.

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
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>
 :root{--bg:#0a0e17;--panel:#121826;--panel2:#0f1420;--bd:#1e2737;--tx:#e8eef9;--mut:#7c8aa3;
   --g1:#7c5cff;--g2:#3f8cff;--ok:#22d3a5;--okbg:#0e2a26;--warn:#ff6b8a;--warnbg:#2a1320;--mutc:#5b6b7b}
 *{box-sizing:border-box}
 body{font:14px/1.55 Inter,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:
   radial-gradient(1200px 600px at 80% -10%,#16213a 0,transparent 60%),var(--bg);color:var(--tx);min-height:100vh}
 .top{display:flex;align-items:center;justify-content:space-between;padding:20px 28px}
 .brand{display:flex;align-items:center;gap:12px}
 .logo{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,var(--g1),var(--g2));
   box-shadow:0 6px 20px rgba(124,92,255,.45)}
 .brand h1{margin:0;font-size:17px;font-weight:700;letter-spacing:-.2px}
 .brand .sub{color:var(--mut);font-size:12px;margin-top:2px}
 .live{display:flex;align-items:center;gap:8px;color:var(--mut);font-size:12px;background:var(--panel);
   border:1px solid var(--bd);padding:7px 13px;border-radius:30px}
 .dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 4px rgba(34,211,165,.18)}
 .kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:8px 28px 4px}
 .kpi{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--bd);
   border-radius:18px;padding:18px 20px;position:relative;overflow:hidden}
 .kpi.feat{background:linear-gradient(135deg,rgba(124,92,255,.22),rgba(63,140,255,.10)),var(--panel)}
 .kpi .lbl{color:var(--mut);font-size:12px;font-weight:500;text-transform:uppercase;letter-spacing:.6px}
 .kpi .val{font-size:30px;font-weight:800;margin-top:6px;font-variant-numeric:tabular-nums;letter-spacing:-1px}
 .kpi .spark{position:absolute;right:14px;bottom:12px;opacity:.5}
 .wrap{display:grid;grid-template-columns:1.35fr 1fr;gap:16px;padding:16px 28px 28px;align-items:start}
 .card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--bd);
   border-radius:18px;padding:8px 8px 4px}
 .card h2{font-size:13px;color:var(--mut);font-weight:600;margin:12px 14px 8px;text-transform:uppercase;letter-spacing:.5px}
 table{border-collapse:separate;border-spacing:0;width:100%}
 th{font-size:11px;color:var(--mut);font-weight:600;text-align:left;padding:8px 14px;text-transform:uppercase;letter-spacing:.4px}
 td{padding:11px 14px;border-top:1px solid var(--bd);font-size:13.5px}
 tbody tr{cursor:pointer;transition:background .12s} tbody tr:hover{background:rgba(124,92,255,.07)}
 tbody tr.sel{background:rgba(124,92,255,.14)}
 .cat{font-weight:600}
 .chip{padding:3px 10px;border-radius:30px;font-size:11.5px;font-weight:600;display:inline-block}
 .ok{background:var(--okbg);color:var(--ok)} .warn{background:var(--warnbg);color:var(--warn)}
 .mut{background:#1a2230;color:var(--mut)}
 .right{text-align:right;font-variant-numeric:tabular-nums}
 .pbar{height:6px;width:64px;border-radius:6px;background:#1a2230;display:inline-block;vertical-align:middle;overflow:hidden}
 .pbar i{display:block;height:100%;background:linear-gradient(90deg,var(--g1),var(--g2))}
 #detail{padding:16px 18px} #detail .empty{color:var(--mut);text-align:center;padding:48px}
 #detail h3{margin:0 0 2px;font-size:18px;font-weight:700} #detail .meta{color:var(--mut);font-size:12.5px;margin-bottom:14px}
 .swp td,.swp th{padding:7px 10px;font-size:12.5px} .swp td{border-top:1px solid var(--bd)}
 .hist{margin-top:14px} .hist b{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}
 .hist div{font-size:12.5px;color:var(--mut);padding:6px 0;border-bottom:1px dashed var(--bd)}
 .warnmsg{color:var(--warn);font-size:12.5px;background:var(--warnbg);border:1px solid #3a1d2b;border-radius:10px;padding:9px 12px;margin-top:10px}
 @media(max-width:980px){.kpis{grid-template-columns:repeat(2,1fr)}.wrap{grid-template-columns:1fr}}
</style>
<div class=top>
 <div class=brand><div class=logo></div><div><h1>identity 매칭 보정 콕핏</h1><div class=sub>__SUB__</div></div></div>
 <div class=live><span class=dot></span>insights_demo · live</div>
</div>
<div class=kpis>__KPIS__</div>
<div class=wrap>
 <div class=card><h2>카테고리 보정 상태</h2>
  <table><thead><tr><th>카테고리</th><th>판정</th><th class=right>임계</th><th class=right>precision</th>
   <th class=right>라벨</th><th class=right>갱신</th></tr></thead><tbody id=rows></tbody></table></div>
 <div class=card id=detail><div class=empty>카테고리를 선택하세요</div></div>
</div>
<script>
const DATA=__DATA__;
const pct=v=>v==null?'-':(v*100).toFixed(0)+'%';
const chip=s=>({effective:['ok','튜닝가능'],needs_strong_key:['warn','강키필요'],imported:['mut','미보정'],unset:['mut','미설정']}[s]||['mut',s||'-']);
function rows(){document.getElementById('rows').innerHTML=DATA.guidelines.map((g,i)=>{const[c,t]=chip(g.status);
 const p=g.precision==null?0:g.precision;
 return `<tr data-i="${i}" onclick="sel(${i})"><td class=cat>${g._id}</td>
  <td><span class="chip ${c}">${t}</span></td>
  <td class=right>${g.name_thresh==null?'<span style=color:#5b6b7b>default</span>':g.name_thresh}</td>
  <td class=right><span class=pbar><i style="width:${p*100}%"></i></span> ${pct(g.precision)}</td>
  <td class=right>${g.n_labels||0}</td><td class=right style=color:#7c8aa3>${(g.updated_at||'').slice(5,10)}</td></tr>`;}).join('');}
function sel(i){document.querySelectorAll('#rows tr').forEach(r=>r.classList.toggle('sel',r.dataset.i==i));
 const g=DATA.guidelines[i],runs=DATA.runs[g._id]||[],last=runs[0];
 let h=`<h3>${g._id}</h3><div class=meta>판정 ${chip(g.status)[1]} · 임계 ${g.name_thresh==null?'default':g.name_thresh} · 라벨 ${g.n_labels||0} · by ${g.updated_by||'-'}</div>`;
 if(last){h+=`<table class=swp><thead><tr><th class=right>임계</th><th class=right>P</th><th class=right>R</th><th class=right>F1</th><th class=right>TP</th><th class=right>FP</th><th class=right>FN</th></tr></thead><tbody>`;
  h+=last.sweep.map(s=>`<tr><td class=right>${s.thr}</td><td class=right>${pct(s.p)}</td><td class=right>${pct(s.r)}</td><td class=right>${pct(s.f1)}</td><td class=right>${s.tp}</td><td class=right>${s.fp}</td><td class=right>${s.fn}</td></tr>`).join('')+`</tbody></table>`;
  if(g.status=='needs_strong_key')h+=`<div class=warnmsg>⚠ precision 평탄 — 임계 튜닝 무효. 씨앗에 color/style_code/barcode(강키) 필요.</div>`;
  h+=`<div class=hist><b>보정 이력</b>`+runs.map(r=>`<div>${(r.ts||'').slice(0,16).replace('T',' ')} · ${r.verdict} · 추천 ${r.recommended==null?'-':r.recommended} · 라벨 ${r.n_labels}${r.applied?' · ✅적용':''}</div>`).join('')+`</div>`;
 }else h+=`<div class=empty>보정 이력 없음 — review/recommend 실행</div>`;
 document.getElementById('detail').innerHTML=h;}
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
    n_eff = sum(1 for g in guidelines if g.get("status") == "effective")
    n_nsk = sum(1 for g in guidelines if g.get("status") == "needs_strong_key")
    n_lab = sum(g.get("n_labels") or 0 for g in guidelines)
    cards = [("카테고리", len(guidelines), False), ("튜닝가능", n_eff, False),
             ("강키필요", n_nsk, False), ("총 라벨", f"{n_lab:,}", True)]
    kpis_html = "".join(
        f'<div class="kpi{" feat" if feat else ""}"><div class=lbl>{lbl}</div><div class=val>{val}</div></div>'
        for lbl, val, feat in cards)
    data = {"guidelines": guidelines, "runs": runs}
    sub = f"가이드라인 {len(guidelines)} · 라벨 {n_lab:,} · 강키필요 {n_nsk}"
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
