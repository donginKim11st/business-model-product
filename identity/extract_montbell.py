#!/usr/bin/env python3
"""몽벨(montbell.co.kr) 공식몰 **전수(全數)** 추출 — 영카트/그누보드, hook=dom.

리스트: /shop/list.php?ca_id={code}&page={p}  (카드: it_id/model/name/price/soldout)
상세  : /shop/item.php?it_id={CODE}           (og:title, it_price, it_option 사이즈, breadcrumb)

전수 전략:
  - 카테고리 집합 = 홈 GNB의 119개 ca_id ∪ 6자리 리프에서 유도한 4자리 부모 노드.
    (그누보드 list.php는 ca_id 정확매칭 + 자식 LIKE 집계 → 부모/리프 모두 돌고 it_id dedup.)
  - 각 카테고리를 page=1..N, "카드<24 또는 신규 it_id 0"이면 종료(하드 상한 100p).
  - 상세는 it_id 단위 1회. style_code(it_id) 기준 dedup.

상품정보제공고시(소재/제조국/제조년월)는 상세 페이지에 **텍스트로 없고 이미지에 박혀** 있다(확인됨).
→ 원산지/소재/제조년월은 텍스트 크롤 불가. 기존 표본 CSV(OCR 산출)에서 style_code로 머지해 보존.

재개(resume) 설계 — 산출물 경로와 작업파일을 분리:
  outputs/_montbell_queue.json   : phase1 리스트 큐 {it_id: lprice}
  outputs/_montbell_rows.jsonl   : phase2 완료 행(append). 재시작 시 done-set 복원.
  outputs/extract_brand_montbell.csv : 최종 산출물(마지막에 1회 원자적 기록).
"""
import csv, html as H, json, os, re, sys, threading, time
import urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
BASE = "https://www.montbell.co.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]

FINAL = os.path.join(OUT, "extract_brand_montbell.csv")
QUEUE_CK = os.path.join(OUT, "_montbell_queue.json")
ROWS_CK = os.path.join(OUT, "_montbell_rows.jsonl")
ENRICH_SRC = os.path.join(OUT, "extract_brand_montbell.csv.preraw.bak")

HARD_PAGE_CAP = 100
HARD_PRODUCT_CAP = 5000
LIST_WORKERS = 10
DETAIL_WORKERS = 8

# 홈 GNB 119개 ca_id (homepage list.php?ca_id= 링크와 1:1)
NAV = ['a01010','a01020','a01030','a01040','a01050','a02010','a02020','a02030','a02040','a02050',
       'a03010','a03020','a040','a050','b01010','b01020','b01030','b01040','b01050','b02010','b02020',
       'b02030','b02040','b02050','b03010','b03020','b03030','c010','c020','c030','c040','c050',
       'd01010','d01020','d01030','d020','d030','d040','d050','e01010','e01020','e01030','e01040',
       'e02010','e02020','e030','e040','e050','e070','e080','e090','f010','f01010','f01020','f01030',
       'f020','f02010','f02020','f02030','f030','f03010','f03020','f03030','f03040','f040','f04010',
       'f04020','f04030','f04040','f050','f060','g01010','g01020','g01030','g01050','g01060','g01070',
       'g01080','g01090','g010a0','g010b0','g010c0','g02020','g02030','g02050','g02060','g02070',
       'g02080','g02090','g020a0','g020b0','g020c0','g020d0','g030','g03010','g03020','g03030',
       'g03050','g04010','g04020','g04030','g04040','g04050','g05010','g05020','g05030','g05040',
       'g05050','g05070','g05080','g05090','g06010','g06020','g06030','g06040','g06050','g06060',
       'g06070','g070']


def cat_set():
    """NAV ∪ (6자리 리프에서 유도한 4자리 부모 집계 노드)."""
    cats = set(NAV)
    for c in NAV:
        if len(c) == 6:
            cats.add(c[:4])
    return sorted(cats)


# curl_cffi 우선, 없으면 urllib
try:
    from curl_cffi import requests as _creq

    def get(url, timeout=25, retries=2):
        url = urllib.parse.quote(url, safe=":/?=&%#+,")
        last = None
        for i in range(retries + 1):
            try:
                r = _creq.get(url, impersonate="chrome", timeout=timeout)
                if r.status_code == 200:
                    return r.text
                last = f"HTTP {r.status_code}"
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(0.4 * (i + 1))
        sys.stderr.write(f"  ! GET fail {url} {last}\n")
        return ""
