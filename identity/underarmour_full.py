#!/usr/bin/env python3
"""언더아머(SFCC/Demandware) 공식몰 전수(全數) 추출 — 재개 가능.

전수 전략(여러 소스 union, pid=style_code 로 dedup):
  1) 제품 사이트맵  /ko-kr/sitemap_0-product.xml         (정식 전체 목록)
  2) Search-UpdateGrid?cgid=root&start=N&sz=120 전 페이지 (카탈로그 루트=전 카테고리)
  3) 검색어 다수 q=...                                    (보강)
각 PDP 의 ld+json(Product/ProductGroup)에서
  name / price / currency / color(변형색) / gender(audience) / category(breadcrumb) 파싱.
gosi(원산지/소재/제조연월)·sizes 는 JS 렌더라 서버측 부재 → 공란(기존 101행 baseline 도 공란).

재개: 기존 CSV 의 style_code 를 읽어 skip, 페이지(배치)마다 append. 다시 실행하면 수렴.
출력: outputs/extract_brand_underarmour.csv (14컬럼 정확, utf-8-sig), source="underarmour".
상한: 5000 (초과 시 중단·notes 기록)."""
import csv
import html as H
import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
CSVP = os.path.join(OUT, "extract_brand_underarmour.csv")
LINKS = os.path.join(OUT, "_ua_links.json")
B = "https://www.underarmour.co.kr"
DW = B + "/on/demandware.store/Sites-KR-Site/ko_KR"
GRID = DW + "/Search-UpdateGrid"
COLS = ["source", "brand", "style_code", "name", "color", "price", "currency",
        "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
CAP = 5000
WORKERS = 12

S = requests.Session()
HDR = {"Accept-Language": "ko-KR,ko;q=0.9"}


def get(u, ajax=False, timeout=40):
    h = dict(HDR)
    if ajax:
        h["X-Requested-With"] = "XMLHttpRequest"
    return S.get(urllib.parse.quote(u, safe=":/?=&%#+,"), impersonate="chrome",
                 timeout=timeout, headers=h).text


def flat(x):
    return x if isinstance(x, list) else [x]


PID_HREF = re.compile(r'href="(/ko-kr/p/[^"]+?/(\d+)\.html)')
NEXTLINK = re.compile(r'data-url="[^"]*Search-UpdateGrid[^"]*start=(\d+)[^"]*"')
DATAPID = re.compile(r'data-pid="(\d+)"')


def add_links(out, body):
    n = 0
    for m in PID_HREF.finditer(body):
        href, pid = m.group(1), m.group(2)
        if pid not in out:
            out[pid] = B + href
            n += 1
    return n


def grid_crawl(out, cgid=None, q=None, sz=120, label=""):
    """Page through one grid source into out{pid:url}; return new-pid count."""
    start, new, pages = 0, 0, 0
    while True:
        if cgid is not None:
            url = "%s?cgid=%s&start=%d&sz=%d" % (GRID, cgid, start, sz)
        else:
            url = "%s?q=%s&start=%d&sz=%d" % (GRID, urllib.parse.quote(q), start, sz)
        try:
            body = get(url, ajax=True)
        except Exception as e:
            print("  [grid %s] err start=%d %s" % (label, start, str(e)[:50]))
            break
        pages += 1
        new += add_links(out, body)
        if not DATAPID.search(body):
            break
        if not NEXTLINK.search(body):
            break
        start += sz
        if start > CAP * 3:
            break
        time.sleep(0.15)
    print("  [grid %s] %d pages, +%d new (total map=%d)" % (label, pages, new, len(out)))
    return new, pages


def sitemap_links(out):
    try:
        body = get(B + "/ko-kr/sitemap_0-product.xml", timeout=60)
    except Exception as e:
        print("  [sitemap] err", str(e)[:60])
        return 0
    new = 0
    for u in re.findall(r"<loc>([^<]+)</loc>", body):
        m = re.search(r"/p/.+?/(\d+)\.html", u)
        if m and m.group(1) not in out:
            out[m.group(1)] = u
            new += 1
    print("  [sitemap] +%d (total map=%d)" % (new, len(out)))
    return new


def discover():
    if os.path.exists(LINKS):
        out = json.load(open(LINKS, encoding="utf-8"))
        print("재사용: 링크맵 %d개 (%s)" % (len(out), LINKS))
        return out
    out = {}
    stats = {}
    print("PHASE 1 — 링크 수집")
    b = len(out); sitemap_links(out); stats["sitemap"] = len(out) - b
    b = len(out); grid_crawl(out, cgid="root", label="root"); stats["root"] = len(out) - b
    # 보강(gap-check): root/sitemap 외 잔여를 잡는 소수 검색어
    queries = ["outlet", "kids", "양말", "모자", "언더아머"]
    b = len(out)
    for q in queries:
        grid_crawl(out, q=q, label="q:" + q)
    stats["search"] = len(out) - b
    json.dump(out, open(LINKS, "w", encoding="utf-8"), ensure_ascii=False)
    print("소스별 신규기여:", stats, "→ 총 unique pid =", len(out))
    return out


# ---------- PDP 파싱 ----------
def parse_pdp(pid, url):
    body = get(url)
    prod = grp = bc = None
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', body, re.S):
        try:
            j = json.loads(m.group(1).strip())
        except Exception:
            continue
        for n in flat(j):
            if not isinstance(n, dict):
                continue
            t = n.get("@type")
            if t == "Product":
                prod = n
            elif t == "ProductGroup":
                grp = n
            elif t == "BreadcrumbList":
                bc = n
    src = prod or grp
    if not src:
        return None
    # price
    offers = flat((src.get("offers") or (grp.get("offers") if grp else None)) or [])
    price = ""
    cur = "KRW"
    for o in offers:
        if isinstance(o, dict):
            p = o.get("price") or o.get("lowPrice")
            if p:
                price = str(p).replace(".0", "")
                cur = o.get("priceCurrency") or cur
                break
    # colors from variants
    colors = []
    if grp:
        for v in flat(grp.get("hasVariant") or []):
            if isinstance(v, dict) and v.get("color"):
                c = str(v["color"]).strip()
                if c and c not in colors:
                    colors.append(c)
    if not colors and src.get("color"):
        colors = [str(src["color"]).strip()]
    # gender from audience
    gender = ""
    aud = src.get("audience") or (prod.get("audience") if prod else None)
    if isinstance(aud, dict):
        gender = aud.get("GenderType") or aud.get("suggestedGender") or ""
    if not gender:
        if "/men" in url or "/mens" in url or "남성" in (src.get("name") or ""):
            gender = "men"
        elif "/women" in url or "/womens" in url or "여성" in (src.get("name") or ""):
            gender = "women"
    # category from breadcrumb
    category = ""
    if bc:
        names = [it.get("name", "") for it in flat(bc.get("itemListElement") or [])
                 if isinstance(it, dict) and it.get("name")]
        category = " > ".join(names)
    brand = src.get("brand")
    brand = brand.get("name", "") if isinstance(brand, dict) else (brand or "")
    return {
        "source": "underarmour", "brand": "언더아머", "style_code": pid,
        "name": src.get("name", ""), "color": "|".join(colors), "price": price,
        "currency": cur, "category": category, "gender": gender, "sizes": "",
        "origin": "", "material": "", "mfg_date": "", "url": url,
    }


def load_done():
    done = set()
    if os.path.exists(CSVP):
        for r in csv.DictReader(open(CSVP, encoding="utf-8-sig")):
            if r.get("style_code"):
                done.add(r["style_code"])
    return done


def append_rows(rows, header_needed):
    mode = "w" if header_needed else "a"
    with open(CSVP, mode, encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if header_needed:
            w.writeheader()
        w.writerows(rows)


def main():
    links = discover()
    done = load_done()
    todo = [(pid, u) for pid, u in links.items() if pid not in done]
    print("PHASE 2 — PDP 파싱: 전체 %d, 기완료 %d, 대상 %d" % (len(links), len(done), len(todo)))
    capped = False
    if len(done) + len(todo) > CAP:
        todo = todo[:max(0, CAP - len(done))]
        capped = True
        print("  ** CAP %d 적용: 대상 %d 로 절삭 **" % (CAP, len(todo)))
    header_needed = not os.path.exists(CSVP) or os.path.getsize(CSVP) == 0
    batch, total_added, fail = [], 0, 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(parse_pdp, pid, u): pid for pid, u in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                d = fut.result()
                if d and d["name"]:
                    batch.append(d)
            except Exception:
                fail += 1
            if len(batch) >= 50:
                append_rows(batch, header_needed)
                header_needed = False
                total_added += len(batch)
                batch = []
            if i % 100 == 0:
                print("  …%d/%d (added=%d fail=%d %.0fs)" %
                      (i, len(todo), total_added, fail, time.time() - t0))
    if batch:
        append_rows(batch, header_needed)
        total_added += len(batch)
    # final stats
    n = sum(1 for _ in csv.DictReader(open(CSVP, encoding="utf-8-sig")))
    print("완료: 신규 %d행, CSV 총 %d행, 실패 %d, capped=%s, %.0fs" %
          (total_added, n, fail, capped, time.time() - t0))
    filled = {}
    rows = list(csv.DictReader(open(CSVP, encoding="utf-8-sig")))
    for c in ("name", "price", "color", "category", "gender", "origin", "material"):
        filled[c] = sum(1 for r in rows if r.get(c, "").strip())
    print("채움:", filled)


if __name__ == "__main__":
    main()
