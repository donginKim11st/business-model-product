#!/usr/bin/env python3
"""통합 대시보드 — 정형 × 여론을 '하나의 디자인'으로 네이티브 렌더.

iframe 으로 기존 HTML 을 끼우지 않는다. 양쪽 원천 데이터를 직접 읽어 동일한 Tandem 디자인으로 다시 그린다.

뷰:
 · 개요       : 양 파이프라인 KPI + 가격 인텔리전스 스트립 + 차트(브랜드별/카테고리별)
 · 여론 인사이트 : 카탈로그별 강점·약점·맛·FAQ + 크로스몰 가격사다리(정량) + 리뷰 가격평(정성) + 가격평↔실가 불일치 플래그
 · 정형 전속성  : 브랜드별 상품 × 모든 속성(JSON-LD·고시·메타·옵션) 검색·펼침
 · 브랜드      : 정형 브랜드 리더보드(상품수·전속성·가격대·고시 커버) → 클릭 시 전속성 드릴다운
 · 카테고리     : 여론(식품) + 정형(신발·의류) 카테고리 분해

데이터가 커서 상세(브랜드별 attrs, insight)는 assets/ 로 분리해 lazy fetch. 브랜드/카테고리/개요는 셸에 임베드.
localhost http 서버로 열 것:  python3 -m http.server 8765  →  http://localhost:8765/dashboard.html
"""
import os, re, csv, json, glob, statistics, collections

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "identity", "outputs", "all_brands.csv")
ATTRS_GLOB = os.path.join(HERE, "identity", "outputs", "attrs_full_*.jsonl")
ASSETS = os.path.join(HERE, "assets")
OUT = os.path.join(HERE, "dashboard.html")

# 가격평↔실가 불일치 휴리스틱 — 적대적 검증(워크플로) 반영: 개당 단가(원/개) 정규화 후
# 카테고리 '단위가' 중앙값 대비 비교. 묶음/대용량팩이 sentiment 로 오인되던 결함 제거.
OVERPRICED_MULT = 2.5   # 긍정평인데 단가 > 카테고리 단위가중앙 × 이 값  → overpraised
CHEAP_MULT = 0.4        # 부정평인데 단가 < 카테고리 단위가중앙 × 이 값  → undercriticized
VALUE_POS = ("가성비", "가격대비", "가격 대비", "합리적", "저렴", "착한 가격", "혜자", "값싸",
             "부담없", "부담 없", "가심비", "가격 만족", "가격이 좋", "경제적")
VALUE_NEG = ("비싸", "비쌈", "값비싸", "가격 부담", "가격이 부담", "사악한 가격",
             "가성비 떨어", "가성비 별로", "가격 아쉬", "가격이 아쉬")
NEG_CANCEL = ("않", "안 ", "지 않", "별로")  # "비싸지 않다"/"저렴하지 않다" 등 양보·부정구문 → 모호로 제외


def _num(v):
    try:
        f = float(v)
        return f if f > 0 else 0
    except (TypeError, ValueError):
        return 0


def parse_count(count_str):
    """'12개' → 12, '3입' → 3. 못 뽑으면 None."""
    if not count_str:
        return None
    m = re.search(r"(\d+)\s*(?:개|입|팩|봉|포|매|병|캔|구)", str(count_str))
    if m:
        n = int(m.group(1))
        return n if 0 < n <= 500 else None
    return None


def classify_point(t):
    """단일 price_range point → 'pos' / 'neg' / None(모호/양보구문)."""
    if any(c in t for c in NEG_CANCEL):   # 부정·양보 구문은 보수적으로 제외(오분류 방지)
        return None
    pos = any(k in t for k in VALUE_POS)
    neg = any(k in t for k in VALUE_NEG)
    if pos and not neg:
        return "pos"
    if neg and not pos:
        return "neg"
    return None


# ── 정형 개요/브랜드/카테고리 (CSV) ─────────────────────────────────────────
def load_identity():
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))  # utf-8-sig: BOM 제거(source 키 보존)
    n = len(rows)
    bc = collections.Counter(r["brand"].strip() for r in rows if r.get("brand"))

    def cov(f):
        return round(sum(1 for r in rows if (r.get(f) or "").strip()) / n * 100) if n else 0

    # 카테고리별(정형)
    catg = collections.defaultdict(list)
    for r in rows:
        c = (r.get("category") or "기타").strip() or "기타"
        catg[c].append(_num(r.get("price")))
    idn_cats = []
    for c, prices in catg.items():
        pp = [p for p in prices if p > 0]
        idn_cats.append({"cat": c, "n": len(prices), "med": int(statistics.median(pp)) if pp else 0})
    idn_cats.sort(key=lambda x: -x["n"])

    return {"rows": rows, "total": n, "brands": len(bc), "brand_top": bc.most_common(12),
            "cov": {f: cov(f) for f in ("origin", "material", "mfg_date")},
            "clusters": _clusters(), "idn_cats": idn_cats[:12]}


def load_brands(rows, attrs_by_slug):
    """브랜드 리더보드: 브랜드별 상품수·가격대·고시 커버·전속성 보유."""
    by = collections.defaultdict(lambda: {"n": 0, "prices": [], "origin": 0, "material": 0,
                                          "mfg": 0, "slugs": collections.Counter()})
    for r in rows:
        b = (r.get("brand") or "").strip()
        if not b:
            continue
        d = by[b]
        d["n"] += 1
        p = _num(r.get("price"))
        if p:
            d["prices"].append(p)
        if (r.get("origin") or "").strip():
            d["origin"] += 1
        if (r.get("material") or "").strip():
            d["material"] += 1
        if (r.get("mfg_date") or "").strip():
            d["mfg"] += 1
        if (r.get("source") or "").strip():
            d["slugs"][r["source"].strip()] += 1
    out = []
    for b, d in by.items():
        pp = sorted(d["prices"])
        slug = d["slugs"].most_common(1)[0][0] if d["slugs"] else ""
        out.append({
            "name": b, "slug": slug, "n": d["n"],
            "attrs": attrs_by_slug.get(slug, 0),
            "pmin": int(pp[0]) if pp else 0, "pmax": int(pp[-1]) if pp else 0,
            "pmed": int(statistics.median(pp)) if pp else 0,
            "origin": round(d["origin"] / d["n"] * 100), "material": round(d["material"] / d["n"] * 100),
            "mfg": round(d["mfg"] / d["n"] * 100),
        })
    out.sort(key=lambda x: -x["n"])
    return out


def _clusters():
    try:
        return len(json.load(open(os.path.join(HERE, "identity", "outputs", "llm_clusters.json"))).get("clusters", []))
    except Exception:
        return 0


