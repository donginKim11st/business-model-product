#!/usr/bin/env python3
"""소비자용 정직 가이드 — 광고·협찬·수수료 없이, 실제 후기와 가격 데이터로만.

신뢰기반(11번가 내부). 셀러 화면이 '약점·갭'이라면 이건 소비자 화면:
  · 💰 최저가 — '주요 몰(쿠팡·11번가·G마켓·옥션·동원몰·롯데ON·브랜드 공식스토어 등) 중 최저'를
                헤드라인으로. 정체불명 군소셀러 초저가에 끌리지 않게(가격 spread 중앙값 185% 함정 회피).
                절대 최저가는 모달 가격사다리에 셀러유형 라벨과 함께 투명 공개.
  · 👍 왜 사야 — 후기에서 '자주 나온 좋은 점'(추천 아님, 사실+건수). customer_view 재사용.
  · 🤔 솔직한 단점 — 약점은 있는 상품에만 정직히 노출(verdict.weaknesses 등). 없으면 가짜 긍정으로 안 채움.
  · 📎 근거 — 좋은 점/단점마다 원문 후기·영상 링크. 📣 실제 언급(블로그 글수·유튜브 영상수).

단일 HTML(외부 의존 0). 따뜻한 라이트 소비자 테마. noindex.
  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/consumer_guide.py --html data/consumer_guide.html
"""
import os
import sys
import json
import html
import argparse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import load_mongo
import representative_view as rv
from pymongo import MongoClient

WEAK = ("verdict.weaknesses", "context.why.negative_concern")

# 신뢰 가능한 '주요 몰' — 큰 플랫폼 + 브랜드 공식스토어. 헤드라인 최저가는 이 안에서.
# (curated allowlist. 가이드에 방법론 고지. 군소셀러 절대최저가는 모달에 라벨링해 공개.)
MAJOR_KEYS = ("쿠팡", "11번가", "G마켓", "지마켓", "옥션", "롯데ON", "롯데on", "롯데홈쇼핑",
              "롯데마트", "롯데백화점", "롯데슈퍼", "롯데프레시", "SSG", "이마트", "신세계",
              "위메프", "티몬", "스마트스토어", "홈플러스", "마켓컬리", "컬리",
              "CJ더마켓", "CJ온스타일", "CJmall", "NS홈쇼핑", "현대Hmall", "Hmall", "현대백화점",
              "GS SHOP", "GSSHOP", "GS프레시", "동원몰", "오뚜기mall", "하림퍼스트키친", "공식")


def is_major(mall):
    m = mall or ""
    return any(k in m for k in MAJOR_KEYS)


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _evs(points_evs):
    """customer_view item.evs 또는 evidence list → [{src,url,quote}] (광고 아닌 것 우선)."""
    out = []
    for e in sorted(points_evs or [], key=lambda x: 1 if x.get("is_ad") else 0):
        u = e.get("url")
        if u:
            out.append({"src": e.get("source") or e.get("src"), "url": u,
                        "quote": (e.get("quote") or "")[:90]})
        if len(out) >= 3:
            break
    return out


