#!/usr/bin/env python3
"""
잔스포츠 공식몰(jansport.co.kr, cafe24) 서버측 **전수(全數)** 추출 (stdlib only).

extract_jansport.py(표본 120개)의 후크/파서를 재사용하되:
  · 표본 캡(TARGET) / 페이지 상한(MAX_PAGES) 제거 → 빈 페이지(신규 0)까지 크롤
  · **전 카테고리 union**: 홈 메뉴의 모든 cate_no 를 끝까지 돌아 product_no 합집합
    (전체상품 cate_no=63 외 콜라보/세일/아트리프팅 등에만 있는 상품도 포착)
  · 카테고리 라벨: 주요 제품군 카테고리 우선(첫 히트가 라벨), 없으면 '전체상품'
  · 중간 저장: product_no 단위로 progress JSONL append + flush → 재개 가능
  · 거대(>5000) 방지 캡: 5000 에서 중단, notes 기록
출력: outputs/extract_brand_jansport.csv (utf-8-sig), style_code 기준 dedup.
  헤더: source,brand,style_code,name,color,price,currency,category,gender,sizes,
        origin,material,mfg_date,url
"""
import csv
import html as ihtml
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
OUT_CSV = os.path.join(OUT, "extract_brand_jansport.csv")
PROGRESS = os.path.join(OUT, "_jansport_progress.jsonl")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE = "https://jansport.co.kr"

HEADER = ["source", "brand", "style_code", "name", "color", "price",
          "currency", "category", "gender", "sizes", "origin",
          "material", "mfg_date", "url"]

# 라벨 우선순위(제품군 부서 먼저 → 첫 히트가 라벨). 63(전체상품)은 라벨 폴백용으로 맨 뒤.
PRIORITY = [44, 130, 59, 64, 88, 87, 85, 60, 104]
ALL_CATE = 63
HARD_CAP = 5000


def fetch(url, timeout=30, retries=2):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def discover_cats():
    """홈 메뉴에서 product list 카테고리(cate_no→라벨) 발견 → 크롤 순서 정렬."""
    st, h = fetch(BASE + "/index.html")
    cats = {}
    for m in re.finditer(r'list\.html\?cate_no=(\d+)[^>]*>(.*?)</a>', h, re.S):
        no = int(m.group(1))
        txt = re.sub(r'\s+', ' ', ihtml.unescape(re.sub(r'<[^>]+>', '', m.group(2)))).strip()
        if txt and no not in cats:
            cats[no] = txt
    # 정렬: 우선 부서 → 나머지 → 전체상품(63) 맨 뒤
    ordered = []
    for no in PRIORITY:
        if no in cats:
            ordered.append((no, cats[no]))
    for no in sorted(cats):
        if no in PRIORITY or no == ALL_CATE:
            continue
        ordered.append((no, cats[no]))
    if ALL_CATE in cats:
        ordered.append((ALL_CATE, cats[ALL_CATE]))
    elif ALL_CATE not in [c for c, _ in ordered]:
        ordered.append((ALL_CATE, "전체상품"))
    return ordered


def list_ids(cate, page):
    st, h = fetch(f"{BASE}/product/list.html?cate_no={cate}&page={page}")
    ids = []
    for m in re.findall(r'/product/[^"\']*?/(\d+)/category/', h):
        if m not in ids:
            ids.append(m)
    cnt = ""
    mc = re.search(r'prdCount[^0-9]*([\d,]+)', h)
    if mc:
        cnt = mc.group(1).replace(",", "")
    return ids, cnt


def crawl_category(cate, label, master, labels, page_log, max_pages=200):
    """한 카테고리를 신규 0까지 페이지네이션. master(순서유지 set)/labels 갱신."""
    pages = 0
    cnt = ""
    for page in range(1, max_pages + 1):
        try:
            ids, c = list_ids(cate, page)
        except Exception as e:  # noqa: BLE001
            print(f"[list] cate={cate} p={page} 실패: {e}", file=sys.stderr)
            break
        if page == 1:
            cnt = c
        new = 0
        for i in ids:
            if i not in master:
                master[i] = None
                new += 1
            if i not in labels:          # 첫 히트(우선순위 순서) 라벨 고정
                labels[i] = label
        pages += 1
        time.sleep(0.2)
        if new == 0:                     # 빈/반복 페이지 → 끝
            break
    page_log[cate] = pages
    return pages, cnt


def pipe(parts):
    seen, keep = set(), []
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            keep.append(p)
    return "|".join(keep)


def options(h):
    out = []
    for m in re.findall(r'<select[^>]*option_title="[^"]*"[^>]*>(.*?)</select>',
                        h, re.S):
        for o in re.findall(r'<option[^>]*>(.*?)</option>', m):
            t = ihtml.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', o))).strip()
            if not t or '옵션을 선택' in t or set(t) <= set('- '):
                continue
            out.append(t)
    return out