# ── 정형 전속성(attrs_full_*.jsonl) → 브랜드별 assets ───────────────────────
def build_attrs():
    os.makedirs(os.path.join(ASSETS, "attrs"), exist_ok=True)
    index, total, by_slug = [], 0, {}
    for jf in sorted(glob.glob(ATTRS_GLOB)):
        slug = os.path.basename(jf)[len("attrs_full_"):-len(".jsonl")]
        items, brand = [], slug
        for line in open(jf, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            brand = r.get("brand") or slug
            attrs = []
            for sect, d in (r.get("attrs") or {}).items():
                if not isinstance(d, dict):
                    continue
                for k, v in d.items():
                    if isinstance(v, list):
                        v = ", ".join(str(x) for x in v)
                    attrs.append([sect, k, str(v)[:300]])
            items.append({"c": r.get("style_code") or "", "n": (r.get("name") or "")[:60],
                          "p": r.get("price") or "", "color": (r.get("color") or "")[:24],
                          "u": r.get("url") or "", "a": attrs})
        if not items:
            continue
        with open(os.path.join(ASSETS, "attrs", f"{slug}.json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
        index.append({"slug": slug, "brand": brand, "n": len(items)})
        by_slug[slug] = len(items)
        total += len(items)
    index.sort(key=lambda x: -x["n"])
    return index, total, by_slug


# ── 여론(MongoDB) ──────────────────────────────────────────────────────────
def load_insight():
    try:
        from pymongo import MongoClient
        uri = os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true")
        dbn = os.environ.get("INSIGHTS_DB", "insights_demo")
        db = MongoClient(uri, serverSelectionTimeoutMS=2500)[dbn]
        db.command("ping")
    except Exception as e:
        print("  (insight: Mongo 연결 안 됨)", e)
        return None
    pkgs = db.products.count_documents({"type": "package"})

    # 오퍼를 카탈로그별로 1패스(가격사다리) + 중고/리셀 위생 카운트
    off_by_ctlg = collections.defaultdict(list)
    n_dirty = 0  # used=True 또는 productType>=4 (중고/리셀/렌탈) — backfill 정정 후 보통 0
    for o in db.offers.find({}, {"ctlg_no": 1, "mall": 1, "platform": 1, "price": 1, "url": 1, "used": 1, "product_type": 1}):
        cno, pr = o.get("ctlg_no"), o.get("price")
        if cno is None or not pr:
            continue
        if o.get("used") or (o.get("product_type") or 0) >= 4:
            n_dirty += 1
        off_by_ctlg[cno].append((o.get("mall") or o.get("platform") or "", int(pr), o.get("url") or ""))
    for v in off_by_ctlg.values():
        v.sort(key=lambda x: x[1])

    spread_list = []        # 몰간 편차 큰 카탈로그(이상 신호)
    low_malls = collections.Counter()  # 최저가 몰 분포

    cats_total = cats_ins = priced = 0
    catstat = collections.defaultdict(lambda: {"n": 0, "ins": 0, "mins": [], "units": [], "spreads": [], "dims": collections.Counter()})
    spreads_all = []
    pr_pos = pr_neg = pr_total = 0
    raw = []  # (item, cl, unit_price, sentiment)

    for p in db.products.find({"type": "package"}, {"catalogs": 1, "category_l1": 1, "category": 1, "keyword": 1}):
        cl = p.get("category_l1") or p.get("category") or "기타"
        for c in p.get("catalogs", []):
            cats_total += 1
            ins = c.get("insight") or {}
            dims = ins.get("dims") or []
            has = bool(dims or ins.get("faqs"))
            ps = c.get("price_summary") or {}
            mn = int(ps.get("min") or 0)
            cnt = parse_count(c.get("count"))
            unit = round(mn / cnt) if (mn and cnt) else None   # 개당 단가(원/개) — 포장단위 정규화
            cs = catstat[cl]
            cs["n"] += 1
            if has:
                cats_ins += 1
                cs["ins"] += 1
            if mn:
                priced += 1
                cs["mins"].append(mn)
                if unit:
                    cs["units"].append(unit)
                sp = int(ps.get("spread_pct") or 0)
                if ps.get("spread_pct") is not None:
                    cs["spreads"].append(sp)
                    spreads_all.append(sp)
                spread_list.append({"name": (c.get("disp") or "")[:60], "cat": cl, "min": mn,
                                    "max": int(ps.get("max") or 0), "sp": sp, "nm": int(ps.get("n_malls") or 0)})
                if ps.get("low_mall"):
                    low_malls[ps["low_mall"]] += 1
            for d in dims:
                cs["dims"][d.get("label") or d.get("dim") or ""] += 1
            if not has:
                continue
            # dims 다듬기 + 가격평 분류(개별 point 단위 + 부정 가드)
            tdims, pr_labels = [], []
            for d in dims:
                pts = []
                for pt in (d.get("points") or [])[:3]:
                    ev = (pt.get("evidence") or [{}])[0]
                    pts.append({"t": (pt.get("point") or "")[:160], "q": (ev.get("quote") or "")[:180],
                                "u": ev.get("url") or "", "s": ev.get("source") or ev.get("kind") or "",
                                "m": ev.get("match") or ""})
                if pts:
                    tdims.append({"l": d.get("label") or d.get("dim") or "", "p": pts})
                if (d.get("label") == "가격대") or (d.get("dim") == "aspect.price_range"):
                    pr_labels += [classify_point(pt.get("point") or "") for pt in (d.get("points") or [])]
            n_p = pr_labels.count("pos")
            n_n = pr_labels.count("neg")
            pr_total += len(pr_labels)
            pr_pos += n_p
            pr_neg += n_n
            sentiment = "pos" if n_p and not n_n else ("neg" if n_n and not n_p else None)
            faqs = [{"q": (f.get("question") or "")[:140], "a": (f.get("short_answer") or "")[:200]}
                    for f in (ins.get("faqs") or [])[:4]]
            cno = c.get("ctlg_no")
            offers = off_by_ctlg.get(cno, [])
            price_block = None
            if mn:
                price_block = {"min": mn, "max": int(ps.get("max") or 0), "med": int(ps.get("median") or 0),
                               "sp": int(ps.get("spread_pct") or 0), "lm": ps.get("low_mall") or "",
                               "nm": int(ps.get("n_malls") or 0), "unit": unit, "cnt": cnt,
                               "off": [[m, pr, u] for (m, pr, u) in offers[:8]]}
            item = {"id": str(cno or ""), "name": (c.get("disp") or p.get("keyword") or "")[:80],
                    "cat": cl, "price": mn, "nm": int(ps.get("n_malls") or 0),
                    "ns": int(ins.get("n_sources") or 0), "dims": tdims, "faqs": faqs, "ps": price_block}
            raw.append((item, cl, unit, sentiment))

    # 카테고리 '단위가(원/개)' 중앙값 → 불일치 플래그 (포장단위 정규화 후)
    cat_unit_med = {cl: int(statistics.median(s["units"])) for cl, s in catstat.items() if len(s["units"]) >= 5}
    n_over = n_under = 0
    items = []
    for item, cl, unit, sent in raw:
        med = cat_unit_med.get(cl, 0)
        if unit and med:
            if sent == "pos" and unit > med * OVERPRICED_MULT:
                item["mm"] = {"k": "over", "med": med, "unit": unit}
                n_over += 1
            elif sent == "neg" and unit < med * CHEAP_MULT:
                item["mm"] = {"k": "under", "med": med, "unit": unit}
                n_under += 1
        items.append(item)

    # 가격 이상 신호 모음
    mismatch_list = []
    for it in items:
        mm = it.get("mm")
        if mm:
            mismatch_list.append({"name": it["name"], "cat": it["cat"], "min": it["price"],
                                  "unit": mm["unit"], "med": mm["med"], "k": mm["k"],
                                  "ratio": round(mm["unit"] / mm["med"], 2)})
    mismatch_list.sort(key=lambda x: -x["ratio"])
    spread_list.sort(key=lambda x: -x["sp"])
    n_extreme = sum(1 for s in spread_list if s["sp"] > 300)
    signals = {"mismatch": mismatch_list, "spread": spread_list[:60],
               "low_malls": low_malls.most_common(12),
               "hygiene": {"dirty": n_dirty, "priced": priced,
                           "n_mismatch": len(mismatch_list), "n_extreme": n_extreme}}

    os.makedirs(ASSETS, exist_ok=True)
    with open(os.path.join(ASSETS, "insight.json"), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, separators=(",", ":"))

    cat_top = sorted(((cl, s["n"]) for cl, s in catstat.items()), key=lambda x: -x[1])[:8]
    ins_cats = []
    for cl, s in sorted(catstat.items(), key=lambda x: -x[1]["n"])[:16]:
        ins_cats.append({"cat": cl, "n": s["n"], "priced": len(s["mins"]),
                         "ins_pct": round(s["ins"] / s["n"] * 100) if s["n"] else 0,
                         "med": int(statistics.median(s["mins"])) if s["mins"] else 0,
                         "unit_med": int(statistics.median(s["units"])) if s["units"] else 0,
                         "spread": round(statistics.median(s["spreads"])) if s["spreads"] else 0,
                         "dims": [d for d, _ in s["dims"].most_common(5) if d]})
    return {
        "pkgs": pkgs, "cats_total": cats_total, "cats_ins": cats_ins,
        "ins_pct": round(cats_ins / cats_total * 100) if cats_total else 0,
        "priced": priced, "offers": sum(len(v) for v in off_by_ctlg.values()),
        "cat_top": [c for c, _ in cat_top], "cat_top_v": [v for _, v in cat_top],
        "ins_cats": ins_cats,
        "med_spread": round(statistics.median(spreads_all)) if spreads_all else 0,
        "val_pos": round(pr_pos / pr_total * 100) if pr_total else 0,
        "val_neg": round(pr_neg / pr_total * 100) if pr_total else 0,
        "n_mismatch": n_over + n_under, "n_over": n_over, "n_under": n_under,
        "signals": signals,
    }


# ── 렌더 ───────────────────────────────────────────────────────────────────
def kpi(big, label, sub, tone="mut"):
    return (f'<div class="kpi"><div class="kl">{label}</div><div class="kb">{big}</div>'
            f'<div class="ks {tone}">{sub}</div></div>')


def render(idn, ins, brand_index, brand_lb, attrs_total):
    if ins:
        cards = (kpi(f'{idn["total"]:,}', "정형 상품", f'{idn["brands"]}개 브랜드 · 공식몰', "mut")
                 + kpi(f'{ins["cats_total"]:,}', "인사이트 카탈로그", f'↑ {ins["ins_pct"]}% 커버', "pos")
                 + kpi(f'{ins["offers"]:,}', "가격 리스팅", f'{ins["pkgs"]:,} 상품 크로스몰', "mut")
                 + kpi(f'{attrs_total:,}', "전속성 추출 상품", f'고시 {idn["cov"]["origin"]}% · 소재 {idn["cov"]["material"]}%', "mut"))
        strip = (f'<div class=strip>'
                 f'<div class=s>가격 편차(중앙)<b>{ins["med_spread"]}%</b></div>'
                 f'<div class=s>가성비 긍정률<b class=pos>{ins["val_pos"]}%</b></div>'
                 f'<div class=s>가격 부정률<b>{ins["val_neg"]}%</b></div>'
                 f'<div class=s>가격평↔단가 불일치<b class=warn>{ins["n_mismatch"]}</b>건</div>'
                 f'<div class=s>가격 보유 카탈로그<b>{ins["priced"]:,}</b></div></div>')
        bar_labels, bar_vals = ins["cat_top"], ins["cat_top_v"]
        bar_title, bar_tag = "여론 — 카테고리별 인사이트 카탈로그", "여론"
    else:
        cards = (kpi(f'{idn["total"]:,}', "정형 상품", f'{idn["brands"]}개 브랜드', "mut")
                 + kpi(f'{attrs_total:,}', "전속성 추출 상품", '모든 속성 보유', "mut")
                 + kpi(f'{idn["clusters"]}', "canonical 클러스터", 'pig 레졸루션', "mut")
                 + kpi(f'{idn["cov"]["origin"]}%', "고시 커버리지", f'소재 {idn["cov"]["material"]}%', "mut"))
        strip = ""
        bar_labels, bar_vals = [c["cat"] for c in idn["idn_cats"][:8]], [c["n"] for c in idn["idn_cats"][:8]]
        bar_title, bar_tag = "정형 — 카테고리별 상품 수", "정형"

    chart = json.dumps({"area": {"labels": [b for b, _ in idn["brand_top"]], "vals": [v for _, v in idn["brand_top"]]},
                        "bar": {"labels": bar_labels, "vals": bar_vals}}, ensure_ascii=False)
    bidx = json.dumps(brand_index, ensure_ascii=False)
    blb = json.dumps(brand_lb, ensure_ascii=False)
    cats = json.dumps({"ins": (ins["ins_cats"] if ins else []), "idn": idn["idn_cats"]}, ensure_ascii=False)
    opts = "".join(f'<option value="{b["slug"]}">{b["brand"]} ({b["n"]:,})</option>' for b in brand_index)

    nav = [("◧", "개요", True, "view:overview"), ("◓", "여론 인사이트", False, "view:insight"),
           ("⊞", "정형 전속성", False, "view:attrs"), ("◆", "브랜드", False, "view:brands"),
           ("▤", "카테고리", False, "view:categories"), ("⚠", "가격 이상 신호", False, "view:signals")]
    navhtml = "".join(
        f'<a class="nav{" on" if on else ""}" data-act="{act}" onclick="nav(this)">'
        f'<span class="ni">{ic}</span>{nm}</a>' for ic, nm, on, act in nav)
    sig = json.dumps(ins["signals"] if ins else {"mismatch": [], "spread": [], "low_malls": [], "hygiene": {}},
                     ensure_ascii=False)

    return TEMPLATE.replace("__CARDS__", cards).replace("__STRIP__", strip).replace("__NAV__", navhtml) \
        .replace("__BARTITLE__", bar_title).replace("__BARTAG__", bar_tag) \
        .replace("__DATA__", chart).replace("__BRANDS__", bidx).replace("__BLB__", blb) \
        .replace("__CATS__", cats).replace("__SIGNALS__", sig).replace("__OPTS__", opts) \
        .replace("__INSBADGE__", "연결됨" if ins else "오프라인").replace("__INSCLS__", "pos" if ins else "none")


TEMPLATE = r"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content=noindex>
<title>ProductGraph — 통합 대시보드</title>
<style>
:root{--bg:#eef1f6;--card:#fff;--ink:#1e2532;--mut:#8a93a6;--line:#eceef3;--brand:#4f6ef7;--pos:#16a34a;--warn:#c2410c;--on:#eef2ff;}
*{box-sizing:border-box}html,body{margin:0}
body{font-family:-apple-system,'Pretendard','Apple SD Gothic Neo',Segoe UI,sans-serif;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;font-size:13px}
.app{display:grid;grid-template-columns:212px 1fr;min-height:100vh}
.side{background:#fff;border-right:1px solid var(--line);padding:22px 14px;display:flex;flex-direction:column}
.logo{font-weight:800;font-size:20px;letter-spacing:-.5px;padding:0 8px 22px}.logo span{color:var(--brand)}
.nav{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:9px;color:#5a6377;text-decoration:none;font-weight:500;margin-bottom:2px;cursor:pointer}
.nav:hover{background:#f5f7fb}.nav.on{background:var(--on);color:var(--brand);font-weight:600}
.ni{display:inline-flex;width:18px;justify-content:center;opacity:.85}
.side .foot{margin-top:auto;border-top:1px solid var(--line);padding-top:10px;font-size:11px;color:var(--mut)}
.main{padding:18px 26px 40px;min-width:0}
.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.crumb{font-size:15px;font-weight:600}.crumb b{color:var(--mut);font-weight:500}
.tools{display:flex;gap:8px;align-items:center}
.ic{width:34px;height:34px;border-radius:9px;background:#fff;border:1px solid var(--line);display:flex;align-items:center;justify-content:center;color:#7a8398}
.av{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#4f6ef7,#9aa7fc)}
.badge{font-size:11px;padding:3px 9px;border-radius:20px;font-weight:600}
.badge.pos{background:#e7f6ee;color:var(--pos)}.badge.none{background:#f1f3f7;color:#97a0b2}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.kl{color:var(--mut);font-size:12px;margin-bottom:9px}.kb{font-size:25px;font-weight:750;letter-spacing:-.5px}
.ks{font-size:11.5px;margin-top:7px;font-weight:600}.ks.pos{color:var(--pos)}.ks.mut{color:var(--mut)}
.strip{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.strip .s{background:#fff;border:1px solid var(--line);border-radius:12px;padding:11px 15px;font-size:12px;color:var(--mut)}
.strip .s b{font-size:17px;color:var(--ink);font-weight:750;margin-left:8px}
.strip .s b.pos{color:var(--pos)}.strip .s b.warn{color:var(--warn)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.ph{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.ph .t{font-size:12px;color:var(--mut)}.ph .v{font-size:21px;font-weight:750;margin-top:3px}
.tag{font-size:11px;font-weight:600;color:var(--brand);background:var(--on);padding:5px 11px;border-radius:8px;border:1px solid #e2e8ff}
canvas{width:100%;display:block}
.tbl-wrap{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:16px}
.tbl-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;gap:12px;flex-wrap:wrap}
.tbl-head h3{margin:0;font-size:15px}
.search{font-size:13px;color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:9px 13px;min-width:240px;flex:1}
select.sel{font-size:13px;border:1px solid var(--line);border-radius:9px;padding:9px 11px;background:#fff}
.cnt{font-size:12px;color:var(--mut);font-weight:600}
table{width:100%;border-collapse:collapse}
th{text-align:left;color:var(--mut);font-weight:600;font-size:11.5px;padding:9px 10px;border-bottom:1px solid var(--line)}
td{padding:10px 10px;border-bottom:1px solid var(--line);font-size:12.5px;vertical-align:middle}
.mono a,.mono{color:var(--brand);text-decoration:none;font-weight:600}
.num{text-align:right;font-weight:650;font-variant-numeric:tabular-nums}
.attrn{display:inline-block;font-size:10.5px;font-weight:700;background:#eef2ff;color:#4f6ef7;padding:2px 8px;border-radius:7px}
.attrn.zero{background:#f1f3f7;color:#aab}
tr.row{cursor:pointer}tr.row:hover{background:#f6f9ff}
.cov3{display:inline-flex;gap:4px}
.cb{font-size:10.5px;font-weight:700;padding:2px 6px;border-radius:6px;min-width:30px;text-align:center}
.cb.hi{background:#e7f7ee;color:#1a9d57}.cb.mid{background:#fff6e6;color:#b9770b}.cb.lo{background:#fdecec;color:#d23b3b}.cb.no{background:#f1f3f7;color:#aab}
.brandlink{color:var(--brand);font-weight:700;cursor:pointer}.brandlink.dead{color:var(--ink);cursor:default}
.detbox{background:#fbfcfe;padding:12px 14px;border-radius:10px;margin:2px 0 8px}
.sect{font-size:10.5px;font-weight:800;letter-spacing:.3px;margin:10px 0 4px}
.sect.jsonld{color:#2d6cdf}.sect.gosi{color:#16a34a}.sect.meta{color:#b9770b}.sect.options{color:#8b5cf6}
.at{display:grid;grid-template-columns:30% 70%;font-size:12px;border-bottom:1px solid #eef1f6}
.at .k{color:var(--mut);font-family:ui-monospace,monospace;font-size:11px;padding:3px 6px;word-break:break-all}
.at .v{padding:3px 6px;word-break:break-word}
.view{display:none}.view.on{display:block}
.ins-list details{background:#fff;border:1px solid var(--line);border-radius:12px;margin-bottom:10px}
.ins-list summary{list-style:none;cursor:pointer;padding:14px 16px;display:flex;align-items:center;gap:12px}
.ins-list summary::-webkit-details-marker{display:none}
.ins-list .nm{font-weight:650;font-size:13.5px}.ins-list .me{color:var(--mut);font-size:11.5px;margin-top:3px}
.ins-list .pr{margin-left:auto;text-align:right;font-weight:700;white-space:nowrap}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;align-items:center}
.chip{font-size:10.5px;font-weight:600;background:#f3f5fa;color:#5a6377;padding:3px 8px;border-radius:7px}
.mm{font-size:10.5px;font-weight:700;color:var(--warn);background:#fff1e8;padding:3px 8px;border-radius:7px}
.ins-body{padding:4px 16px 16px;border-top:1px solid var(--line)}
.dim{margin:12px 0}.dim .dl{font-weight:700;font-size:12px;color:var(--brand);margin-bottom:6px}
.pt{margin:7px 0 7px 2px}.pt .tx{font-size:12.5px}
.pt .ev{font-size:11.5px;color:#6b7486;border-left:2px solid #e3e7ef;padding:3px 0 3px 9px;margin-top:4px}
.pt .ev a{color:var(--brand);text-decoration:none}
.faq .q{font-weight:650;font-size:12.5px}.faq .a{font-size:12px;color:#56607a;margin:2px 0 9px}
.m-verified{color:var(--pos);font-weight:700}.m-partial{color:#c8881a;font-weight:700}
.price{margin:12px 0;background:#f8faff;border:1px solid #e6ebfb;border-radius:10px;padding:11px 13px}
.price .ph2{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;margin-bottom:10px;color:#56607a}
.price .ph2 b{font-weight:750;color:var(--ink)}.price .lo b{color:var(--pos)}
.price .mmline{font-size:11.5px;font-weight:700;color:var(--warn);margin-bottom:9px}
.ladder{display:flex;flex-direction:column;gap:5px}
.lrow{display:grid;grid-template-columns:96px 1fr 84px;align-items:center;gap:9px;font-size:11.5px}
.lrow .ml{color:#56607a;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lrow .ml a{color:inherit;text-decoration:none}.lrow .ml a:hover{color:var(--brand)}
.lbar{height:8px;border-radius:5px;background:#cdd9fb;min-width:4px}
.lrow.best .lbar{background:var(--pos)}
.lrow .lp{text-align:right;font-weight:700;font-variant-numeric:tabular-nums}
.lrow.best .lp{color:var(--pos)}.lrow.best .ml{color:var(--pos);font-weight:700}
.price .pdl{font-weight:700;font-size:12px;color:#b9770b;margin:12px 0 4px;border-top:1px dashed #e6ebfb;padding-top:11px}
@media(max-width:1080px){.kpis{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}}
@media(max-width:720px){.app{grid-template-columns:1fr}.side{display:none}.at{grid-template-columns:1fr}}
</style></head><body>
<div class=app>
 <aside class=side>
   <div class=logo>Product<span>Graph</span></div>
   __NAV__
   <div class=foot>정형 × 여론 통합 뷰<br>네이티브 렌더 · 단일 디자인</div>
 </aside>
 <main class=main>
   <div class=top><div class=crumb id=crumb><b>통합 /</b> 개요</div>
     <div class=tools><span class="badge __INSCLS__">여론 DB __INSBADGE__</span>
       <div class=ic>⌕</div><div class=ic>⋯</div><div class=av></div></div></div>

   <!-- 개요 -->
   <section id=overview class="view on">
     <div class=kpis>__CARDS__</div>
     __STRIP__
     <div class=grid2>
       <div class=panel><div class=ph><div><div class=t>정형 — 브랜드별 상품 수</div><div class=v id=areaPeak>—</div></div>
         <span class=tag>정형 · Top 12</span></div><canvas id=area height=230></canvas></div>
       <div class=panel><div class=ph><div><div class=t>__BARTITLE__</div><div class=v id=barPeak>—</div></div>
         <span class=tag>__BARTAG__</span></div><canvas id=bar height=230></canvas></div>
     </div>
   </section>

   <!-- 여론 인사이트 -->
   <section id=insight class=view><div class=tbl-wrap>
     <div class=tbl-head><h3>여론 인사이트 — 강점·약점·가격(정량+정성)·FAQ·근거</h3>
       <input class=search id=qIns placeholder="상품명·카테고리 검색…" oninput=renderIns()>
       <span class=cnt id=cntIns>로딩…</span></div>
     <div id=insList class=ins-list></div></div></section>

   <!-- 정형 전속성 -->
   <section id=attrs class=view><div class=tbl-wrap>
     <div class=tbl-head><h3>정형 전속성 — 상품 × 모든 속성</h3>
       <select class=sel id=brandSel onchange=loadBrand()>__OPTS__</select>
       <input class=search id=qAttr placeholder="코드·이름·컬러 검색…" oninput=renderAttr()>
       <span class=cnt id=cntAttr>브랜드 선택</span></div>
     <table><thead><tr><th>스타일코드</th><th>이름</th><th>컬러</th><th class=num>가격</th><th class=num>속성</th></tr></thead>
       <tbody id=tbAttr></tbody></table></div></section>

   <!-- 브랜드 리더보드 -->
   <section id=brands class=view><div class=tbl-wrap>
     <div class=tbl-head><h3>브랜드 리더보드 — 정형</h3>
       <input class=search id=qBrand placeholder="브랜드 검색…" oninput=renderBrands()>
       <span class=cnt id=cntBrand></span></div>
     <table><thead><tr><th>브랜드</th><th class=num>상품수</th><th class=num>전속성</th>
       <th>가격대(최저~중앙~최고)</th><th>고시 커버 (제조국·소재·제조년)</th></tr></thead>
       <tbody id=tbBrand></tbody></table>
     <div class=cnt style="margin-top:9px">행 클릭 → 해당 브랜드 전속성 보기</div></div></section>

   <!-- 카테고리 -->
   <section id=categories class=view>
     <div class=tbl-wrap><div class=tbl-head><h3>여론 — 카테고리 (식품)</h3><span class=tag>insight</span></div>
       <table><thead><tr><th>카테고리</th><th class=num>카탈로그</th><th class=num>인사이트</th>
         <th class=num>가격 중앙</th><th class=num>개당 중앙</th><th class=num>편차(중앙)</th><th>대표 인사이트 축</th></tr></thead>
         <tbody id=tbCatIns></tbody></table></div>
     <div class=tbl-wrap><div class=tbl-head><h3>정형 — 카테고리</h3><span class=tag>identity</span></div>
       <table><thead><tr><th>카테고리</th><th class=num>상품수</th><th class=num>가격 중앙</th></tr></thead>
         <tbody id=tbCatIdn></tbody></table></div>
   </section>

   <!-- 가격 이상 신호 -->
   <section id=signals class=view>
     <div class=strip id=sigStrip></div>
     <div class=tbl-wrap><div class=tbl-head><h3>가격평 ↔ 단가 불일치</h3>
       <span class=tag>리뷰 ‘저렴/가성비’지만 개당 단가가 카테고리 중앙의 2.5배↑</span></div>
       <table><thead><tr><th>상품</th><th>카테고리</th><th class=num>개당 단가</th>
         <th class=num>카테 개당중앙</th><th class=num>배율</th></tr></thead><tbody id=tbSigMm></tbody></table></div>
     <div class=tbl-wrap><div class=tbl-head><h3>몰간 가격 편차 큰 카탈로그</h3>
       <span class=tag>편차 &gt;300% = 가격 매칭 의심(이상 오퍼 혼입)</span></div>
       <table><thead><tr><th>상품</th><th>카테고리</th><th class=num>최저~최고</th>
         <th class=num>편차</th><th class=num>몰수</th><th>판정</th></tr></thead><tbody id=tbSigSp></tbody></table></div>
     <div class=tbl-wrap><div class=tbl-head><h3>최저가 몰 분포</h3>
       <span class=tag>어느 몰이 자주 최저가인가</span></div>
       <table><thead><tr><th>몰</th><th>최저가 횟수</th><th class=num>건수</th></tr></thead><tbody id=tbSigMall></tbody></table></div>
   </section>
 </main>
</div>
<script>
var D=__DATA__, BRANDS=__BRANDS__, BLB=__BLB__, CATS=__CATS__, SIGNALS=__SIGNALS__, INS=null, ATTR=null, ATTR_SLUG=null;
var SECTS={jsonld:'JSON-LD',gosi:'고시/스펙',meta:'메타',options:'옵션'};
var CRUMB={overview:'개요',insight:'여론 인사이트',attrs:'정형 전속성',brands:'브랜드',categories:'카테고리',signals:'가격 이상 신호'};
function esc(s){return (s||'').replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function won(n){return n?'₩'+Number(n).toLocaleString():'—'}
function nav(el){document.querySelectorAll('.side .nav').forEach(function(n){n.classList.remove('on')});el.classList.add('on');
 var a=el.getAttribute('data-act');if(a.indexOf('view:')===0)showView(a.slice(5));
 else if(a.indexOf('scroll:')===0){showView('overview');var t=document.querySelector(a.slice(7));if(t)setTimeout(function(){t.scrollIntoView({behavior:'smooth',block:'center'})},60)}}
function showView(id){document.querySelectorAll('.view').forEach(function(v){v.classList.remove('on')});
 document.getElementById(id).classList.add('on');document.getElementById('crumb').innerHTML='<b>통합 /</b> '+CRUMB[id];window.scrollTo({top:0});
 if(id==='insight')ensureIns();if(id==='attrs')loadBrand();if(id==='brands')renderBrands();if(id==='categories')renderCats();if(id==='signals')renderSignals()}
/* 여론 */
function ensureIns(){if(INS)return;fetch('assets/insight.json').then(function(r){return r.json()}).then(function(j){INS=j;renderIns()})}
function mcls(m){return m==='verified'?'m-verified':(m?'m-partial':'')}
function renderIns(){if(!INS)return;var q=document.getElementById('qIns').value.toLowerCase().trim();var out=[],shown=0,matched=0;
 for(var i=0;i<INS.length;i++){var it=INS[i];if(q&&(it.name+' '+it.cat).toLowerCase().indexOf(q)<0)continue;matched++;if(shown>=60)continue;shown++;
  var chips=it.dims.map(function(d){return '<span class=chip>'+esc(d.l)+'</span>'}).join('');
  var mmb=it.mm?'<span class=mm>⚠ 단가 '+(it.mm.k==='over'?'비쌈':'저렴')+' · 리뷰와 불일치</span>':'';
  function ptsHtml(d){return d.p.map(function(p){var ev=p.q?'<div class=ev>“'+esc(p.q)+'” '+(p.u?'<a href="'+p.u+'" target=_blank>'+esc(p.s||'출처')+' ↗</a>':'')+(p.m?' <span class='+mcls(p.m)+'>'+(p.m==='verified'?'검증':'부분')+'</span>':'')+'</div>':'';return '<div class=pt><div class=tx>· '+esc(p.t)+'</div>'+ev+'</div>'}).join('')}
  var pdim=null,others=[];it.dims.forEach(function(d){if(d.l==='가격대'&&!pdim)pdim=d;else others.push(d)});
  var hasLadder=it.ps&&it.ps.off&&it.ps.off.length;var body='';
  if(hasLadder||pdim){body+='<div class=price>';
   if(it.mm){body+='<div class=mmline>⚠ 개당 단가 <b>₩'+it.mm.unit.toLocaleString()+'</b> · 카테고리 개당 중앙 ₩'+it.mm.med.toLocaleString()+' → '+(it.mm.k==='over'?'리뷰는 ‘저렴/가성비’지만 단가는 '+(it.mm.unit/it.mm.med).toFixed(1)+'배 비쌈':'리뷰는 ‘비싸다’지만 단가는 중앙의 '+Math.round(it.mm.unit/it.mm.med*100)+'%로 저렴')+' (개수 정규화)</div>'}
   if(hasLadder){var P=it.ps;var vmin=P.off[0][1],vmax=P.off[P.off.length-1][1];
    function bw(pr){return vmax>vmin?Math.round(34+(pr-vmin)/(vmax-vmin)*66):100}
    var rows=P.off.map(function(o){var best=o[1]===P.min;var ml=o[2]?'<a href="'+o[2]+'" target=_blank>'+esc(o[0])+'</a>':esc(o[0]);return '<div class="lrow'+(best?' best':'')+'"><div class=ml>'+ml+(best?' · 최저':'')+'</div><div class=lbar style="width:'+bw(o[1])+'%"></div><div class=lp>₩'+o[1].toLocaleString()+'</div></div>'}).join('');
    body+='<div class=ph2><span>크로스몰 가격(정량) · <b>'+P.nm+'개 몰</b></span><span class=lo>최저 <b>₩'+P.min.toLocaleString()+'</b> '+esc(P.lm)+'</span><span>중앙 <b>₩'+P.med.toLocaleString()+'</b></span><span>최고 <b>₩'+P.max.toLocaleString()+'</b></span><span>편차 <b>'+P.sp+'%</b></span></div><div class=ladder>'+rows+'</div>'}
   if(pdim){body+='<div class=pdl>리뷰 속 가격·가성비 (정성, 비정형 추출)</div>'+ptsHtml(pdim)}
   body+='</div>'}
  others.forEach(function(d){body+='<div class=dim><div class=dl>'+esc(d.l)+'</div>'+ptsHtml(d)+'</div>'});
  if(it.faqs.length){body+='<div class=dim><div class=dl>FAQ</div>'+it.faqs.map(function(f){return '<div class=faq><div class=q>Q. '+esc(f.q)+'</div><div class=a>'+esc(f.a)+'</div></div>'}).join('')+'</div>'}
  out.push('<details><summary><div><div class=nm>'+esc(it.name)+'</div><div class=me>'+esc(it.cat)+' · 근거 '+it.ns+'건 · '+it.nm+'개 몰</div><div class=chips>'+chips+mmb+'</div></div><div class=pr>'+won(it.price)+'</div></summary><div class=ins-body>'+body+'</div></details>')}
 document.getElementById('insList').innerHTML=out.join('');
 document.getElementById('cntIns').innerHTML='<b>'+matched.toLocaleString()+'</b>건'+(matched>shown?' (상위 '+shown+')':'')}
/* 정형 전속성 */
function loadBrand(){var slug=document.getElementById('brandSel').value;if(slug===ATTR_SLUG&&ATTR){renderAttr();return}
 ATTR_SLUG=slug;ATTR=null;document.getElementById('cntAttr').textContent='로딩…';document.getElementById('tbAttr').innerHTML='';
 fetch('assets/attrs/'+slug+'.json').then(function(r){return r.json()}).then(function(j){ATTR=j;renderAttr()})}
function renderAttr(){if(!ATTR)return;var q=document.getElementById('qAttr').value.toLowerCase().trim();var out=[],shown=0,matched=0;
 for(var i=0;i<ATTR.length;i++){var r=ATTR[i];if(q&&(r.c+' '+r.n+' '+r.color).toLowerCase().indexOf(q)<0)continue;matched++;if(shown>=250)continue;shown++;
  out.push('<tr class=row onclick="tog('+i+')"><td class=mono>'+esc(r.c||'—')+'</td><td>'+esc(r.n)+'</td><td>'+esc(r.color)+'</td><td class=num>'+won(r.p)+'</td><td class=num><span class=attrn>'+r.a.length+'</span></td></tr>');
  out.push('<tr id=det'+i+' style=display:none><td colspan=5 style="padding:0 4px"><div class=detbox id=db'+i+'></div></td></tr>')}
 document.getElementById('tbAttr').innerHTML=out.join('');
 document.getElementById('cntAttr').innerHTML='<b>'+matched.toLocaleString()+'</b>개'+(matched>shown?' (상위 '+shown+')':'')}
function tog(i){var d=document.getElementById('det'+i);if(!d)return;var open=d.style.display!=='none';d.style.display=open?'none':'';
 if(open)return;var r=ATTR[i],box=document.getElementById('db'+i);if(box.dataset.done)return;var by={};r.a.forEach(function(t){(by[t[0]]=by[t[0]]||[]).push(t)});var h='';
 ['jsonld','gosi','meta','options'].forEach(function(s){if(!by[s])return;h+='<div class="sect '+s+'">'+(SECTS[s]||s)+' ('+by[s].length+')</div>';by[s].forEach(function(t){h+='<div class=at><div class=k>'+esc(t[1])+'</div><div class=v>'+esc(t[2])+'</div></div>'})});
 if(r.u)h+='<div style="margin-top:8px"><a href="'+r.u+'" target=_blank style="color:#4f6ef7">원본 PDP ↗</a></div>';box.innerHTML=h;box.dataset.done=1}
/* 브랜드 리더보드 */
function cb(v){var c=v>=80?'hi':(v>=40?'mid':(v>0?'lo':'no'));return '<span class="cb '+c+'">'+v+'%</span>'}
var ATTRSLUGS={};BRANDS.forEach(function(b){ATTRSLUGS[b.slug]=1});
function renderBrands(){var q=document.getElementById('qBrand').value.toLowerCase().trim();var out=[],shown=0;
 for(var i=0;i<BLB.length;i++){var b=BLB[i];if(q&&b.name.toLowerCase().indexOf(q)<0)continue;shown++;
  var has=ATTRSLUGS[b.slug];
  var nm=has?'<span class="brandlink" onclick="goAttr(\''+b.slug+'\')">'+esc(b.name)+' →</span>':'<span class="brandlink dead">'+esc(b.name)+'</span>';
  var an=b.attrs?'<span class=attrn>'+b.attrs.toLocaleString()+'</span>':'<span class="attrn zero">0</span>';
  out.push('<tr><td>'+nm+'</td><td class=num>'+b.n.toLocaleString()+'</td><td class=num>'+an+'</td>'+
   '<td>'+won(b.pmin)+' ~ <b>'+won(b.pmed)+'</b> ~ '+won(b.pmax)+'</td>'+
   '<td><span class=cov3>'+cb(b.origin)+cb(b.material)+cb(b.mfg)+'</span></td></tr>')}
 document.getElementById('tbBrand').innerHTML=out.join('');
 document.getElementById('cntBrand').innerHTML='<b>'+shown+'</b> 브랜드'}
function goAttr(slug){document.querySelectorAll('.side .nav').forEach(function(n){n.classList.remove('on')});
 var n=document.querySelector('.nav[data-act="view:attrs"]');if(n)n.classList.add('on');
 document.getElementById('brandSel').value=slug;showView('attrs')}
/* 가격 이상 신호 */
function renderSignals(){var S=SIGNALS,H=S.hygiene||{};
 document.getElementById('sigStrip').innerHTML=
  '<div class=s>가격평↔단가 불일치<b class=warn>'+(H.n_mismatch||0)+'</b>건</div>'+
  '<div class=s>편차 극단(&gt;300%)<b class=warn>'+(H.n_extreme||0)+'</b>건</div>'+
  '<div class=s>중고·리셀 혼입<b class=pos>'+(H.dirty||0)+'</b>건 '+((H.dirty||0)===0?'(정정완료)':'')+'</div>'+
  '<div class=s>가격 보유 카탈로그<b>'+(H.priced||0).toLocaleString()+'</b></div>';
 document.getElementById('tbSigMm').innerHTML=(S.mismatch||[]).map(function(m){
  return '<tr><td>'+esc(m.name)+'</td><td>'+esc(m.cat)+'</td><td class=num>₩'+m.unit.toLocaleString()+'/개</td>'+
   '<td class=num>₩'+m.med.toLocaleString()+'/개</td><td class=num><b class=warn>'+m.ratio+'×</b></td></tr>'}).join('')
   || '<tr><td colspan=5 class=cnt>없음</td></tr>';
 document.getElementById('tbSigSp').innerHTML=(S.spread||[]).map(function(s){
  var sus=s.sp>300;return '<tr><td>'+esc(s.name)+'</td><td>'+esc(s.cat)+'</td>'+
   '<td class=num>'+won(s.min)+' ~ '+won(s.max)+'</td><td class=num>'+(sus?'<b class=warn>':'')+s.sp+'%'+(sus?'</b>':'')+'</td>'+
   '<td class=num>'+s.nm+'</td><td>'+(sus?'<span class=mm>매칭 의심</span>':'<span class=pill p-ok><i></i>정상</span>')+'</td></tr>'}).join('');
 var lm=S.low_malls||[],mx=lm.length?lm[0][1]:1;
 document.getElementById('tbSigMall').innerHTML=lm.map(function(p){var w=Math.round(p[1]/mx*100);
  return '<tr><td><b>'+esc(p[0])+'</b></td><td><div class=lbar style="width:'+w+'%;background:#9fb0fc"></div></td><td class=num>'+p[1]+'</td></tr>'}).join('')}
/* 카테고리 */
function renderCats(){
 document.getElementById('tbCatIns').innerHTML=CATS.ins.map(function(c){
  var chips=(c.dims||[]).map(function(d){return '<span class=chip>'+esc(d)+'</span>'}).join(' ');
  return '<tr><td><b>'+esc(c.cat)+'</b></td><td class=num>'+c.n.toLocaleString()+'</td><td class=num>'+c.ins_pct+'%</td><td class=num>'+won(c.med)+'</td><td class=num>'+(c.unit_med?won(c.unit_med)+'/개':'—')+'</td><td class=num>'+c.spread+'%</td><td>'+chips+'</td></tr>'}).join('');
 document.getElementById('tbCatIdn').innerHTML=CATS.idn.map(function(c){
  return '<tr><td><b>'+esc(c.cat)+'</b></td><td class=num>'+c.n.toLocaleString()+'</td><td class=num>'+won(c.med)+'</td></tr>'}).join('')}
/* 차트 */
function dpr(c){var r=c.getBoundingClientRect(),d=window.devicePixelRatio||1;c.width=r.width*d;c.height=r.height*d;var x=c.getContext('2d');x.setTransform(d,0,0,d,0,0);return {x:x,w:r.width,h:r.height}}
function area(){var c=document.getElementById('area');var o=dpr(c),x=o.x,w=o.w,h=o.h;var L=D.area.labels,V=D.area.vals,n=V.length;var mx=Math.max.apply(0,V)*1.12||1;var pad=28,bw=h-pad-22,x0=6,x1=w-6;
 function px(i){return x0+(x1-x0)*i/(n-1)}function py(v){return pad+bw-bw*v/mx}
 var g=x.createLinearGradient(0,pad,0,pad+bw);g.addColorStop(0,'rgba(79,110,247,.28)');g.addColorStop(1,'rgba(79,110,247,0)');
 x.strokeStyle='#f0f2f7';for(var k=0;k<=3;k++){var gy=pad+bw*k/3;x.beginPath();x.moveTo(x0,gy);x.lineTo(x1,gy);x.stroke()}
 x.beginPath();x.moveTo(px(0),py(V[0]));for(var i=1;i<n;i++)x.lineTo(px(i),py(V[i]));x.lineTo(px(n-1),pad+bw);x.lineTo(px(0),pad+bw);x.closePath();x.fillStyle=g;x.fill();
 x.beginPath();x.moveTo(px(0),py(V[0]));for(i=1;i<n;i++)x.lineTo(px(i),py(V[i]));x.strokeStyle='#4f6ef7';x.lineWidth=2.4;x.lineJoin='round';x.stroke();
 x.fillStyle='#aeb6c6';x.font='10px -apple-system';x.textAlign='center';for(i=0;i<n;i++)x.fillText(L[i].length>5?L[i].slice(0,5):L[i],px(i),h-6);
 c._hit={px:px,py:py,L:L,V:V,n:n,x:x,o:o,redraw:area};document.getElementById('areaPeak').textContent=Math.max.apply(0,V).toLocaleString()+' 개'}
function bar(){var c=document.getElementById('bar');var o=dpr(c),x=o.x,w=o.w,h=o.h;var L=D.bar.labels,V=D.bar.vals,n=V.length;var mx=Math.max.apply(0,V)*1.15||1;var pad=18,bh=h-pad-22,gap=w/n,bwd=Math.min(34,gap*0.5);
 x.strokeStyle='#f0f2f7';for(var k=0;k<=3;k++){var gy=pad+bh*k/3;x.beginPath();x.moveTo(0,gy);x.lineTo(w,gy);x.stroke()}
 for(var i=0;i<n;i++){var cx=gap*i+gap/2,vh=bh*V[i]/mx,yy=pad+bh-vh;var g=x.createLinearGradient(0,yy,0,pad+bh);g.addColorStop(0,'#5b78fb');g.addColorStop(1,'#9fb0fc');x.fillStyle=g;rr(x,cx-bwd/2,yy,bwd,vh,5);x.fill();
  x.fillStyle='#aeb6c6';x.font='10px -apple-system';x.textAlign='center';x.fillText(L[i].length>6?L[i].slice(0,6):L[i],cx,h-6)}
 document.getElementById('barPeak').textContent=Math.max.apply(0,V).toLocaleString()}
function rr(x,a,b,w,h,r){r=Math.min(r,h);x.beginPath();x.moveTo(a+r,b);x.arcTo(a+w,b,a+w,b+h,r);x.arcTo(a+w,b+h,a,b+h,0);x.arcTo(a,b+h,a,b,0);x.arcTo(a,b,a+w,b,r);x.closePath()}
var tip=document.createElement('div');tip.style.cssText='position:fixed;pointer-events:none;display:none;background:#1e2532;color:#fff;padding:8px 11px;border-radius:9px;font:600 12px -apple-system;z-index:9;box-shadow:0 8px 20px rgba(20,30,60,.25)';document.body.appendChild(tip);
function bindHover(){var c=document.getElementById('area');c.onmousemove=function(e){var H=c._hit;if(!H)return;var r=c.getBoundingClientRect();var mx=e.clientX-r.left;var best=0,bd=1e9;for(var i=0;i<H.n;i++){var d=Math.abs(H.px(i)-mx);if(d<bd){bd=d;best=i}}H.redraw();var X=H.px(best),Y=H.py(H.V[best]);H.x.strokeStyle='#cdd6ee';H.x.lineWidth=1;H.x.beginPath();H.x.moveTo(X,18);H.x.lineTo(X,H.o.h-22);H.x.stroke();H.x.fillStyle='#4f6ef7';H.x.beginPath();H.x.arc(X,Y,4.5,0,7);H.x.fill();H.x.strokeStyle='#fff';H.x.lineWidth=2;H.x.stroke();tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY-12)+'px';tip.innerHTML=H.L[best]+' · <b>'+H.V[best].toLocaleString()+'</b> 개'};c.onmouseleave=function(){var H=c._hit;if(H)H.redraw();tip.style.display='none'}}
function draw(){area();bar();bindHover()}
draw();window.addEventListener('resize',function(){if(document.getElementById('overview').classList.contains('on'))draw()});
</script></body></html>"""


def main():
    os.makedirs(ASSETS, exist_ok=True)
    print("정형(CSV)…")
    idn = load_identity()
    print(f"  정형 상품 {idn['total']:,} · 브랜드 {idn['brands']}")
    print("정형 전속성(attrs_full) → 브랜드별 assets…")
    brand_index, attrs_total, attrs_by_slug = build_attrs()
    brand_lb = load_brands(idn["rows"], attrs_by_slug)
    print(f"  전속성 상품 {attrs_total:,} · 리더보드 {len(brand_lb)} 브랜드")
    print("여론(Mongo)…")
    ins = load_insight()
    if ins:
        print(f"  카탈로그 {ins['cats_total']:,} · 인사이트 {ins['cats_ins']:,} · 편차중앙 {ins['med_spread']}% · "
              f"가성비긍정 {ins['val_pos']}% · 불일치 {ins['n_mismatch']}(over {ins['n_over']}/under {ins['n_under']})")
    html = render(idn, ins, brand_index, brand_lb, attrs_total)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✓ {OUT}  (shell {len(html)//1024} KB)")


if __name__ == "__main__":
    main()