except Exception:  # noqa: BLE001
    def get(url, timeout=25, retries=2):
        url = urllib.parse.quote(url, safe=":/?=&%#+,")
        last = None
        for i in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
                    "Accept": "text/html,application/xhtml+xml"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read().decode("utf-8", "replace")
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(0.4 * (i + 1))
        sys.stderr.write(f"  ! GET fail {url} {last}\n")
        return ""


# ---------- parsing helpers ----------
_COLOR = re.compile(r'\s([A-Z][A-Z /&\-]+)\s*$')
GENDERS = [("남여공용", "공용"), ("남녀공용", "공용"), ("공용", "공용"), ("유니섹스", "공용"),
           ("남성", "남성"), ("여성", "여성"), ("키즈", "키즈"), ("아동", "키즈"),
           ("남", "남성"), ("여", "여성")]


def parse_title(title):
    title = title.strip()
    cm = _COLOR.search(title)
    if cm:
        return title[:cm.start()].strip(), cm.group(1).strip()
    return title, ""


def parse_gender(category, name):
    head = category.split(" > ")[0] if category else ""
    norm = {"남성": "남성", "여성": "여성", "공용": "공용", "키즈": "키즈", "아동": "키즈"}
    if head in norm:
        return norm[head]
    for tok, g in GENDERS:
        if re.search(r'(?:^|\s)' + re.escape(tok) + r'(?=\s|\d|$)', name):
            return g
    return ""


def parse_sizes(html):
    sizes = []
    for sel in re.finditer(r'<select[^>]*class="[^"]*it_option[^"]*"[^>]*>(.*?)</select>',
                           html, re.S):
        for om in re.finditer(r'<option value="([^"]*)"[^>]*>(.*?)</option>', sel.group(1), re.S):
            if om.group(1).strip() == "":
                continue
            lab = H.unescape(re.sub(r'<[^>]+>', '', om.group(2))).replace("\xa0", " ")
            lab = re.sub(r'\[\s*품절\s*\]', '', lab).strip()
            if lab and lab.upper() != "XXX" and lab not in sizes:
                sizes.append(lab)
    return sizes


def parse_breadcrumb(html):
    m = re.search(r'<ol class="breadcrumb">(.*?)</ol>', html, re.S)
    if not m:
        return ""
    parts = [H.unescape(re.sub(r'<[^>]+>', '', a)).strip()
             for a in re.findall(r'<a[^>]*>(.*?)</a>', m.group(1), re.S)]
    parts = [p for p in parts if p and p != "홈"]
    return " > ".join(parts)


# ---------- phase 1: list crawl ----------
def list_page(ca, p):
    html = get(f"{BASE}/shop/list.php?ca_id={ca}&page={p}")
    out = {}
    for c in re.split(r'<li>', html):
        m = re.search(r'it_id=([A-Za-z0-9]+)', c)
        if not m:
            continue
        name = re.search(r'<div class="name"[^>]*>([^<]*)</div>', c)
        if not name:
            continue
        it_id = m.group(1)
        price = re.search(r'<strong>([\d,]+)<i>', c)
        out[it_id] = price.group(1).replace(",", "") if price else ""
    return out


def crawl_category(ca):
    """page 1..N, stop at <24 cards or no-new. returns {it_id: lprice}."""
    acc = {}
    seen = set()
    for p in range(1, HARD_PAGE_CAP + 1):
        page = list_page(ca, p)
        if not page:
            break
        new = set(page) - seen
        for k, v in page.items():
            if k not in acc or (not acc[k] and v):
                acc[k] = v
        seen |= set(page)
        if len(page) < 24 or not new:
            break
    return ca, acc


def phase1_listcrawl():
    if os.path.exists(QUEUE_CK):
        with open(QUEUE_CK, encoding="utf-8") as f:
            q = json.load(f)
        print(f"[phase1] resume queue {len(q)} from checkpoint")
        return q
    cats = cat_set()
    print(f"[phase1] crawling {len(cats)} categories (NAV+parents)")
    queue = {}
    with ThreadPoolExecutor(max_workers=LIST_WORKERS) as ex:
        futs = {ex.submit(crawl_category, c): c for c in cats}
        for i, fut in enumerate(as_completed(futs), 1):
            ca, acc = fut.result()
            added = 0
            for k, v in acc.items():
                if k not in queue:
                    queue[k] = v
                    added += 1
                elif not queue[k] and v:
                    queue[k] = v
            print(f"  [{i}/{len(cats)}] {ca}: {len(acc)} items (+{added} new) | total {len(queue)}")
    with open(QUEUE_CK, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False)
    print(f"[phase1] DONE unique it_id = {len(queue)} -> {QUEUE_CK}")
    return queue


