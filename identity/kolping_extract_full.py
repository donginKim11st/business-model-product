#!/usr/bin/env python3
"""콜핑(KOLPING) 공식몰 ekolping.co.kr 전수(全數) 추출기.

전략(검증됨): sitemap.xml(3346)은 불완전(cate44 1405중 450 누락) -> 전 카테고리(167)
크롤 ∪ sitemap = 진짜 id universe. 상세는 검증된 kolping_extract.detail() 재사용.

2-phase, 재개 가능:
  phase1 discover : 167개 cate_no 전 페이지(끝까지) + sitemap -> id universe 저장
                    outputs/_kolping_ids.json
  phase2 detail   : 각 id 상세 fetch, outputs/_kolping_partial.jsonl 에 줄단위 append
                    (재실행 시 성공/실패 모두 skip)
  phase3 build    : partial -> dedup(style_code 비공란 기준, 공란은 product_no) -> CSV
출력: outputs/extract_brand_kolping.csv  (utf-8-sig, 지정 스키마)
"""
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kolping_extract as K  # noqa: E402  (detail/list_ids/http_get/FIELDS 재사용)

OUT = os.path.join(HERE, "outputs")
BASE = "https://ekolping.co.kr"
IDS_FILE = os.path.join(OUT, "_kolping_ids.json")
PARTIAL = os.path.join(OUT, "_kolping_partial.jsonl")
CSV_PATH = os.path.join(OUT, "extract_brand_kolping.csv")
FIELDS = K.FIELDS
CAP = 5000
MAXPAGE = 300          # 카테고리당 안전 상한(끝/빈/반복 페이지에서 자연 종료)
DISC_WORKERS = 10      # 카테고리 동시 크롤
DET_WORKERS = 8        # 상세 동시 fetch


# ----------------------------- phase 1: discover -----------------------------
def sitemap_ids():
    h = K.http_get(f"{BASE}/sitemap.xml")
    locs = re.findall(r"<loc>(.*?)</loc>", h)
    ids, cats = set(), {}
    for u in locs:
        if "/product/" in u:
            m = re.search(r"/product/.+/(\d+)/?$", u)
            if m:
                ids.add(m.group(1))
        else:
            m = re.search(r"/category/[^/]+/(\d+)/?$", u)
            if m:
                cats[m.group(1)] = True
    return ids, sorted(cats, key=int)


def crawl_category(cate):
    """한 카테고리를 끝까지 페이지네이션. 빈/반복 페이지에서 종료."""
    ids, prev, pages = set(), None, 0
    for p in range(1, MAXPAGE + 1):
        page_ids, _ = K.list_ids(cate, p)
        if not page_ids:
            break
        s = set(page_ids)
        if s == prev:          # 마지막 페이지 반복 방지
            break
        prev = s
        ids |= s
        pages = p
        time.sleep(0.05)
    return cate, ids, pages


def discover():
    os.makedirs(OUT, exist_ok=True)
    smids, cat_nos = sitemap_ids()
    print(f"[disc] sitemap: {len(smids)} product ids, {len(cat_nos)} categories")
    universe = set(smids)
    per_cat = {}
    with ThreadPoolExecutor(max_workers=DISC_WORKERS) as ex:
        futs = {ex.submit(crawl_category, c): c for c in cat_nos}
        done = 0
        for fut in as_completed(futs):
            cate, ids, pages = fut.result()
            per_cat[cate] = {"ids": len(ids), "pages": pages}
            universe |= ids
            done += 1
            if done % 20 == 0 or done == len(cat_nos):
                print(f"[disc] {done}/{len(cat_nos)} cats; universe={len(universe)}")
    total_pages = sum(v["pages"] for v in per_cat.values())
    data = {
        "ids": sorted(universe, key=int),
        "n_ids": len(universe),
        "sitemap_ids": len(smids),
        "n_categories": len(cat_nos),
        "total_list_pages": total_pages,
        "per_cat": per_cat,
    }
    with open(IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"[disc] DONE universe={len(universe)} sitemap_only_missing="
          f"{len(universe - smids)} total_list_pages={total_pages} -> {IDS_FILE}")
    return data


# ----------------------------- phase 2: detail -------------------------------
_lock = threading.Lock()


def done_pids():
    s = set()
    if os.path.exists(PARTIAL):
        with open(PARTIAL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    s.add(str(json.loads(line)["_pid"]))
                except Exception:
                    pass
    return s


def fetch_one(pid):
    try:
        rec = K.detail(pid, "")
    except Exception as e:
        return {"_pid": pid, "_err": f"{type(e).__name__}:{e}"}
    if not rec:
        return {"_pid": pid, "_none": True}
    rec["_pid"] = pid
    return rec


def detail_phase():
    with open(IDS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    ids = [str(i) for i in data["ids"]]
    have = done_pids()
    todo = [i for i in ids if i not in have]
    print(f"[det] universe={len(ids)} done={len(have)} todo={len(todo)}")
    fh = open(PARTIAL, "a", encoding="utf-8")
    n = 0
    with ThreadPoolExecutor(max_workers=DET_WORKERS) as ex:
        futs = [ex.submit(fetch_one, i) for i in todo]
        for fut in as_completed(futs):
            rec = fut.result()
            with _lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
            n += 1
            if n % 100 == 0:
                print(f"[det] wrote {n}/{len(todo)} (total partial≈{len(have)+n})")
    fh.close()
    print(f"[det] DONE wrote {n} new; partial total≈{len(have)+n}")


# ----------------------------- phase 3: build CSV ----------------------------
def build_csv():
    recs = []
    with open(PARTIAL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("_none") or d.get("_err"):
                continue
            if not d.get("name"):
                continue
            recs.append(d)
    # dedup: style_code 비공란 기준, 공란은 product_no 별로 유지
    seen_style, out, blanks = set(), [], 0
    for d in recs:
        sc = (d.get("style_code") or "").strip()
        if sc:
            if sc in seen_style:
                continue
            seen_style.add(sc)
        else:
            blanks += 1
        out.append({k: d.get(k, "") for k in FIELDS})
    # 5000 cap: 초과 시 product_no(=신상) 내림차순으로 5000
    capped = False
    if len(out) > CAP:
        # _pid 기준 정렬 위해 원본 보존 필요 -> recs에서 매핑
        order = {id(o): None for o in out}
        out.sort(key=lambda r: int(re.search(r"product_no=(\d+)", r["url"]).group(1))
                 if re.search(r"product_no=(\d+)", r["url"]) else 0, reverse=True)
        out = out[:CAP]
        capped = True
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    nonblank = len(seen_style)
    print(f"[csv] recs_with_name={len(recs)} unique_nonblank_style={nonblank} "
          f"blank_style={blanks} rows_out={len(out)} capped={capped} -> {CSV_PATH}")
    filled = {k: sum(1 for r in out if str(r[k]).strip()) for k in FIELDS}
    print("[csv] filled:", json.dumps(filled, ensure_ascii=False))
    return len(out), capped


def main():
    os.makedirs(OUT, exist_ok=True)
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    if phase in ("discover", "all"):
        discover()
    if phase in ("detail", "all"):
        detail_phase()
    if phase in ("build", "all"):
        build_csv()


if __name__ == "__main__":
    main()
