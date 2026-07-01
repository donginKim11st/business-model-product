#!/usr/bin/env python3
"""통합 리포트(대시보드) → 단일 self-contained HTML. DB·서버·fetch 없이 파일만으로 동작.

카탈로그(SKU) 단위로 **정형 + 비정형 추출 내용 전체**를 펼쳐 본다:
  정형  : 카테고리 경로 · 용량(size)/개수(count) · 가격(min~median~max·몰수·최저몰·편차)
  비정형: 모든 dims(강점/약점/맛/식감/스펙/크기/관리 등)의 points · FAQ(Q&A) · 출처수 · 유튜브
상단에 KPI 요약. 검색·카테고리 필터·페이지네이션·행 펼치기. 데이터는 HTML 에 인라인.

※ evidence(리뷰 원문 인용·URL)는 전량 inline 시 ~276MB 라 '출처 N건'으로만 표기(클로드 디자인).

  MONGO_URI=.. INSIGHTS_DB=insights_demo python3 db/export_dashboard.py [--out-dir exports] [--all]
끝에 결과 JSON 한 줄 출력.
"""
import os
import sys
import json
import argparse
import datetime
from pymongo import MongoClient


def build_rows(db, extracted_only=True):
    cur = db.products.find(
        {"catalogs": {"$exists": True}},
        {"category_l1": 1, "category_path": 1, "youtube": 1, "catalogs": 1},
    )
    import load_mongo
    yt_done = lambda d: (d.get("youtube") or {}).get("status") == "done"
    rows = []
    for d in cur:
        yt = "O" if yt_done(d) else ""
        # 유튜브 근거(영상→LLM): 제품 레벨 youtube.taxonomy 를 비정형과 같은 모양으로 평탄화해 함께 표기.
        ytd = d.get("youtube") or {}
        yt_dims, yt_faqs, yt_src = [], [], 0
        if yt and ytd.get("taxonomy"):
            for dim_path, items in load_mongo.walk_points(ytd["taxonomy"]):
                yt_dims.append([load_mongo.dim_label(dim_path),
                                [[pt.get("point", ""),
                                  [[e.get("quote") or e.get("text") or "", e.get("url"),
                                    e.get("source") or "유튜브", e.get("date") or ""]
                                   for e in (pt.get("evidence") or [])]]
                                 for pt in items]])
            yt_faqs = [[f.get("question", ""), f.get("short_answer", "")] for f in (ytd.get("faqs") or [])]
            yt_src = ytd.get("n_sources") or 0
        for c in d.get("catalogs", []):
            if not c.get("ctlg_no"):
                continue
            ins = c.get("insight") or {}
            dims = ins.get("dims") or []
            ps = c.get("price_summary") or {}
            has_price = ps.get("min") is not None
            has_ins = bool(dims)
            if extracted_only and not (has_price or has_ins or yt_dims):  # 미추출 제외(유튜브 근거 있으면 포함)
                continue
            price = None
            if has_price:
                price = [ps.get("min"), ps.get("median"), ps.get("max"),
                         ps.get("n_malls"), ps.get("low_mall"), ps.get("spread_pct")]
            rows.append({
                "c": d.get("category_l1") or "미분류",
                "p": d.get("category_path") or "",
                "n": c.get("disp") or "",
                "k": c.get("ctlg_no"),
                "z": c.get("size"), "u": c.get("count"),
                "pr": price,
                "hf": 1 if has_price else 0,   # 정형(가격) 보유
                "hi": 1 if has_ins else 0,     # 비정형(인사이트) 보유
                "d": [[dim.get("label") or dim.get("dim"),
                       [[pt.get("point", ""),
                         [[e.get("quote") or "", e.get("url"), e.get("author") or "", e.get("date") or ""]
                          for e in (pt.get("evidence") or [])]]
                        for pt in (dim.get("points") or [])]]
                      for dim in dims],
                "f": [[f.get("question", ""), f.get("short_answer", "")]
                      for f in (ins.get("faqs") or [])],
                "s": ins.get("n_sources") or 0,
                "y": yt,
                "yd": yt_dims, "yf": yt_faqs, "ys": yt_src,   # 유튜브 근거(영상→LLM)
            })
    return rows


def kpi(db, rows):
    pkgs = db.products.count_documents({"type": "package"})
    return {"catalogs": len(rows),
            "formal": sum(r["hf"] for r in rows),
            "insight": sum(r["hi"] for r in rows),
            "both": sum(1 for r in rows if r["hf"] and r["hi"]),
            "youtube": sum(1 for r in rows if r["y"]),
            "categories": len({r["c"] for r in rows}),
            "packages": pkgs}