# ---------- phase 2: detail crawl ----------
_lock = threading.Lock()


def detail(it_id, lprice):
    html = get(f"{BASE}/shop/item.php?it_id={it_id}")
    if not html:
        return None
    tm = re.search(r'og:title" content="(.*?)"', html, re.S)
    title = H.unescape(tm.group(1).strip()) if tm else ""
    name, color = parse_title(title)
    pm = re.search(r'id="it_price"[^>]*value="(-?\d+)"', html)
    price = lprice
    if pm:
        v = int(pm.group(1))
        price = str(v) if v > 0 else (lprice or "")
    category = parse_breadcrumb(html)
    return {"source": "montbell", "brand": "몽벨", "style_code": it_id, "name": name,
            "color": color, "price": price, "currency": "KRW", "category": category,
            "gender": parse_gender(category, name), "sizes": "|".join(parse_sizes(html)),
            "origin": "", "material": "", "mfg_date": "",
            "url": f"{BASE}/shop/item.php?it_id={it_id}"}


def load_done():
    done = set()
    if os.path.exists(ROWS_CK):
        with open(ROWS_CK, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    done.add(json.loads(ln)["style_code"])
                except Exception:  # noqa: BLE001
                    pass
    return done


def phase2_detailcrawl(queue):
    done = load_done()
    todo = [(k, v) for k, v in queue.items() if k not in done]
    capped = len(queue) > HARD_PRODUCT_CAP
    if capped:
        # 결정적 순서로 5000개 컷
        keep = set(sorted(queue)[:HARD_PRODUCT_CAP])
        todo = [(k, v) for k, v in todo if k in keep]
    print(f"[phase2] queue={len(queue)} done={len(done)} todo={len(todo)} capped={capped}")
    fh = open(ROWS_CK, "a", encoding="utf-8")
    n = 0
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as ex:
        futs = {ex.submit(detail, k, v): k for k, v in todo}
        for fut in as_completed(futs):
            it = futs[fut]
            try:
                d = fut.result()
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"  ! detail {it} {e}\n")
                d = None
            if d:
                with _lock:
                    fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                    fh.flush()
            n += 1
            if n % 100 == 0:
                print(f"  ..{n}/{len(todo)}")
    fh.close()
    print(f"[phase2] DONE wrote {n} new rows")
    return capped


# ---------- phase 3: finalize ----------
def load_enrich():
    """기존 표본 CSV(OCR)에서 origin/material/mfg_date 보존 머지."""
    en = {}
    if os.path.exists(ENRICH_SRC):
        with open(ENRICH_SRC, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                sc = r.get("style_code", "").strip()
                if not sc:
                    continue
                en[sc] = {k: r.get(k, "").strip() for k in ("origin", "material", "mfg_date")}
    return en


def finalize():
    en = load_enrich()
    rows = {}
    with open(ROWS_CK, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            d = json.loads(ln)
            sc = d["style_code"]
            # dedup by style_code: prefer record with more filled fields
            if sc in rows:
                old = rows[sc]
                if sum(1 for k in HEADER if d.get(k)) <= sum(1 for k in HEADER if old.get(k)):
                    continue
            rows[sc] = d
    # merge enrichment
    enr_n = 0
    for sc, d in rows.items():
        e = en.get(sc)
        if e:
            for k in ("origin", "material", "mfg_date"):
                if e[k] and not d.get(k):
                    d[k] = e[k]
            if e["origin"] or e["material"] or e["mfg_date"]:
                enr_n += 1
    ordered = [rows[sc] for sc in sorted(rows)]
    tmp = FINAL + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in ordered:
            w.writerow({k: r.get(k, "") for k in HEADER})
    os.replace(tmp, FINAL)
    filled = {k: sum(1 for r in ordered if r.get(k)) for k in HEADER}
    print(f"[finalize] WROTE {FINAL} rows={len(ordered)} enriched={enr_n}")
    print("[finalize] FILLED:", filled)
    return len(ordered)


def main():
    os.makedirs(OUT, exist_ok=True)
    queue = phase1_listcrawl()
    capped = phase2_detailcrawl(queue)
    n = finalize()
    print(f"=== TOTAL {n} products (capped={capped}) ===")


if __name__ == "__main__":
    main()