def color_from_name(name):
    m = re.search(r'([A-Z][A-Z0-9]*(?:\s+[A-Z0-9]+){0,4})\s*$', name.strip())
    return m.group(1).strip() if m else ""


def parse_detail(pid, cate_label):
    url = f"{BASE}/product/detail.html?product_no={pid}"
    st, h = fetch(url)
    rec = {k: "" for k in HEADER}
    rec["source"] = "jansport"
    rec["brand"] = "잔스포츠"
    rec["category"] = cate_label
    rec["url"] = url
    rec["_pid"] = str(pid)

    name = price = currency = ""
    for b in re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            h, re.S):
        try:
            d = json.loads(b.strip())
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict) and d.get("@type") in ("Product", "ProductGroup"):
            name = d.get("name", "") or name
            offers = d.get("offers") or []
            if isinstance(offers, dict):
                offers = [offers]
            prices = []
            for o in offers:
                if isinstance(o, dict) and o.get("price") not in (None, ""):
                    try:
                        prices.append(float(o["price"]))
                    except Exception:  # noqa: BLE001
                        pass
                    currency = o.get("priceCurrency", currency)
            if prices:
                p = min(prices)
                price = str(int(p)) if p == int(p) else str(p)
            break
    name = re.sub(r'\s+', ' ', ihtml.unescape(name)).strip()
    rec["name"] = name
    rec["price"] = price
    rec["currency"] = currency or ("KRW" if price else "")

    ms = re.search(r'스타일\s*:\s*([A-Za-z0-9\-]+)', h)
    rec["style_code"] = ms.group(1).strip().upper() if ms else ""

    sizes = options(h)
    mc = re.search(r'용량\s*:\s*([0-9.]+\s*L)', h)
    if mc:
        sizes = sizes + [mc.group(1).replace(" ", "")]
    rec["sizes"] = pipe(sizes)

    rec["color"] = color_from_name(name)
    if re.search(r'키즈|주니어|유아|아동', name):
        rec["gender"] = "아동"
    return rec


def main():
    os.makedirs(OUT, exist_ok=True)

    # 1) 전 카테고리 발견 + union 크롤
    cats = discover_cats()
    print(f"[cats] {len(cats)}개 카테고리: "
          + ", ".join(f"{c}:{l}" for c, l in cats), file=sys.stderr)
    master = {}        # product_no -> None (순서 유지)
    labels = {}        # product_no -> 라벨(첫 히트)
    page_log = {}
    cat_counts = {}
    for cate, label in cats:
        pages, cnt = crawl_category(cate, label, master, labels, page_log)
        cat_counts[label] = cnt
        print(f"[list] cate={cate}({label}) pages={pages} prdCount={cnt} "
              f"누적union={len(master)}", file=sys.stderr)

    all_ids = list(master.keys())
    total_pages = sum(page_log.values())
    capped = False
    if len(all_ids) > HARD_CAP:
        all_ids = all_ids[:HARD_CAP]
        capped = True
    print(f"[union] product_no {len(all_ids)}개 | 총 list 페이지 {total_pages} "
          f"| capped={capped}", file=sys.stderr)

    # 2) 재개: 이미 받은 pid 로드
    done = {}
    if os.path.exists(PROGRESS):
        with open(PROGRESS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    done[str(r.get("_pid", ""))] = r
                except Exception:  # noqa: BLE001
                    pass
        print(f"[resume] progress 기존 {len(done)}건 로드", file=sys.stderr)

    # 3) 상세 크롤 (product_no 단위 append + flush)
    fail = 0
    pf = open(PROGRESS, "a", encoding="utf-8")
    todo = [i for i in all_ids if i not in done]
    for n, pid in enumerate(todo, 1):
        try:
            rec = parse_detail(pid, labels.get(pid, "전체상품"))
            done[pid] = rec
            pf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            pf.flush()
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[detail] {pid} 실패: {e}", file=sys.stderr)
        if n % 25 == 0:
            print(f"[detail] {n}/{len(todo)} ...", file=sys.stderr)
        time.sleep(0.2)
    pf.close()

    # 4) style_code 기준 dedup (공란이면 product_no 폴백) → 최종 CSV
    recs = [done[i] for i in all_ids if i in done]
    seen, rows = set(), []
    for r in recs:
        sc = (r.get("style_code") or "").strip().upper()
        key = sc if sc else f"NO:{r.get('_pid','')}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({k: r.get(k, "") for k in HEADER})

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    filled = {k: sum(1 for r in rows if str(r.get(k, "")).strip()) for k in HEADER}
    print(f"\n=== 완료 === union={len(all_ids)} 상세성공={len(recs)} "
          f"dedup후행수={len(rows)} (실패 {fail}) 총list페이지={total_pages} "
          f"capped={capped}")
    print("채워진 컬럼:", {k: v for k, v in filled.items() if v})
    print("cat_counts:", cat_counts)
    print("경로:", OUT_CSV)


if __name__ == "__main__":
    main()