def write_html(rows, kpis, path, generated):
    rows.sort(key=lambda r: (r["c"], r["n"]))
    cats = sorted({r["c"] for r in rows})
    cat_opts = "".join(f'<option>{c}</option>' for c in cats)
    data_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    doc = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>추출 통합 리포트</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');
:root{--bg:#F0EEE6;--card:#fff;--ink:#2A2723;--muted:#8A857C;--accent:#CC785C;--line:#E7E2D7;--good:#7A9B76;--bad:#C1664F}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 "Noto Sans KR","Apple SD Gothic Neo",-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
header{padding:28px 28px 0;max-width:1180px;margin:0 auto}
header h1{font-family:"Noto Sans KR",sans-serif;font-weight:700;font-size:27px;margin:0 0 4px;letter-spacing:-.5px}
header .meta{color:var(--muted);font-size:13px}
.kpis{max-width:1180px;margin:18px auto 0;padding:0 28px;display:flex;gap:12px;flex-wrap:wrap}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:12px 18px;min-width:130px}
.kpi b{font-size:22px;font-weight:700;letter-spacing:-.5px}.kpi span{display:block;color:var(--muted);font-size:12px;margin-top:2px}
.controls{max-width:1180px;margin:18px auto 0;padding:0 28px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;position:sticky;top:0;background:var(--bg);padding-top:12px;padding-bottom:12px;z-index:5}
.controls input,.controls select{padding:9px 12px;border:1px solid var(--line);border-radius:9px;font-size:13px;background:#fff}
.chip{cursor:pointer;user-select:none;display:inline-flex}
.chip input{position:absolute;opacity:0;width:0;height:0}
.chip span{display:inline-flex;align-items:center;gap:7px;padding:8px 15px;border:1px solid var(--line);border-radius:22px;font-size:13px;background:#fff;color:var(--muted);transition:all .15s}
.chip span::before{content:"";width:9px;height:9px;border-radius:50%;background:#C1664F;transition:.15s}
.chip:hover span{border-color:#D9B7AB}
.chip input:checked+span{background:#FCE4E0;border-color:var(--accent);color:#B5402C;font-weight:600}
.chip input:checked+span::before{box-shadow:0 0 0 3px rgba(204,120,92,.2)}
.controls input{flex:1;min-width:240px}#cnt{color:var(--muted);font-size:12.5px;margin-left:auto}
.list{max-width:1180px;margin:0 auto;padding:0 28px 60px}
.row{background:var(--card);border:1px solid var(--line);border-radius:13px;margin-bottom:8px;overflow:hidden}
.rhead{display:grid;grid-template-columns:120px 1fr auto auto;gap:14px;align-items:center;padding:13px 18px;cursor:pointer}
.rhead:hover{background:#FBFAF7}
.rcat{font-size:12px;font-weight:600;color:var(--accent);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rname{font-weight:600;font-size:14px}
.rtags{display:flex;gap:6px;align-items:center}
.tag{font-size:11.5px;padding:2px 8px;border-radius:20px;background:#F0EDE5;color:#6B6760;white-space:nowrap}
.tag.s{background:#E8F0E6;color:#4B6B45}.tag.w{background:#F6E7E2;color:#9C4A37}.tag.p{background:#FBF0D9;color:#8A6A1E}.tag.y{background:#FCE4E0;color:#B5402C}
.caret{color:var(--muted);transition:transform .15s}.row.open .caret{transform:rotate(90deg)}
.detail{display:none;padding:4px 18px 18px;border-top:1px solid var(--line)}
.row.open .detail{display:block}
.sec{margin-top:14px}.sec h4{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin:0 0 8px;font-weight:700}
.formal{display:flex;gap:22px;flex-wrap:wrap;font-size:13px}
.formal div b{color:var(--muted);font-weight:600;margin-right:5px}
.dim{margin-bottom:12px}.dim .dl{font-weight:700;font-size:13px;margin-bottom:4px}
.dim.strengths .dl{color:var(--good)}.dim.weak .dl{color:var(--bad)}
.pt{margin:4px 0 8px;font-size:13.5px}
.ev{font-size:12px;color:#7c766c;padding:3px 0 3px 12px;margin:3px 0 3px 10px;border-left:2px solid #E7E0D4}
.ev .src{color:#aba59a}
.evlink{color:var(--accent);text-decoration:none;font-size:11px;font-weight:600}
.evlink:hover{text-decoration:underline}
.faq{margin-bottom:8px}.faq .q{font-weight:600}.faq .a{color:#555;padding-left:2px}
.pager{max-width:1180px;margin:0 auto;padding:0 28px 50px;display:flex;gap:8px;justify-content:center;align-items:center}
.pager button{padding:7px 14px;border:1px solid var(--line);background:#fff;border-radius:9px;cursor:pointer}
.pager button:disabled{opacity:.4;cursor:default}
</style></head><body>
<header><h1>추출 통합 리포트</h1>
<div class="meta">생성 __GEN__ · 카탈로그별 정형·비정형 추출 내용 전체 · DB 없이 이 파일만으로 동작</div></header>
<div class="kpis">__KPIS__</div>
<div class="controls">
  <input id="q" placeholder="상품명·강점·약점·FAQ 검색…">
  <select id="cat"><option value="">전체 카테고리</option>__CATS__</select>
  <select id="mode">
    <option value="">전체 추출</option>
    <option value="b">둘 다 있음</option>
    <option value="f">정형(가격) 있음</option>
    <option value="i">비정형(인사이트) 있음</option>
  </select>
  <label class="chip"><input type="checkbox" id="yo"><span>유튜브 있는 것만</span></label>
  <span id="cnt"></span>
</div>
<div class="list" id="list"></div>
<div class="pager"><button id="prev">‹ 이전</button><span id="pg"></span><button id="next">다음 ›</button></div>
<script>
const DATA=__DATA__, PAGE=40;
let view=DATA, page=0;
function esc(s){return (s==null?'':''+s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function flat(r){let t=r.n+' ';r.d.forEach(d=>d[1].forEach(p=>t+=p[0]+' '));r.f.forEach(f=>t+=f[0]+f[1]);return t.toLowerCase()}
function apply(){
  const q=document.getElementById('q').value.trim().toLowerCase();
  const cat=document.getElementById('cat').value, mode=document.getElementById('mode').value, yo=document.getElementById('yo').checked;
  view=DATA.filter(r=>{
    if(cat&&r.c!==cat)return false;
    if(mode==='f'&&!r.hf)return false; if(mode==='i'&&!r.hi)return false; if(mode==='b'&&!(r.hf&&r.hi))return false;
    if(yo&&r.y!=='O')return false;
    if(q&&!flat(r).includes(q))return false; return true;});
  page=0;render();
}
function strengths(r){const d=r.d.find(x=>/강점|strength/i.test(x[0]));return d?d[1].length:0}
function weak(r){const d=r.d.find(x=>/약점|weak/i.test(x[0]));return d?d[1].length:0}
function render(){
  const start=page*PAGE, slice=view.slice(start,start+PAGE);
  document.getElementById('list').innerHTML=slice.map((r,i)=>{
    const gi=start+i;
    const pr=r.hf?`<span class="tag p">₩${(+r.pr[1]).toLocaleString()} · ${r.pr[3]}몰</span>`:'';
    const yt=r.y==='O'?'<span class="tag y">YT</span>':'';
    const ins=r.hi?`<span class="tag s">강점 ${strengths(r)}</span><span class="tag w">약점 ${weak(r)}</span><span class="tag">출처 ${r.s}</span>`
                  :'<span class="tag" style="background:#EFEAE0;color:#aaa">비정형 없음</span>';
    return `<div class="row" data-i="${gi}">
      <div class="rhead" onclick="toggle(this)">
        <div class="rcat">${esc(r.c)}</div>
        <div class="rname">${esc(r.n)} <span style="color:#b3ab9e;font-size:11px;font-weight:400">#${esc(r.k)}</span></div>
        <div class="rtags">${ins}${pr}${yt}</div>
        <div class="caret">›</div>
      </div>
      <div class="detail">${detail(r)}</div></div>`;
  }).join('');
  const pages=Math.max(1,Math.ceil(view.length/PAGE));
  document.getElementById('pg').textContent=`${page+1} / ${pages}`;
  document.getElementById('cnt').textContent=`${view.length.toLocaleString()}건`;
  document.getElementById('prev').disabled=page<=0;
  document.getElementById('next').disabled=page>=pages-1;
}
function detail(r){
  let h='<div class="sec"><h4>정형 (구조화 추출)</h4><div class="formal">';
  h+=`<div><b>카탈로그번호</b>${esc(r.k)}</div>`;
  h+=`<div><b>카테고리</b>${esc(r.p||r.c)}</div>`;
  if(r.z)h+=`<div><b>용량</b>${esc(r.z)}</div>`;
  if(r.u)h+=`<div><b>개수</b>${esc(r.u)}</div>`;
  if(r.pr)h+=`<div><b>가격</b>₩${(+r.pr[0]).toLocaleString()} ~ ${(+r.pr[2]).toLocaleString()} (중앙 ₩${(+r.pr[1]).toLocaleString()})</div>`+
            `<div><b>몰</b>${r.pr[3]}곳 · 최저 ${esc(r.pr[4])} · 편차 ${r.pr[5]}%</div>`;
  else h+='<div style="color:#aaa">가격 미수집</div>';
  h+='</div></div>';
  h+='<div class="sec"><h4>비정형 (리뷰→LLM 인사이트)'+(r.hi?' · 출처 '+r.s+'건':'')+'</h4>';
  if(r.hi){r.d.forEach(d=>{const cls=/강점|strength/i.test(d[0])?'strengths':/약점|weak/i.test(d[0])?'weak':'';
    h+=`<div class="dim ${cls}"><div class="dl">${esc(d[0])}</div>`;
    d[1].forEach(p=>{
      h+=`<div class="pt">• ${esc(p[0])}`;
      (p[1]||[]).forEach(e=>{const meta=[e[2],e[3]].filter(Boolean).map(esc).join(' · ');
        const lk=e[1]?` <a class="evlink" href="${esc(e[1])}" target="_blank" rel="noopener">원문↗</a>`:'';
        h+=`<div class="ev">“${esc(e[0])}”${meta?` <span class="src">— ${meta}</span>`:''}${lk}</div>`;});
      h+='</div>';});
    h+='</div>';});}
  else h+='<div style="color:#aaa">비정형 인사이트 미추출</div>';
  h+='</div>';
  if(r.yd&&r.yd.length){h+='<div class="sec"><h4>유튜브 (영상→LLM 인사이트)'+(r.ys?' · 출처 '+r.ys+'건':'')+'</h4>';
    r.yd.forEach(d=>{const cls=/강점|strength/i.test(d[0])?'strengths':/약점|weak/i.test(d[0])?'weak':'';
    h+=`<div class="dim ${cls}"><div class="dl">${esc(d[0])}</div>`;
    d[1].forEach(p=>{h+=`<div class="pt">• ${esc(p[0])}`;
      (p[1]||[]).forEach(e=>{const meta=[e[2],e[3]].filter(Boolean).map(esc).join(' · ');
        const lk=e[1]?` <a class="evlink" href="${esc(e[1])}" target="_blank" rel="noopener">영상↗</a>`:'';
        h+=`<div class="ev">“${esc(e[0])}”${meta?` <span class="src">— ${meta}</span>`:''}${lk}</div>`;});
      h+='</div>';});
    h+='</div>';});
    if(r.yf&&r.yf.length){r.yf.forEach(f=>h+=`<div class="faq"><div class="q">Q. ${esc(f[0])}</div><div class="a">A. ${esc(f[1])}</div></div>`);}
    h+='</div>';}
  if(r.f.length){h+='<div class="sec"><h4>FAQ</h4>';
    r.f.forEach(f=>h+=`<div class="faq"><div class="q">Q. ${esc(f[0])}</div><div class="a">A. ${esc(f[1])}</div></div>`);h+='</div>';}
  return h;
}
function toggle(el){el.parentElement.classList.toggle('open')}
['q','cat','mode','yo'].forEach(id=>document.getElementById(id).addEventListener('input',apply));
document.getElementById('prev').onclick=()=>{if(page>0){page--;render();scrollTo(0,0)}};
document.getElementById('next').onclick=()=>{page++;render();scrollTo(0,0)};
apply();
</script></body></html>"""
    kpi_html = "".join(
        f'<div class="kpi"><b>{v}</b><span>{lbl}</span></div>' for v, lbl in [
            (f'{kpis["catalogs"]:,}', "추출 카탈로그(SKU)"),
            (f'{kpis["both"]:,}', "둘 다 있음"),
            (f'{kpis["formal"]:,}', "정형(가격) 있음"),
            (f'{kpis["insight"]:,}', "비정형(인사이트) 있음"),
            (f'{kpis["youtube"]:,}', "유튜브 분석"),
            (f'{kpis["categories"]:,}', "카테고리"),
        ])
    doc = (doc.replace("__GEN__", generated).replace("__KPIS__", kpi_html)
              .replace("__CATS__", cat_opts).replace("__DATA__", data_json))
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="exports")
    ap.add_argument("--all", action="store_true", help="미추출 포함 전체")
    args = ap.parse_args()
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights_demo")]
    os.makedirs(args.out_dir, exist_ok=True)
    rows = build_rows(db, extracted_only=not args.all)
    kpis = kpi(db, rows)
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    p = os.path.join(args.out_dir, "unified_report.html")
    size = write_html(rows, kpis, p, generated)
    print(json.dumps({"stage": "dashboard", "generated": generated, "kpi": kpis,
                      "files": [{"path": os.path.abspath(p), "rows": len(rows), "bytes": size}]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
