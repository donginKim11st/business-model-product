#!/usr/bin/env python3
"""
나이키(nike.com/kr) 공식몰 '전수' 추출.

전략(검증 2026-06-29):
  · 브라우즈 카테고리 Wall = 진짜 카탈로그(검색 q=는 relevance 랭킹이라 누락 위험 → 브라우즈 사용).
  · 1페이지: /kr/w/<slug> HTML 의 __NEXT_DATA__ → Wall.productGroupings + pageData.next
  · 2페이지~: pageData.next/pages.next 의 product_wall API anchor 페이징
      GET https://api.nike.com/discover/product_wall/v1/...?anchor=N&count=24
      헤더 nike-api-caller-id: nike:dotcom:browse:wall.client:2.0  (없으면 403)
  · 12개 브로드 Wall(남/여/키즈 × 신발/의류/용품 + 조던) 합집합 → style_code dedup.

2단계:
  enumerate : 전 Wall 전 페이지 → 기본필드(코드/이름/컬러/가격/카테고리/성별/URL) CSV append (재개가능)
  enrich    : 각 PDP __NEXT_DATA__ 로 사이즈/원산지/소재/제조년월 best-effort 보강(재개가능)

출력: outputs/extract_brand_nike.csv
  헤더: source,brand,style_code,name,color,price,currency,category,gender,sizes,origin,material,mfg_date,url (utf-8-sig)
"""
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
CSV_PATH = os.path.join(OUT, "extract_brand_nike.csv")
STATE_PATH = os.path.join(OUT, "_nike_full_state.json")
DETAIL_JSONL = os.path.join(OUT, "_nike_full_detail.jsonl")

HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
CALLER = "nike:dotcom:browse:wall.client:2.0"
API_BASE = "https://api.nike.com"
SITE = "https://www.nike.com/kr/w/"
CAP = 5000

# (slug, fallback_gender, fallback_category)
WALLS = [
    ("men-shoes-nik1zy7ok", "남성", "신발"),
    ("men-apparel-6ymx6znik1", "남성", "의류"),
    ("men-accessories-equipment-awwpwznik1", "남성", "용품"),
    ("women-shoes-5e1x6zy7ok", "여성", "신발"),
    ("women-apparel-5e1x6z6ymx6", "여성", "의류"),
    ("women-accessories-equipment-5e1x6zawwpw", "여성", "용품"),
    ("kids-shoes-v4dhzy7ok", "키즈", "신발"),
    ("kids-apparel-6ymx6zv4dh", "키즈", "의류"),
    ("kids-accessories-equipment-awwpwzv4dh", "키즈", "용품"),
    ("men-jordan-37eefznik1", "남성", "조던"),
    ("women-jordan-37eefz5e1x6", "여성", "조던"),
    ("kids-jordan-37eefzv4dh", "키즈", "조던"),
    # 완전성 검증용(메인 그리드에 안 잡힐 수 있는 별도 컬렉션/세일)
    ("clearance-3yaep", "", "세일"),
    ("men-clearance-3yaepznik1", "남성", "세일"),
    ("women-clearance-3yaepz5e1x6", "여성", "세일"),
    ("kids-clearance-3yaepzv4dh", "키즈", "세일"),
    ("nikeskims-shoes-b2asdzy7ok", "여성", "신발"),
    ("korea-national-team-collection-4ebjt", "", "국가대표"),
]

try:
    from curl_cffi import requests as _cc
    _HAS = True
except ImportError:
    _HAS = False

_ND = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def _q(url):
    return urllib.parse.quote(url, safe=":/?=&%#+,")


def http_get(url, headers=None, retries=3, timeout=30):
    url = _q(url)
    h = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    if headers:
        h.update(headers)
    last = None
    for i in range(retries + 1):
        try:
            if _HAS:
                r = _cc.get(url, impersonate="chrome", timeout=timeout, headers=h)
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}")
                return r.text
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"GET fail {url[:80]}: {last}")


def parse_nextdata(html):
    m = _ND.search(html)
    return json.loads(m.group(1)) if m else None


# -------------------------------------------------- gender / category from subTitle
_GENDERS = ["남녀공용", "남성", "여성", "주니어", "유아", "리틀 키즈", "리틀키즈",
            "빅 키즈", "빅키즈", "베이비", "토들러", "키즈", "아동", "공용"]


def parse_gender(subtitle, fallback):
    s = subtitle or ""
    if "남녀공용" in s or "공용" in s:
        return "공용"
    if "남성" in s:
        return "남성"
    if "여성" in s:
        return "여성"
    if any(k in s for k in ("주니어", "유아", "리틀", "빅 키즈", "빅키즈", "베이비",
                            "토들러", "키즈", "아동")):
        return "키즈"
    return fallback