def price_for(catalogs, offers_by_ctlg):
    """카탈로그별 몰 최저가 사다리 + 헤드라인(주요 몰 중 최저).
    returns {head:{price,mall,major,n_malls}, ladder:[{title,lo,lo_mall,major,n_malls,n_major,malls:[...]}]}"""
    ladder = []
    head = None
    for c in catalogs or []:
        cno = str(c.get("ctlg_no") or "")
        offs = offers_by_ctlg.get(cno) or []
        mall_min = {}
        title = None
        for o in offs:
            if o.get("used") is True or o.get("product_type") in (4, 5, 6):
                continue
            p, m = o.get("price"), o.get("mall")
            if not p or not m:
                continue
            if m not in mall_min or p < mall_min[m]:
                mall_min[m] = p
            if title is None and o.get("title"):
                title = o["title"]
        if not mall_min:
            continue
        major = {m: p for m, p in mall_min.items() if is_major(m)}
        pool = major if major else mall_min
        lo_mall = min(pool, key=lambda m: pool[m])
        lo = pool[lo_mall]
        malls = sorted(({"mall": m, "price": p, "major": is_major(m)} for m, p in mall_min.items()),
                       key=lambda x: x["price"])[:10]
        entry = {"title": (title or "")[:60], "lo": lo, "lo_mall": lo_mall, "major": bool(major),
                 "n_malls": len(mall_min), "n_major": len(major), "malls": malls}
        ladder.append(entry)
        # 헤드라인: 주요 몰 보유 카탈로그 우선, 그 중 최저가
        better = (head is None
                  or (entry["major"] and not head["major"])
                  or (entry["major"] == head["major"] and lo < head["price"]))
        if better:
            head = {"price": lo, "mall": lo_mall, "major": bool(major), "n_malls": len(mall_min)}
    ladder.sort(key=lambda e: e["lo"])
    return {"head": head, "ladder": ladder}


def gather(db):
    # offers 인덱싱 (ctlg → offers)
    offers_by_ctlg = defaultdict(list)
    for o in db.offers.find({}, {"mall": 1, "price": 1, "ctlg_no": 1, "used": 1,
                                 "product_type": 1, "title": 1}):
        offers_by_ctlg[str(o.get("ctlg_no") or "")].append(o)

    prods = []
    kpi_yt = kpi_listing = kpi_cons = kpi_review = 0
    for p in db.products.find({"type": "package", "representative.dims.0": {"$exists": True}},
                              {"keyword": 1, "category_l1": 1, "category": 1, "representative": 1,
                               "taxonomy": 1, "buzz": 1, "youtube": 1, "catalogs.ctlg_no": 1}):
        cv = rv.customer_view(p.get("representative"))
        # 좋은 점(긍정 섹션, caveat 제외) — 섹션 보존(모달용) + 플랫 top(카드용)
        pros_sec = []
        flat = []
        for s in cv["sections"]:
            if s["key"] == "caveat":
                continue
            items = [{"text": it["text"], "n": it["mentions"], "evs": it.get("evs") or [], "yt": False}
                     for it in s["items"] if (it.get("text") or "").strip()]
            if items:
                pros_sec.append({"emoji": s["emoji"], "title": s["title"], "items": items})
                flat.extend(items)
        # 유튜브 후기(있으면) — taxonomy → 좋은 점에 🎬 태그로
        yt = p.get("youtube") or {}
        yt_done = yt.get("status") == "done"
        yt_n = yt.get("n_videos") or 0
        yt_sec = []
        if yt.get("taxonomy"):
            ytcv = rv.customer_view({"dims": rv.taxonomy_to_dims(yt["taxonomy"])})
            yi = []
            for s in ytcv["sections"]:
                if s["key"] == "caveat":
                    continue
                for it in s["items"]:
                    if (it.get("text") or "").strip():
                        yi.append({"text": it["text"], "n": it["mentions"],
                                   "evs": it.get("evs") or [], "yt": True})
            if yi:
                yt_sec = sorted(yi, key=lambda x: -x["n"])[:4]

        # 솔직한 단점 — 명시 추출(customer_view caveat 컷 우회)
        cons = []
        for dp, pts in load_mongo.walk_points(p.get("taxonomy") or {}):
            if dp.startswith(WEAK):
                for pt in pts:
                    t = (pt.get("point") or "").strip()
                    if t:
                        cons.append({"text": t, "n": pt.get("cited_examples") or 0,
                                     "evs": _evs(pt.get("evidence"))})
        cons.sort(key=lambda x: -x["n"])
        # 모순 제거: 같은 후기 문장이 taxonomy 이중분류로 단점이자 '좋은 점'으로 동시 노출되는 것 차단
        #  (예 '고기 잡내','느끼함'이 '왜 사야'에 뜨는 것). 단점이면 좋은 점에서 뺀다.
        con_norms = {(c["text"] or "").lower().replace(" ", "") for c in cons}
        def _ok(it):
            return (it["text"] or "").lower().replace(" ", "") not in con_norms
        pros_sec = [{**s, "items": [it for it in s["items"] if _ok(it)]} for s in pros_sec]
        pros_sec = [s for s in pros_sec if s["items"]]
        flat = [it for it in flat if _ok(it)]
        yt_sec = [it for it in yt_sec if _ok(it)]
        if cons:
            kpi_cons += 1

        pr = price_for(p.get("catalogs"), offers_by_ctlg)
        buzz = (p.get("buzz") or {}).get("naver_blog") or 0
        kpi_review += cv["review_count"] or 0
        if yt_done:
            kpi_yt += yt_n
        for e in pr["ladder"]:
            kpi_listing += e["n_malls"]

        # 카드용 top3 좋은 점(유튜브 우선 섞되 본후기 위주)
        card_pros = sorted(flat, key=lambda x: -x["n"])[:3]
        prods.append({
            "uid": p.get("_id"),
            "kw": p.get("keyword"), "cat": p.get("category_l1") or "(미분류)",
            "headline": cv["headline"], "low_conf": cv.get("low_confidence", False),
            "pros": card_pros, "pros_sec": pros_sec, "yt_sec": yt_sec,
            "cons": cons, "n_cons": len(cons),
            "price": pr, "buzz": buzz, "yt_done": yt_done, "yt_n": yt_n,
            "review_count": cv["review_count"],
        })

    # 정렬: 가격(주요 몰 보유) + 후기 많은 순
    prods.sort(key=lambda x: (0 if (x["price"]["head"] and x["price"]["head"]["major"]) else 1,
                              -(x["buzz"] or 0)))
    cats = sorted({p["cat"] for p in prods})
    return {"prod": prods, "cats": cats,
            "kpi": {"n_prod": len(prods), "n_cons_prod": kpi_cons,
                    "n_review": kpi_review, "n_yt": kpi_yt, "n_listing": kpi_listing,
                    "n_mall": len({m["mall"] for p in prods for e in p["price"]["ladder"] for m in e["malls"]})}}


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#fbf9f5;--card:#fff;--ink:#2b2620;--ink2:#7a7066;--line:#ece6dc;--brand:#c2410c;--brand2:#0e7490;
  --good:#15803d;--goodbg:#f0fdf4;--goodln:#bbf7d0;--warn:#b45309;--warnbg:#fffbeb;--warnln:#fde68a;--price:#0e7490}
