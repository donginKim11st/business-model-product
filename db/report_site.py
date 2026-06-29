#!/usr/bin/env python3
"""단일 자가완결 HTML 리포트 사이트 — 캔버스 차트 + 카드 브라우저(외부 의존 0).

데이터를 그래프(순수 Canvas, 라이브러리·CDN 없음)로 그리고, 기존 결합 카드 브라우저(bundle_view)를
한 페이지에 탭으로 묶는다. 결과는 단일 .html — GitHub Pages 등에 그냥 올리면 뜬다(서버 불필요).

차트: ①카테고리 분포(막대) ②추출 현황(도넛) ③언급량 Top(가로막대) ④카테고리별 대표 속성 coverage(가로막대)
브라우저: 검색·카테고리 칩·카드 펼침(인사이트+카탈로그 가격사다리) — bundle_view 그대로 재사용.

  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/report_site.py --html data/report.html
"""
import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bundle_view as bv
from pymongo import MongoClient


def select_rows(db, per_cat, max_cards, skus):
    """bundle_view.main 과 동일 선정 — 카테고리별 대표/카탈로그 보유 패키지(가격 보유 우선)."""
    bases = []
    for cat in sorted(c for c in db.products.distinct("representative.category") if c):
        q = {"representative.category": cat, "type": "package", "representative.dims.0": {"$exists": True}}
        cur = list(db.products.find(q, {"_id": 1, "n_catalogs": 1, "catalogs.price_summary.min": 1}).limit(400))
        cur.sort(key=lambda d: (-int(any((c.get("price_summary") or {}).get("min")
                                          for c in d.get("catalogs") or [])), -(d.get("n_catalogs") or 0)))
        bases += [db.products.find_one({"_id": d["_id"]}) for d in cur[:per_cat]]
    bases = bases[:max_cards]
    rows = []
    for base in bases:
        if not base:
            continue
        rows.append({"uid": base["_id"], "keyword": base.get("keyword"),
                     "category": base.get("category_l1") or base.get("category"),
                     "rep": base.get("representative") or {},
                     "panel": bv.identity_panel(db, base, max_skus=skus), "buzz": base.get("buzz")})
    return rows


