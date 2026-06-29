#!/usr/bin/env python3
"""전 상품 × 모든 속성(attrs_full_*.jsonl) → 단일 독립형 검색 뷰어.
상품 목록(브랜드·코드·이름·가격·속성수) 검색/필터 → 행 클릭하면 그 상품의 전체 속성 펼침.
데이터는 HTML에 JSON으로 임베드(외부 의존성 0). 출력: outputs/attrs_full_viewer.html"""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")


def main():
    items = []
    for jf in sorted(glob.glob(os.path.join(OUT, "attrs_full_*.jsonl"))):
        for line in open(jf, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            # 속성을 평탄 리스트로(섹션 태그 포함) — 뷰어에서 그룹핑
            attrs = []
            for sect, d in r.get("attrs", {}).items():
                if not isinstance(d, dict):
                    continue
                for k, v in d.items():
                    if isinstance(v, list):
                        v = ", ".join(str(x) for x in v)
                    attrs.append([sect, k, str(v)[:300]])
            items.append({"b": r["brand"], "c": r["style_code"], "n": (r.get("name") or "")[:50],
                          "p": r.get("price", ""), "color": (r.get("color") or "")[:20],
                          "u": r.get("url", ""), "a": attrs})
    data = json.dumps(items, ensure_ascii=False)
    nbrands = len({i["b"] for i in items})
    nattrs = sum(len(i["a"]) for i in items)
    html = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>전 상품 속성 탐색기</title>
<style>
:root{--ink:#15181d;--mut:#697586;--line:#e7eaf0;--bg:#f4f6fa;--card:#fff;--brand:#2d6cdf;}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,'Apple SD Gothic Neo','Pretendard',sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}
.wrap{max-width:1100px;margin:0 auto;padding:0 16px 56px}
header{background:rgba(20,22,26,.96);color:#fff;padding:12px 0;position:sticky;top:0;z-index:5}
header .wrap{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.logo{background:#fff;color:#111;font-weight:900;padding:5px 11px;border-radius:8px;font-size:12px}
h1{font-size:15px;margin:0;font-weight:800}
.bar{position:sticky;top:48px;background:var(--bg);padding:12px 0;z-index:4}
.bar .wrap2{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input,select{padding:8px 11px;border:1px solid var(--line);border-radius:9px;font-size:13px;background:#fff}
#q{flex:1;min-width:200px}
.cnt{font-size:12px;color:var(--mut)}.cnt b{color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:12.5px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}
thead th{color:var(--mut);font-size:10px;text-transform:uppercase;background:#eef1f6;border-bottom:2px solid var(--line)}
tr.row{cursor:pointer}tr.row:hover{background:#f6f9ff}
td.num{text-align:right;font-variant-numeric:tabular-nums}
code{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:11px}
.det td{background:#fbfcfe;padding:0}
.detbox{padding:10px 14px}
.sect{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.3px;margin:8px 0 3px}
.sect.jsonld{color:#2d6cdf}.sect.gosi{color:#1a9d57}.sect.meta{color:#b9770b}.sect.options{color:#8b5cf6}
.at{display:grid;grid-template-columns:34% 66%;font-size:12px;border-bottom:1px solid #eef1f6}
.at .k{color:var(--mut);font-family:ui-monospace,monospace;font-size:11px;padding:3px 6px;word-break:break-all}
.at .v{padding:3px 6px;word-break:break-word}
.bchip{font-size:10px;font-weight:700;color:var(--brand);background:#eef3fc;padding:1px 7px;border-radius:999px}
a{color:var(--brand)}
</style></head><body>
<header><div class=wrap><span class=logo>ATTRS</span><h1>전 상품 속성 탐색기</h1></div></header>
<div class=bar><div class="wrap wrap2">
  <input type=search id=q placeholder="브랜드·코드·이름 검색…" oninput=render()>
  <select id=bf onchange=render()></select>
  <span class=cnt id=cnt></span>
</div></div>
<div class=wrap>
  <div class=note style="font-size:12.5px;color:#697586;margin:8px 0">행을 클릭하면 그 상품의 <b style="color:#15181d">전체 속성</b>(JSON-LD/고시/메타/옵션)이 펼쳐집니다. 단일 HTML·외부 의존성 0.</div>
  <table id=tbl><thead><tr><th>브랜드</th><th>스타일코드</th><th>이름</th><th>컬러</th><th class=num>가격</th><th class=num>속성</th></tr></thead><tbody id=tb></tbody></table>
</div>
<script>
const DATA=__DATA__;
const SECTS={jsonld:'JSON-LD',gosi:'고시/스펙',meta:'메타',options:'옵션'};
const bf=document.getElementById('bf');
[...new Set(DATA.map(d=>d.b))].sort().forEach(b=>{const o=document.createElement('option');o.value=b;o.textContent=b;bf.appendChild(o);});
bf.insertAdjacentHTML('afterbegin','<option value="">전체 브랜드</option>');
let open=new Set();
function esc(s){return (s+'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function detail(d){
  let by={}; d.a.forEach(([s,k,v])=>{(by[s]=by[s]||[]).push([k,v]);});
  let h='<td colspan=6 class=det><div class=detbox>';
  for(const s of ['jsonld','gosi','meta','options']){ if(!by[s])continue;
    h+='<div class="sect '+s+'">'+SECTS[s]+' ('+by[s].length+')</div>';
    by[s].forEach(([k,v])=>h+='<div class=at><div class=k>'+esc(k)+'</div><div class=v>'+esc(v)+'</div></div>');
  }
  h+='<div style=margin-top:8px><a href="'+esc(d.u)+'" target=_blank>공식 PDP↗</a></div></div></td>';
  return h;
}
function render(){
  const q=document.getElementById('q').value.toLowerCase().trim();
  const b=bf.value;
  const tb=document.getElementById('tb'); tb.innerHTML='';
  let n=0;
  for(let i=0;i<DATA.length;i++){const d=DATA[i];
    if(b&&d.b!==b)continue;
    if(q&&!((d.b+' '+d.c+' '+d.n).toLowerCase().includes(q)))continue;
    n++; if(n>600){continue;}
    const pr=(''+d.p).replace(/(\\d)(?=(\\d{3})+$)/g,'$1,');
    const tr=document.createElement('tr'); tr.className='row';
    tr.innerHTML='<td><span class=bchip>'+esc(d.b)+'</span></td><td><code>'+esc(d.c)+'</code></td><td>'+esc(d.n)+'</td><td>'+esc(d.color)+'</td><td class=num>'+esc(pr)+'</td><td class=num>'+d.a.length+'</td>';
    tr.onclick=()=>{const nx=tr.nextSibling; if(nx&&nx.className==='det-row'){nx.remove();return;} const dr=document.createElement('tr');dr.className='det-row';dr.innerHTML=detail(d);tr.after(dr);};
    tb.appendChild(tr);
  }
  document.getElementById('cnt').innerHTML='<b>'+n.toLocaleString()+'</b> 상품'+(n>600?' (상위 600 표시)':'');
}
render();
</script></body></html>"""
    html = html.replace("__DATA__", data)
    outp = os.path.join(OUT, "attrs_full_viewer.html")
    open(outp, "w", encoding="utf-8").write(html)
    mb = os.path.getsize(outp) / 1e6
    print(f"{len(items):,}개 상품 · 속성 {nattrs:,} · {nbrands}브랜드 → {outp} ({mb:.1f}MB)")


if __name__ == "__main__":
    main()