body{font-family:'Pretendard',-apple-system,'Apple SD Gothic Neo','Inter',sans-serif;background:var(--bg);color:var(--ink);line-height:1.55}
a{color:inherit}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px 70px}
.hero{text-align:center;padding:40px 0 22px}
.hero h1{font-size:30px;font-weight:900;letter-spacing:-.5px}.hero .sub{color:var(--ink2);font-size:15px;margin-top:8px}
.badges{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:16px}
.badge{background:#fff;border:1px solid var(--line);border-radius:999px;padding:6px 14px;font-size:12.5px;font-weight:700;color:var(--ink)}
.badge.hl{background:#fff7ed;border-color:#fed7aa;color:var(--brand)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:22px 0 14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px 17px;text-align:center}
.kpi .v{font-size:25px;font-weight:900}.kpi .l{font-size:12px;color:var(--ink2);margin-top:2px}
.disc{background:#fff;border:1px solid var(--line);border-radius:13px;padding:13px 16px;font-size:12.5px;color:var(--ink2);margin-bottom:18px}
.disc b{color:var(--ink)}
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
#q{flex:1;min-width:210px;background:#fff;border:1px solid var(--line);border-radius:12px;color:var(--ink);font-size:15px;padding:11px 14px}
.chips{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:16px}
.chip{background:#fff;border:1px solid var(--line);color:var(--ink2);font-size:12.5px;font-weight:700;border-radius:999px;padding:6px 13px;cursor:pointer}
.chip.active{background:var(--brand);color:#fff;border-color:var(--brand)}
.chip.tg{margin-left:auto}.chip.tg.active{background:var(--warn);border-color:var(--warn)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:15px}
.card{background:var(--card);border:1px solid var(--line);border-radius:17px;padding:17px 18px;cursor:pointer;transition:.12s;display:flex;flex-direction:column}
.card:hover{box-shadow:0 7px 22px #2b262012;transform:translateY(-2px)}
.ccat{display:inline-block;background:#f6efe6;color:var(--brand);font-size:11px;font-weight:800;border-radius:6px;padding:2px 8px}
.ckw{font-size:16.5px;font-weight:900;margin:7px 0 3px}
.chead{font-size:13px;color:var(--ink2);margin-bottom:11px;min-height:18px}
.price{background:linear-gradient(135deg,#ecfeff,#f0fdfa);border:1px solid #cff2f3;border-radius:12px;padding:9px 12px;margin-bottom:12px}
.price .p{font-size:20px;font-weight:900;color:var(--price)}.price .m{font-size:12px;color:var(--ink2);font-weight:700}
.price .mall{font-weight:800;color:var(--ink)}
.minor{font-size:10.5px;background:#fff7ed;color:var(--warn);border-radius:5px;padding:1px 6px;font-weight:800;margin-left:4px}
.blk{margin-bottom:9px}.blk h4{font-size:12px;font-weight:800;margin-bottom:4px}
.blk.good h4{color:var(--good)}.blk.bad h4{color:var(--warn)}
.blk ul{list-style:none}.blk li{font-size:13px;color:#3d352b;padding:2px 0 2px 15px;position:relative}
.blk li:before{content:'•';position:absolute;left:3px}.blk.good li:before{color:var(--good)}.blk.bad li:before{color:var(--warn)}
.cn{font-size:10.5px;color:var(--ink2);font-weight:700}
.evlink{font-size:10.5px;color:var(--brand2);text-decoration:none;font-weight:700;border:1px solid var(--line);border-radius:5px;padding:0 5px;margin-left:3px}
.yt{color:#b91c1c}
.mention{margin-top:auto;padding-top:10px;border-top:1px dashed var(--line);font-size:12px;color:var(--ink2);display:flex;gap:12px;flex-wrap:wrap}
.mention b{color:var(--ink)}
.muted{color:var(--ink2);font-size:12px}
.nocons{font-size:12px;color:var(--ink2);background:#fafaf8;border:1px solid var(--line);border-radius:8px;padding:6px 10px}
/* 모달 */
.ovl{position:fixed;inset:0;background:#2b262099;display:none;align-items:flex-start;justify-content:center;padding:36px 16px;overflow:auto;z-index:50}
.ovl.on{display:flex}
.modal{background:var(--bg);border-radius:20px;max-width:760px;width:100%;padding:0;box-shadow:0 20px 60px #00000040}
.mhead{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);border-radius:20px 20px 0 0;padding:18px 22px;display:flex;align-items:flex-start;gap:10px;z-index:2}
.mhead .x{margin-left:auto;font-size:22px;color:var(--ink2);cursor:pointer;line-height:1;font-weight:700}
.mbody{padding:18px 22px 26px}
.msec{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-bottom:13px}
.msec h3{font-size:14px;font-weight:900;margin-bottom:9px}
.lad{margin-bottom:11px}.lad .lt{font-size:12.5px;font-weight:800;margin-bottom:5px}
.malltab{width:100%;border-collapse:collapse;font-size:12.5px}
.malltab td{padding:4px 7px;border-bottom:1px solid #f0ece4}.malltab td.r{text-align:right;font-weight:800;color:var(--price)}
.malltab tr.best td{background:#ecfeff}.malltab td.mn{color:var(--ink2)}
.grp{margin-bottom:11px}.grp .gt{font-size:12.5px;font-weight:800;margin-bottom:4px;color:var(--good)}
.grp.bad .gt{color:var(--warn)}.grp li{font-size:13px;padding:2px 0 2px 16px;position:relative;list-style:none}
.grp li:before{content:'•';position:absolute;left:4px}.grp.bad li:before{color:var(--warn)}.grp li:before{color:var(--good)}
.foot{text-align:center;color:var(--ink2);font-size:11.5px;margin-top:26px;padding-top:16px;border-top:1px solid var(--line)}
"""

JS = r"""
const D=__DATA__;
function won(n){return n==null?'—':'₩'+Number(n).toLocaleString();}
function esc(s){return (s||'').replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}
function evl(evs){return (evs||[]).map(e=>e&&e.url?`<a class=evlink href="${e.url}" target=_blank rel=noopener title="${(e.quote||'').replace(/"/g,'&quot;')}">📎${e.src==='youtube'?'영상':'후기'}</a>`:'').join('');}
function priceLine(pr){const h=pr.head;if(!h)return '<div class="price"><span class=m>가격 비교 준비 중</span></div>';
  return `<div class=price><span class=p>${won(h.price)}</span> <span class=m>최저 · <span class=mall>${esc(h.mall)}</span>${h.major?'':'<span class=minor>소형 판매처</span>'} · 비교 ${h.n_malls}몰</span></div>`;}
function card(p,i){
  const pros=p.pros.length?`<div class="blk good"><h4>👍 후기에서 자주 나온 좋은 점</h4><ul>${p.pros.map(x=>`<li>${esc(x.text)} <span class=cn>${x.n?'·'+x.n+'건':''}</span> ${evl(x.evs)}</li>`).join('')}</ul></div>`:'';
  const cons=p.n_cons?`<div class="blk bad"><h4>🤔 후기에서 나온 아쉬운 점 <span class=cn>(${p.n_cons})</span></h4><ul>${p.cons.slice(0,2).map(x=>`<li>${esc(x.text)} <span class=cn>${x.n?'·'+x.n+'건':''}</span> ${evl(x.evs)}</li>`).join('')}</ul></div>`:'<div class=nocons>🔎 수집된 후기에서 두드러진 단점 언급은 없었어요</div>';
  return `<div class=card data-cat="${esc(p.cat)}" data-kw="${esc((p.kw||'').toLowerCase())}" data-cons="${p.n_cons>0?1:0}" onclick="openP(${i})">
    <div><span class=ccat>${esc(p.cat)}</span></div>
    <div class=ckw>${esc(p.kw)}</div>
    <div class=chead>${esc(p.headline||'')}</div>
    ${priceLine(p.price)}
    ${pros}${cons}
    <div class=mention><span>📣 블로그 <b>${(p.buzz||0).toLocaleString()}</b>건 검색</span>${p.yt_done?`<span>🎬 유튜브 <b>${p.yt_n}</b>영상</span>`:''}<span>자세히 ›</span></div>
  </div>`;}
function ladRow(e){
  const rows=e.malls.map((m,j)=>`<tr class="${j===0?'best':''}"><td>${esc(m.mall)}${m.major?'':' <span class=minor>소형</span>'}</td><td class=r>${won(m.price)}</td></tr>`).join('');
  return `<div class=lad><div class=lt>${e.title?esc(e.title):'구성'} <span class=muted>· 비교 ${e.n_malls}몰(주요 ${e.n_major})</span></div><table class=malltab>${rows}</table></div>`;}
function openP(i){const p=D.prod[i];
  const ladder=p.price.ladder.length?`<div class=msec><h3>💰 가격 비교 — 몰별 최저가</h3>${p.price.ladder.map(ladRow).join('')}<div class=muted style="margin-top:6px">헤드라인 최저가는 신뢰 가능한 <b>주요 몰</b> 중 최저입니다. 군소셀러 절대 최저가는 위 표에 '소형'으로 함께 공개합니다.</div></div>`:'';
  const prosSec=p.pros_sec.map(s=>`<div class=grp><div class=gt>${s.emoji} ${esc(s.title)}</div><ul>${s.items.map(x=>`<li>${esc(x.text)} <span class=cn>${x.n?'·'+x.n+'건':''}</span> ${evl(x.evs)}</li>`).join('')}</ul></div>`).join('');
  const ytSec=p.yt_sec.length?`<div class=grp><div class=gt yt>🎬 영상 후기에서</div><ul>${p.yt_sec.map(x=>`<li>${esc(x.text)} ${evl(x.evs)}</li>`).join('')}</ul></div>`:'';
  const consSec=p.n_cons?`<div class=msec><h3>🤔 솔직한 단점 — 후기에서 나온 아쉬운 점</h3>${p.cons.map(x=>`<div class="grp bad"><ul><li>${esc(x.text)} <span class=cn>${x.n?'·'+x.n+'건':''}</span> ${evl(x.evs)}</li></ul></div>`).join('')}</div>`:`<div class=msec><h3>🤔 솔직한 단점</h3><div class=muted>수집된 후기에서 두드러진 단점 언급은 없었어요. (단점이 '없다'가 아니라, 후기에 자주 등장하지 않았다는 뜻입니다.)</div></div>`;
  document.getElementById('mc').innerHTML=`
    <div class=mhead><div><span class=ccat>${esc(p.cat)}</span><div class=ckw style="margin:6px 0 0">${esc(p.kw)}</div><div class=muted>${esc(p.headline||'')}</div></div><span class=x onclick="closeP()">✕</span></div>
    <div class=mbody>
      <div class=disc style="margin-bottom:13px">이 가이드는 <b>광고·협찬·수수료가 없습니다.</b> 아래 좋은 점/단점은 모두 실제 후기·영상에서 나온 내용이며 📎로 원문을 확인할 수 있어요.</div>
      ${ladder}
      <div class=msec><h3>👍 왜 사야 — 후기에서 자주 나온 좋은 점</h3>${prosSec||'<div class=muted>좋은 점 데이터 없음</div>'}${ytSec}</div>
      ${consSec}
      <div class=msec><h3>📣 근거·출처</h3><div class=muted>네이버 블로그 <b>${(p.buzz||0).toLocaleString()}</b>건 검색(키워드 기준) · ${p.yt_done?`유튜브 <b>${p.yt_n}</b>영상 분석 · `:''}가격 리스팅 <b>${p.price.ladder.reduce((a,e)=>a+e.n_malls,0)}</b>개 · 분석에 인용한 후기 <b>${p.review_count}</b>건. 모든 수치는 직접 수집·실측입니다. <span style="display:block;margin-top:4px">※ '검색 N건'은 네이버 키워드 검색 결과 수이며, 그중 본문을 분석·인용한 건수는 별도 표기했습니다.</span></div></div>
    </div>`;
  document.getElementById('ovl').classList.add('on');document.body.style.overflow='hidden';}
function closeP(){document.getElementById('ovl').classList.remove('on');document.body.style.overflow='';}
window.addEventListener('load',()=>{
  const grid=document.getElementById('grid');grid.innerHTML=D.prod.map(card).join('');
  const cards=[...grid.children];
  const cw=document.getElementById('chips');
  cw.innerHTML=`<button class='chip active' data-c=all>전체 ${D.prod.length}</button>`+D.cats.map(c=>`<button class=chip data-c="${c}">${c} ${D.prod.filter(p=>p.cat===c).length}</button>`).join('')+`<button class='chip tg' id=tgc data-c=cons>🤔 단점 공개 상품만 ${D.kpi.n_cons_prod}</button>`;
  const chips=[...cw.children];let cat='all',q='',consOnly=false;
  function ap(){let n=0;cards.forEach(c=>{const ok=(cat==='all'||c.dataset.cat===cat)&&(q===''||c.dataset.kw.includes(q))&&(!consOnly||c.dataset.cons==='1');c.style.display=ok?'':'none';if(ok)n++;});document.getElementById('cnt').textContent=n;}
  chips.forEach(ch=>ch.onclick=()=>{
    if(ch.dataset.c==='cons'){consOnly=!consOnly;ch.classList.toggle('active',consOnly);ap();return;}
    chips.forEach(x=>{if(x.dataset.c!=='cons')x.classList.remove('active')});ch.classList.add('active');cat=ch.dataset.c;ap();});
  document.getElementById('q').oninput=e=>{q=e.target.value.trim().toLowerCase();ap();};
  document.getElementById('ovl').onclick=e=>{if(e.target.id==='ovl')closeP();};
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeP();});
  ap();
});
"""


def render(d):
    k = d["kpi"]
    kpis = "".join(f"<div class=kpi><div class=v>{v:,}</div><div class=l>{l}</div></div>" for l, v in [
        ("정직 가이드 상품", k["n_prod"]), ("비교한 판매처(몰)", k["n_mall"]),
        ("분석에 인용한 후기 근거", k["n_review"]), ("단점도 공개한 상품", k["n_cons_prod"])])
    js = JS.replace("__DATA__", json.dumps(d, ensure_ascii=False).replace("</", "<\\/"))
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=robots content='noindex,nofollow,noarchive'>"
            f"<title>정직 가이드 — 광고 없이, 실제 후기와 가격으로</title><style>{CSS}</style></head><body><div class=wrap>"
            f"<div class=hero><h1>🕯️ 정직 가이드</h1>"
            f"<div class=sub>광고·협찬·수수료 없이 — <b>실제 후기와 가격 데이터로만</b> 골라드려요</div>"
            f"<div class=badges><span class='badge hl'>✅ 협찬·광고 없음</span><span class=badge>✅ 실측 최저가</span>"
            f"<span class=badge>✅ 단점도 숨기지 않음</span><span class=badge>✅ 근거 📎 링크</span><span class=badge>11번가 내부 신뢰 데이터</span></div></div>"
            f"<div class=kpis>{kpis}</div>"
            f"<div class=disc><b>어떻게 만들었나요?</b> 네이버 블로그·쇼핑·유튜브의 실제 후기를 직접 수집해 자주 나온 좋은 점/아쉬운 점을 정리하고, "
            f"여러 판매처의 실제 판매가를 모아 비교했습니다. <b>최저가</b>는 신뢰 가능한 <b>주요 몰</b>(쿠팡·11번가·G마켓·옥션·동원몰·롯데ON·브랜드 공식스토어 등) 중 최저를 보여주며, "
            f"정체불명 군소셀러의 절대 최저가는 상세 화면 가격표에 '소형'으로 투명하게 함께 공개합니다. 모든 문장과 숫자는 직접 수집·실측이며 꾸밈이 없습니다. "
            f"<span class=muted>※ 향후 제휴·법적 표시가 추가될 수 있으며, 추가 시 명확히 고지합니다.</span></div>"
            f"<div class=controls><input id=q placeholder='상품명 검색…'><div class=muted>표시 <b id=cnt></b>개</div></div>"
            f"<div class=chips id=chips></div>"
            f"<div class=grid id=grid></div>"
            f"<div class=foot>정직 가이드 · 광고·협찬·수수료 없음 · 모든 수치 직접 수집·실측 · 근거 📎 제공<br>"
            f"단점 '없음'은 단점이 없다는 뜻이 아니라 수집 후기에 자주 등장하지 않았다는 의미입니다.</div>"
            f"</div>"
            f"<div class=ovl id=ovl><div class=modal id=mc></div></div>"
            f"<script>{js}</script></body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="data/consumer_guide.html")
    args = ap.parse_args()
    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]
    d = gather(db)
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(render(d))
    k = d["kpi"]
    print(f"소비자 정직 가이드 → {args.html} · 상품 {k['n_prod']} · 단점공개 {k['n_cons_prod']} · "
          f"비교몰 {k['n_mall']} · 후기인용 {k['n_review']:,} · 유튜브 {k['n_yt']}영상")


if __name__ == "__main__":
    main()