def parse_category(subtitle, fallback):
    s = (subtitle or "").strip()
    for g in _GENDERS:
        if s.startswith(g):
            s = s[len(g):].strip()
            break
    return s or fallback


# -------------------------------------------------- enumerate (phase 1)
def rows_from_groupings(groupings, w_gender, w_cat):
    out = []
    for g in groupings or []:
        if not isinstance(g, dict):
            continue
        for p in g.get("products") or []:
            code = p.get("productCode")
            if not code:
                continue
            copy = p.get("copy") or {}
            sub = copy.get("subTitle", "")
            pr = p.get("prices") or {}
            dc = p.get("displayColors") or {}
            out.append({
                "source": "nike", "brand": "nike", "style_code": code,
                "name": (copy.get("title") or "").strip(),
                "color": dc.get("colorDescription", ""),
                "price": pr.get("currentPrice", ""),
                "currency": pr.get("currency", "KRW"),
                "category": parse_category(sub, w_cat),
                "gender": parse_gender(sub, w_gender),
                "sizes": "", "origin": "", "material": "", "mfg_date": "",
                "url": (p.get("pdpUrl") or {}).get("url", ""),
            })
    return out


def load_state():
    if os.path.exists(STATE_PATH):
        return json.load(open(STATE_PATH, encoding="utf-8"))
    return {"walls_done": [], "cur_wall": None, "cur_next": None}


def save_state(st):
    json.dump(st, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False)


def load_seen():
    seen = set()
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("style_code"):
                    seen.add(r["style_code"])
    return seen