def gather_charts(db):
    # 카테고리 분포 + 추출 현황
    cat_dist = {}
    cat_total = cat_ins = priced = 0
    for p in db.products.find({"type": "package"},
                              {"category_l1": 1, "category": 1, "catalogs.has_insight": 1,
                               "catalogs.price_summary.min": 1, "representative.dims": 1}):
        cat = p.get("category_l1") or p.get("category") or "(미분류)"
        d = cat_dist.setdefault(cat, {"n_pkg": 0, "priced": 0})
        d["n_pkg"] += 1
        cats = p.get("catalogs") or []
        cat_total += len(cats); cat_ins += sum(1 for c in cats if c.get("has_insight"))
        if any((c.get("price_summary") or {}).get("min") for c in cats):
            d["priced"] += 1; priced += 1
    pkg = db.products.count_documents({"type": "package"})
    pkg_ins = db.products.count_documents({"type": "package", "representative.dims.0": {"$exists": True}})
    # 언급량 Top (blog 기준)
    buzz_top = [{"kw": (p.get("buzz") or {}).get("keyword") or p.get("keyword"),
                 "blog": p["buzz"]["naver_blog"], "shop": p["buzz"].get("naver_shop") or 0}
                for p in db.products.find({"type": "package", "buzz.naver_blog": {"$exists": True}},
                                          {"keyword": 1, "buzz": 1}).sort("buzz.naver_blog", -1).limit(15)]
    # 카테고리별 대표 속성 coverage (category_attribute_rank) + 히트맵용
    from collections import Counter
    cat_rank = {}
    cat_cov = {}
    dim_freq = Counter()
    for r in db.category_attribute_rank.find({}, {"ranked_dims": 1, "top_dims": 1}):
        rd = r.get("ranked_dims") or []
        dims = [{"label": d["label"], "coverage": round(d["coverage"] * 100)} for d in rd[:8]]
        if dims:
            cat_rank[r["_id"]] = dims
        cat_cov[r["_id"]] = {d["label"]: round(d["coverage"] * 100) for d in rd}
        for d in rd[:8]:
            dim_freq[d["label"]] += 1
    heat_dims = [d for d, _ in dim_freq.most_common(10)]
    heat_cats = sorted(cat_cov, key=lambda c: -cat_dist.get(c, {}).get("n_pkg", 0))
    heatmap = {"cats": heat_cats, "dims": heat_dims,
               "matrix": [[cat_cov[c].get(d, 0) for d in heat_dims] for c in heat_cats]}

    # 가격대 분포(히스토그램) — 카탈로그 median 가격
    edges = [0, 2000, 5000, 10000, 20000, 40000, 10 ** 12]
    hlabels = ["~2천", "2~5천", "5천~1만", "1~2만", "2~4만", "4만+"]
    hist = [0] * 6
    scatter = []
    for p in db.products.find({"type": "package"},
                              {"keyword": 1, "buzz.naver_blog": 1, "catalogs.price_summary.median": 1}):
        meds = [(c.get("price_summary") or {}).get("median") for c in p.get("catalogs") or []]
        meds = [m for m in meds if m]
        for m in meds:
            for k in range(6):
                if m < edges[k + 1]:
                    hist[k] += 1
                    break
        blog = (p.get("buzz") or {}).get("naver_blog")
        if meds and blog:
            scatter.append({"x": blog, "y": sum(meds) // len(meds), "kw": p.get("keyword")})
    scatter.sort(key=lambda s: -s["x"])
    scatter = scatter[:150]
    # SKU(카탈로그) 인사이트 = insight.dims(비어있지 않은 실측) 보유 카탈로그 / 전체 ctlg 카탈로그.
    # (패키지 단위가 아니라 진짜 SKU 단위로 세야 라벨과 일치 — 감사 반영.)
    ci_agg = list(db.products.aggregate([
        {"$match": {"type": "package"}},
        {"$project": {
            "ci": {"$size": {"$filter": {"input": {"$ifNull": ["$catalogs", []]}, "as": "c",
                   "cond": {"$gt": [{"$size": {"$ifNull": ["$$c.insight.dims", []]}}, 0]}}}},
            "pc": {"$size": {"$filter": {"input": {"$ifNull": ["$catalogs", []]}, "as": "c",
                   "cond": {"$ne": [{"$ifNull": ["$$c.price_summary.min", None]}, None]}}}},
            "nc": {"$size": {"$filter": {"input": {"$ifNull": ["$catalogs", []]}, "as": "c",
                   "cond": {"$ne": ["$$c.ctlg_no", None]}}}}}},
        {"$group": {"_id": None, "ci": {"$sum": "$ci"}, "pc": {"$sum": "$pc"}, "nc": {"$sum": "$nc"}}}]))
    cat_ins_sku = ci_agg[0]["ci"] if ci_agg else 0
    cat_priced_sku = ci_agg[0]["pc"] if ci_agg else 0
    cat_ctlg_total = ci_agg[0]["nc"] if ci_agg else cat_total
    n_buzz = db.products.count_documents({"type": "package", "buzz.naver_blog": {"$exists": True}})
    cat_real = db.products.count_documents({"type": "package", "category_source": "naver_shop"})
    cat_demo = db.products.count_documents({"type": "package", "category_source": {"$ne": "naver_shop"}})
    yt_done = db.products.count_documents({"type": "package", "youtube.status": "done"})
    return {
        "summary": {"packages": pkg, "catalogs": cat_total, "offers": db.offers.count_documents({}),
                    "categories": len(cat_dist), "priced": priced, "pkg_ins": pkg_ins, "buzz": n_buzz,
                    "cat_real": cat_real, "cat_demo": cat_demo, "yt_done": yt_done},
        "cat_dist": sorted([{"name": k, "n_pkg": v["n_pkg"], "priced": v["priced"]}
                            for k, v in cat_dist.items()], key=lambda x: -x["n_pkg"]),
        "extraction": [
            {"label": "패키지 비정형", "done": pkg_ins, "total": pkg},
            {"label": "카탈로그 가격", "done": cat_priced_sku, "total": cat_ctlg_total},
            {"label": "SKU 인사이트", "done": cat_ins_sku, "total": cat_ctlg_total},
            {"label": "언급량(네이버)", "done": n_buzz, "total": pkg},
            {"label": "유튜브 수집", "done": db.products.count_documents({"type": "package", "youtube.status": "done"}), "total": pkg},
        ],
        "buzz_top": buzz_top, "cat_rank": cat_rank,
        "heatmap": heatmap, "price_hist": {"labels": hlabels, "values": hist}, "scatter": scatter,
    }


CHART_CSS = """
*{box-sizing:border-box}
.tabs{display:flex;gap:8px;margin:0 0 18px}
.tab{background:#171a21;border:1px solid #2b3240;color:#cbd2db;font-size:14px;font-weight:600;
     border-radius:9px;padding:9px 18px;cursor:pointer}.tab.active{background:#1f6feb22;border-color:#3b82f6;color:#dbeafe}
.view{display:none}.view.active{display:block}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:#12161d;border:1px solid #232b38;border-radius:12px;padding:14px 16px}
.kpi .v{font-size:24px;font-weight:800;color:#7ee0a0}.kpi .l{font-size:12px;color:#9aa3af;margin-top:2px}
.panel{background:#12161d;border:1px solid #232b38;border-radius:14px;padding:16px 18px;margin-bottom:18px}
.panel h3{margin:0 0 4px;font-size:15px;color:#e6e8eb}.panel .d{color:#9aa3af;font-size:12px;margin-bottom:12px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:780px){.grid2{grid-template-columns:1fr}}
.donuts{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}
canvas{width:100%;display:block}
select{background:#171a21;border:1px solid #2b3240;color:#e6e8eb;border-radius:8px;padding:6px 10px;font-size:13px;margin-bottom:10px}
"""

CHART_JS = r"""
const D = __DATA__;
function _cv(id,h){const c=document.getElementById(id);if(!c)return null;
  const r=c.getBoundingClientRect(),W=r.width||c.parentElement.clientWidth-36,dpr=devicePixelRatio||1;
  c.style.height=h+'px';c.width=W*dpr;c.height=h*dpr;const x=c.getContext('2d');x.scale(dpr,dpr);
  return {x,W,H:h};}
function bar(id,labels,vals,color){const o=_cv(id,300);if(!o)return;const{x,W,H}=o;
  const pl=34,pb=58,pt=14,max=Math.max(...vals,1),n=vals.length,step=(W-pl-6)/n,bw=Math.min(step*0.66,46);
  x.clearRect(0,0,W,H);x.strokeStyle='#2a3240';x.beginPath();x.moveTo(pl,H-pb);x.lineTo(W,H-pb);x.stroke();
  vals.forEach((v,i)=>{const h=(H-pb-pt)*v/max,px=pl+i*step+(step-bw)/2,py=H-pb-h;
    const g=x.createLinearGradient(0,py,0,H-pb);g.addColorStop(0,color);g.addColorStop(1,color+'55');
    x.fillStyle=g;x.fillRect(px,py,bw,Math.max(h,1));
    x.fillStyle='#cbd2db';x.font='10px sans-serif';x.textAlign='center';x.fillText(v.toLocaleString(),px+bw/2,py-4);
    x.save();x.translate(px+bw/2,H-pb+10);x.rotate(-0.5);x.fillStyle='#9aa3af';x.textAlign='right';x.fillText(labels[i],0,0);x.restore();});}
function hbar(id,items,color,h){const o=_cv(id,h||(items.length*26+14));if(!o)return;const{x,W,H}=o;
  const pl=Math.min(170,W*0.42),pr=66,n=items.length,rh=(H-10)/n,max=Math.max(...items.map(i=>i.value),1);
  x.clearRect(0,0,W,H);x.font='11px sans-serif';
  items.forEach((it,i)=>{const y=6+i*rh,bw=(W-pl-pr)*it.value/max;
    const g=x.createLinearGradient(pl,0,pl+bw,0);g.addColorStop(0,color);g.addColorStop(1,color+'77');
    x.fillStyle=g;x.fillRect(pl,y+rh*0.16,Math.max(bw,2),rh*0.66);
    x.fillStyle='#cbd2db';x.textAlign='right';x.fillText(it.label.slice(0,16),pl-6,y+rh*0.62);
    x.fillStyle='#86efac';x.textAlign='left';x.fillText(it.disp||it.value.toLocaleString(),pl+Math.max(bw,2)+5,y+rh*0.62);});}
function donut(id,pct,label,sub){const o=_cv(id,130);if(!o)return;const{x,W,H}=o;
  const cx=W/2,cy=H/2-6,r=Math.min(W,H-20)/2-6;x.clearRect(0,0,W,H);x.lineWidth=11;x.lineCap='round';
  x.strokeStyle='#222a36';x.beginPath();x.arc(cx,cy,r,0,7);x.stroke();
  x.strokeStyle=pct>=66?'#34d399':(pct>=25?'#facc88':'#f87171');
  x.beginPath();x.arc(cx,cy,r,-Math.PI/2,-Math.PI/2+Math.max(pct,0.1)/100*2*Math.PI);x.stroke();
  x.fillStyle='#e6e8eb';x.textAlign='center';x.font='bold 17px sans-serif';x.fillText(pct.toFixed(0)+'%',cx,cy+2);
  x.fillStyle='#9aa3af';x.font='10px sans-serif';x.fillText(label,cx,cy+r+14);if(sub){x.fillStyle='#6b7686';x.fillText(sub,cx,cy+16);}}
function _num(n){return n>=10000?(n/10000).toFixed(1)+'만':n.toLocaleString();}
function heatmap(id,data){const rows=data.cats,cols=data.dims,m=data.matrix;
  const o=_cv(id,rows.length*26+64);if(!o)return;const{x,W,H}=o;
  const pl=Math.min(150,W*0.3),pt=58,cw=(W-pl-6)/cols.length,rh=(H-pt-6)/rows.length;
  x.clearRect(0,0,W,H);x.font='10px sans-serif';
  cols.forEach((c,j)=>{x.save();x.translate(pl+j*cw+cw/2,pt-8);x.rotate(-0.6);x.fillStyle='#9aa3af';x.textAlign='left';x.fillText(c.slice(0,8),0,0);x.restore();});
  rows.forEach((r,i)=>{x.fillStyle='#cbd2db';x.textAlign='right';x.font='10px sans-serif';x.fillText(r.slice(0,11),pl-5,pt+i*rh+rh*0.62);
    cols.forEach((c,j)=>{const v=m[i][j],px=pl+j*cw,py=pt+i*rh,a=v/100;
      x.fillStyle='rgba(52,211,153,'+(0.08+a*0.85).toFixed(2)+')';x.fillRect(px+1,py+1,cw-2,rh-2);
      if(v>0){x.fillStyle=a>0.5?'#08160f':'#cbd2db';x.textAlign='center';x.fillText(v,px+cw/2,py+rh*0.62);}});});}
function scatter(id,pts){const o=_cv(id,300);if(!o)return;const{x,W,H}=o;const pl=46,pb=34,pt=12,pr=12;
  const xs=pts.map(p=>Math.log10(Math.max(p.x,1))),ys=pts.map(p=>p.y);
  const xmin=Math.min(...xs,0),xmax=Math.max(...xs,1),ymax=Math.max(...ys,1);
  x.clearRect(0,0,W,H);x.strokeStyle='#2a3240';x.beginPath();x.moveTo(pl,H-pb);x.lineTo(W-pr,H-pb);x.moveTo(pl,pt);x.lineTo(pl,H-pb);x.stroke();
  x.fillStyle='#9aa3af';x.font='10px sans-serif';x.textAlign='center';x.fillText('언급량(블로그, log) →',W/2,H-4);
  x.save();x.translate(13,H/2);x.rotate(-Math.PI/2);x.fillText('평균가(원)',0,0);x.restore();
  pts.forEach((p,i)=>{const px=pl+(xs[i]-xmin)/((xmax-xmin)||1)*(W-pl-pr),py=H-pb-(ys[i]/ymax)*(H-pb-pt);
    x.fillStyle='rgba(167,139,250,0.55)';x.beginPath();x.arc(px,py,3.5,0,7);x.fill();});}
function drawAll(){
  bar('catBar',D.cat_dist.map(c=>c.name),D.cat_dist.map(c=>c.n_pkg),'#3b82f6');
  D.extraction.forEach((e,i)=>donut('don'+i,e.total?e.done/e.total*100:0,e.label,e.done.toLocaleString()+'/'+e.total.toLocaleString()));
  hbar('buzzBar',D.buzz_top.map(b=>({label:b.kw,value:b.blog,disp:_num(b.blog)})),'#a78bfa',D.buzz_top.length*26+14);
  heatmap('heat',D.heatmap);
  bar('priceHist',D.price_hist.labels,D.price_hist.values,'#f59e0b');
  scatter('scat',D.scatter);
  drawCat();
}
function drawCat(){const sel=document.getElementById('catSel');const c=sel?sel.value:Object.keys(D.cat_rank)[0];
  const dims=(D.cat_rank[c]||[]);
  hbar('catDims',dims.map(d=>({label:d.label,value:d.coverage,disp:d.coverage+'%'})),'#34d399',dims.length*28+14);}
let drawn=false;
function show(v){document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
  document.getElementById('view-'+v).classList.add('active');document.getElementById('tab-'+v).classList.add('active');
  if(v==='charts'){drawAll();drawn=true;}}
window.addEventListener('resize',()=>{if(document.getElementById('view-charts').classList.contains('active'))drawAll();});
window.addEventListener('load',()=>show('charts'));
"""


def render(rows, data, status):
    full = bv.render_html(rows, status=status)        # 기존 카드 브라우저(스타일+바디+필터JS)
    style = full.split("<style>", 1)[1].split("</style>", 1)[0]
    body = full.split("<body>", 1)[1].split("</body>", 1)[0]
    s = data["summary"]
    kpis = "".join(f"<div class=kpi><div class=v>{v:,}</div><div class=l>{l}</div></div>" for l, v in [
        ("패키지", s["packages"]), ("카탈로그", s["catalogs"]), ("판매처 offers", s["offers"]),
        ("카테고리", s["categories"]), ("가격 보유", s["priced"]), ("비정형 추출", s["pkg_ins"])])
    donuts = "".join(f"<div><canvas id=don{i}></canvas></div>" for i in range(len(data["extraction"])))
    cat_opts = "".join(f"<option>{bv._esc(c)}</option>" for c in data["cat_rank"])
    charts = f"""
    <div class=kpis>{kpis}</div>
    <div class=panel><h3>📊 추출 현황 (전부 실측 직접수집)</h3>
      <div class=d>어디까지 직접 뽑혔는지 — 도넛=완료율</div><div class=donuts>{donuts}</div></div>
    <div class=grid2>
      <div class=panel><h3>📦 카테고리별 패키지 수</h3><div class=d>데모 카테고리 기준</div><canvas id=catBar></canvas></div>
      <div class=panel><h3>📣 언급량 Top 15 (네이버 블로그)</h3><div class=d>풀네임 실측</div><canvas id=buzzBar></canvas></div>
    </div>
    <div class=panel><h3>🏷️ 카테고리별 대표 속성 coverage</h3>
      <div class=d>그 카테고리 번들 중 몇 %가 그 속성을 언급하나</div>
      <select id=catSel onchange=drawCat()>{cat_opts}</select><canvas id=catDims></canvas></div>
    <div class=panel><h3>🌡️ 카테고리 × 속성 히트맵</h3>
      <div class=d>행=카테고리, 열=속성, 칸=coverage%(진할수록 높음) — 어떤 카테고리가 어떤 속성에 강한지 한눈에</div>
      <canvas id=heat></canvas></div>
    <div class=grid2>
      <div class=panel><h3>💰 가격대 분포</h3><div class=d>카탈로그 median 가격(개수매칭·이상치 정정 후)</div><canvas id=priceHist></canvas></div>
      <div class=panel><h3>🔀 언급량 ↔ 가격 산점도</h3><div class=d>인기(블로그 언급) vs 평균가 — 패키지별</div><canvas id=scat></canvas></div>
    </div>
    """
    js = CHART_JS.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=robots content='noindex,nofollow,noarchive'>"
            f"<title>상품 인사이트 리포트</title><style>{style}{CHART_CSS}</style></head><body>"
            f"<h1>상품 인사이트 리포트 — 비정형 + 가격 + 언급량</h1>"
            f"<div class=sub>전부 직접 수집한 실측 데이터 · 단일 HTML(외부 의존 없음)</div>"
            f"<div class=tabs><button class=tab id=tab-charts onclick=show('charts')>📊 차트</button>"
            f"<button class=tab id=tab-browser onclick=show('browser')>🗂️ 번들 브라우저</button></div>"
            f"<div class=view id=view-charts>{charts}</div>"
            f"<div class=view id=view-browser>{body}</div>"
            f"<script>{js}</script></body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="data/report.html")
    ap.add_argument("--per-cat", type=int, default=30)
    ap.add_argument("--max-cards", type=int, default=250)
    ap.add_argument("--skus", type=int, default=10)
    args = ap.parse_args()

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]
    data = gather_charts(db)
    rows = select_rows(db, args.per_cat, args.max_cards, args.skus)
    status = {"pkg": data["summary"]["packages"], "pkg_ins": data["summary"]["pkg_ins"],
              "priced": data["summary"]["priced"],
              "cat_total": data["summary"]["catalogs"],
              "cat_ins": next((e["done"] for e in data["extraction"] if e["label"] == "SKU 인사이트"), 0),
              "yt_ins": next((e["done"] for e in data["extraction"] if e["label"] == "유튜브 수집"), 0),
              "buzz": data["summary"]["buzz"],
              "buzz_yt": db.products.count_documents({"type": "package", "buzz.youtube_status": "done"})}
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(render(rows, data, status))
    print(f"리포트 생성 → {args.html} · 패키지 {len(rows)} · 카테고리 {data['summary']['categories']} "
          f"· 차트 4종 + 카드 브라우저 (단일 HTML)")


if __name__ == "__main__":
    main()