def append_rows(rows):
    new = os.path.exists(CSV_PATH) is False
    with open(CSV_PATH, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def enumerate_all():
    os.makedirs(OUT, exist_ok=True)
    st = load_state()
    seen = load_seen()
    # fresh start if no state and CSV absent
    if not os.path.exists(CSV_PATH):
        append_rows([])  # writes header
    print(f"[enum] resume: {len(seen)} seen, walls_done={st['walls_done']}", flush=True)
    pages_total = 0
    capped = False
    for slug, w_gender, w_cat in WALLS:
        if slug in st["walls_done"]:
            continue
        if len(seen) >= CAP:
            capped = True
            break
        # determine starting next-path
        if st.get("cur_wall") == slug and st.get("cur_next"):
            next_path = st["cur_next"]
            groupings = None
        else:
            html = http_get(SITE + slug)
            nd = parse_nextdata(html)
            if not nd:
                print(f"[enum] {slug}: no NEXT_DATA, skip", flush=True)
                st["walls_done"].append(slug)
                save_state(st)
                continue
            wall = nd["props"]["pageProps"]["initialState"]["Wall"]
            groupings = wall.get("productGroupings")
            pd = wall.get("pageData") or {}
            tot = pd.get("totalResources")
            next_path = pd.get("next") or ""
            pages_total += 1
            print(f"[enum] {slug}: total={tot}", flush=True)
            rows = rows_from_groupings(groupings, w_gender, w_cat)
            fresh = [r for r in rows if r["style_code"] not in seen]
            for r in fresh:
                seen.add(r["style_code"])
            if fresh:
                append_rows(fresh[:max(0, CAP - (len(seen) - len(fresh)))])
            st["cur_wall"] = slug
            st["cur_next"] = next_path
            save_state(st)
        # follow API pages
        guard = 0
        while next_path and len(seen) < CAP and guard < 400:
            guard += 1
            try:
                txt = http_get(API_BASE + next_path,
                               headers={"nike-api-caller-id": CALLER,
                                        "Accept": "application/json"})
                j = json.loads(txt)
            except Exception as e:  # noqa: BLE001
                print(f"[enum] {slug} page err: {e}", flush=True)
                time.sleep(1.0)
                continue
            gs = j.get("productGroupings")
            if not gs:
                break
            rows = rows_from_groupings(gs, w_gender, w_cat)
            fresh = [r for r in rows if r["style_code"] not in seen]
            room = CAP - len(seen)
            for r in fresh[:room]:
                seen.add(r["style_code"])
            if fresh:
                append_rows(fresh[:room])
            pages_total += 1
            next_path = (j.get("pages") or {}).get("next") or ""
            st["cur_next"] = next_path
            save_state(st)
            if pages_total % 10 == 0:
                print(f"[enum] {slug} … pages={pages_total} seen={len(seen)}", flush=True)
            time.sleep(0.15)
        if len(seen) >= CAP:
            capped = True
            break
        st["walls_done"].append(slug)
        st["cur_wall"] = None
        st["cur_next"] = None
        save_state(st)
        print(f"[enum] DONE {slug}: seen={len(seen)}", flush=True)
    print(f"[enum] FINISHED pages={pages_total} unique={len(seen)} capped={capped}", flush=True)
    return len(seen), pages_total, capped


# -------------------------------------------------- enrich (phase 2)
def _arr(v):
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return list(v.values())
    return [v] if v else []


def detail(pdp_url, code):
    html = http_get(pdp_url)
    nd = parse_nextdata(html)
    if not nd:
        return {}
    pp = nd["props"]["pageProps"]
    sp = pp.get("selectedProduct")
    if not sp or sp.get("styleColor") != code:
        for g in pp.get("productGroups", []):
            cand = (g.get("products") or {}).get(code)
            if cand:
                sp = cand
                break
    if not sp:
        return {}
    pi = sp.get("productInfo") or {}
    material = ""
    for blk in _arr(pi.get("productDetails")):
        if isinstance(blk, dict):
            body = blk.get("body")
            if isinstance(body, list):
                material += " ".join(body) + " "
    sizes = [s.get("localizedLabel") or s.get("label") for s in (sp.get("sizes") or [])]
    sizes = [s for s in sizes if s]
    out = {
        "sizes": "|".join(sizes),
        "origin": "|".join(_arr(sp.get("manufacturingCountriesOfOrigin"))),
        "material": material.strip()[:500],
    }
    # mfg_date / 고시 (있으면)
    blob = json.dumps(sp, ensure_ascii=False)
    m = re.search(r'(20\d{2}[.\-/]?\d{1,2}[.\-/]?\d{0,2}?)\s*(?:제조|생산)', blob)
    if m:
        out["mfg_date"] = m.group(1)
    return out


def enrich(budget_sec=99999, workers=8):
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    done = {}
    if os.path.exists(DETAIL_JSONL):
        for line in open(DETAIL_JSONL, encoding="utf-8"):
            try:
                d = json.loads(line)
                done[d["style_code"]] = d
            except Exception:  # noqa: BLE001
                pass
    todo = [r for r in rows if r["style_code"] not in done and r.get("url")]
    print(f"[enrich] rows={len(rows)} done={len(done)} todo={len(todo)} workers={workers}",
          flush=True)
    t0 = time.time()
    lock = threading.Lock()
    jf = open(DETAIL_JSONL, "a", encoding="utf-8")
    counter = {"n": 0}

    def work(r):
        try:
            d = detail(r["url"], r["style_code"])
        except Exception:  # noqa: BLE001
            d = {}
        d["style_code"] = r["style_code"]
        with lock:
            jf.write(json.dumps(d, ensure_ascii=False) + "\n")
            jf.flush()
            done[r["style_code"]] = d
            counter["n"] += 1
            n = counter["n"]
            if n % 200 == 0:
                print(f"[enrich] {n}/{len(todo)} ({len(done)} total) "
                      f"{(time.time()-t0)/n:.2f}s/it", flush=True)
                merge_details(rows, done)
        return d

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = []
        for r in todo:
            if time.time() - t0 > budget_sec:
                break
            futs.append(ex.submit(work, r))
        for _ in as_completed(futs):
            if time.time() - t0 > budget_sec:
                print("[enrich] budget reached", flush=True)
                break
    jf.close()
    merge_details(rows, done)
    cov = sum(1 for r in rows if (done.get(r["style_code"]) or {}).get("origin"))
    print(f"[enrich] merged. origin coverage={cov}/{len(rows)}", flush=True)
    return len(rows), cov


def merge_details(rows, done):
    for r in rows:
        d = done.get(r["style_code"])
        if not d:
            continue
        for k in ("sizes", "origin", "material", "mfg_date"):
            if d.get(k):
                r[k] = d[k]
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "enumerate"
    if mode == "enumerate":
        n, pages, capped = enumerate_all()
        print(json.dumps({"unique": n, "pages": pages, "capped": capped}))
    elif mode == "enrich":
        budget = int(sys.argv[2]) if len(sys.argv) > 2 else 99999
        workers = int(sys.argv[3]) if len(sys.argv) > 3 else 8
        enrich(budget, workers)
    else:
        print("usage: extract_nike_full.py [enumerate|enrich [budget_sec]]")


if __name__ == "__main__":
    main()
