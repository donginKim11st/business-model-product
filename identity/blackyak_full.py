#!/usr/bin/env python3
"""
블랙야크 공식몰(byn.kr) 전수(全數) 추출 (stdlib only). 재개 가능.

플랫폼: 자체 PHP (BYN MALL). 후크: DOM.
  · 리스트: /blackyak/shop/big_section.php?cno1={N}&page={p}  (20개/page)
  · 상세:   /blackyak/shop/detail.php?pno={HASH}

전략:
  Phase 1 (list)  : 전 카테고리(cno1) × 전 페이지(TotalCnt로 페이지수 산출) 스윕.
                    리스트 이미지 URL(picweb.../workbook/YYYY_BY/<STYLE>_<COLOR>/)에서
                    style_code 추출 → style_code 기준으로 colorway 묶음. 디스크 체크포인트.
  Phase 2 (detail): style_code별 대표 pno 1건 상세 파싱(고시표+recopick). jsonl append 재개.
                    대표가 sparse면 같은 style의 다른 pno로 재시도.
  Phase 3 (final) : resolved style_code(상세 상품코드 or 이미지 style) 기준 dedup,
                    style_code 정렬, utf-8-sig CSV 덮어쓰기.

cap: unique style 5000 초과 시 5000에서 중단(notes 기록).
"""
import csv
import html as _html
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BASE = "https://www.byn.kr"
LIST = "/blackyak/shop/big_section.php?cno1={cno}&page={page}"
DETAIL = "/blackyak/shop/detail.php?pno={pno}"

HEADER = ["source", "brand", "style_code", "name", "color", "price",
          "currency", "category", "gender", "sizes", "origin",
          "material", "mfg_date", "url"]

CAP = 5000
WORKERS = 8
LIST_CKPT = os.path.join(OUT, "_blackyak_list.json")
DETAIL_CKPT = os.path.join(OUT, "_blackyak_detail.jsonl")
FINAL = os.path.join(OUT, "extract_brand_blackyak.csv")


def http_get(url, retries=3, timeout=25):
    url = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


_PNO_RE = re.compile(r'detail\.php\?pno=([0-9A-F]+)')
_TOTAL_RE = re.compile(r'TotalCnt"?>\s*([0-9,]+)')
_STYLE_IMG_RE = re.compile(
    r'picweb\.blackyak\.com/workbook/[^/]+/([0-9A-Za-z]+)_[0-9A-Za-z]+/')
_ROW_RE = re.compile(
    r'<th[^>]*>.*?<span[^>]*>([^<]+)</span>\s*</th>\s*<td[^>]*>(.*?)</td>', re.S)
_RECO_RE = re.compile(r'items:\s*\[\{(.*?)\}\s*\]', re.S)


def _clean(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    s = _html.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def _reco_field(block, key):
    m = re.search(r'\b' + re.escape(key) + r'\s*:\s*"([^"]*)"', block)
    return m.group(1).strip() if m else ""


def _dedupe_csv(value):
    seen, keep = set(), []
    for part in value.split(','):
        p = part.strip()
        if p and p not in seen:
            seen.add(p)
            keep.append(p)
    return ', '.join(keep)


_GENDER_MAP = [
    ("남녀공용", "남녀공용"), ("공용", "남녀공용"),
    ("남성", "남성"), ("여성", "여성"),
    ("키즈", "아동"), ("아동", "아동"), ("주니어", "아동"), ("유아", "아동"),
]


def _gender_from_name(name):
    head = name[:6]
    for kw, label in _GENDER_MAP:
        if head.startswith(kw) or kw in head:
            return label
    return ""


def discover_cnos():
    html = http_get(BASE + LIST.format(cno=1012, page=1))
    return sorted(set(re.findall(r'cno1=([0-9]+)', html)), key=int)


def list_page(cno, page):
    """returns (pairs[(pno, style|None)], total:int)"""
    html = http_get(BASE + LIST.format(cno=cno, page=page))
    total = 0
    mt = _TOTAL_RE.search(html)
    if mt:
        total = int(mt.group(1).replace(",", ""))
    blocks = re.split(r'(?=detail\.php\?pno=)', html)
    pairs, seen = [], set()
    for b in blocks[1:]:
        mp = re.match(r'detail\.php\?pno=([0-9A-F]+)', b)
        if not mp:
            continue
        pno = mp.group(1)
        if pno in seen:
            continue
        seen.add(pno)
        ms = _STYLE_IMG_RE.search(b[:900])
        pairs.append((pno, ms.group(1) if ms else None))
    return pairs, total


def parse_detail(pno):
    url = BASE + DETAIL.format(pno=pno)
    html = http_get(url)

    table = {}
    i = html.find('제조국')
    if i != -1:
        seg = html[i - 4000:i + 2000]
        for label, val in _ROW_RE.findall(seg):
            table[label.strip()] = _clean(val)

    reco = ""
    mr = _RECO_RE.search(html)
    if mr:
        reco = mr.group(1)

    name = _reco_field(reco, "title")
    if not name:
        m = re.search(r'og:description"\s+content="([^"]*)"', html)
        name = _html.unescape(m.group(1).strip()) if m else ""
    name = re.sub(r'#\d+\s*$', '', name).strip()

    price = _reco_field(reco, "sale_price") or _reco_field(reco, "price")
    currency = (_reco_field(reco, "sale_currency")
                or _reco_field(reco, "currency") or "KRW")
    category = _reco_field(reco, "c1")

    style_code = table.get("상품코드", "")
    color = table.get("색상", "")
    sizes_raw = table.get("사이즈", "")
    sizes = "|".join(s.strip() for s in sizes_raw.split(',') if s.strip())
    origin = table.get("제조국", "")
    material = _dedupe_csv(table.get("소재", ""))
    mfg_date = table.get("제조년월", "")
    gender = _gender_from_name(name)

    return {
        "source": "blackyak", "brand": "블랙야크",
        "style_code": style_code, "name": name, "color": color,
        "price": price, "currency": currency, "category": category,
        "gender": gender, "sizes": sizes, "origin": origin,
        "material": material, "mfg_date": mfg_date, "url": url,
    }


# ---------- Phase 1: list sweep (resumable) ----------
def phase_list():
    state = {"styles": {}, "unknown": [], "done_cnos": [], "totals": {}}
    if os.path.exists(LIST_CKPT):
        with open(LIST_CKPT, encoding="utf-8") as f:
            state = json.load(f)
        print(f"[list] 체크포인트 재개: styles={len(state['styles'])} "
              f"done_cnos={len(state['done_cnos'])}", file=sys.stderr)

    cnos = discover_cnos()
    print(f"[list] 카테고리 {len(cnos)}개", file=sys.stderr)
    styles = state["styles"]
    unknown = set(state["unknown"])
    done = set(state["done_cnos"])

    for ci, cno in enumerate(cnos, 1):
        if cno in done:
            continue
        try:
            pairs1, total = list_page(cno, 1)
        except Exception as e:  # noqa: BLE001
            print(f"[list] cno={cno} p1 실패: {e}", file=sys.stderr)
            continue
        state["totals"][cno] = total
        # TotalCnt로 페이지수 산출. clamp/오차 대비 +1 페이지 여유(중복은 dedup).
        pages = max(1, math.ceil(total / 20)) if total else 1
        page_pairs = {1: pairs1}
        if pages > 1:
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                futs = {ex.submit(list_page, cno, p): p
                        for p in range(2, pages + 2)}
                for fut in as_completed(futs):
                    p = futs[fut]
                    try:
                        pr, _ = fut.result()
                        page_pairs[p] = pr
                    except Exception as e:  # noqa: BLE001
                        print(f"[list] cno={cno} p{p} 실패: {e}",
                              file=sys.stderr)
        added = 0
        for p in sorted(page_pairs):
            for pno, style in page_pairs[p]:
                if style:
                    lst = styles.setdefault(style, [])
                    if pno not in lst:
                        lst.append(pno)
                        added += 1
                else:
                    unknown.add(pno)
        done.add(cno)
        state["unknown"] = sorted(unknown)
        state["done_cnos"] = sorted(done, key=int)
        with open(LIST_CKPT, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        print(f"[list] {ci}/{len(cnos)} cno={cno} total={total} pages={pages} "
              f"+{added} | styles누적={len(styles)} unknown={len(unknown)}",
              file=sys.stderr)
    return state


# ---------- Phase 2: detail fetch (resumable) ----------
def _filled(row):
    return sum(1 for k in ("style_code", "origin", "material", "sizes", "name")
               if str(row.get(k, "")).strip())


def _process_group(gkey, pnos):
    best = None
    img_style = gkey if not gkey.startswith("PNO:") else ""
    for pno in pnos[:4]:
        try:
            row = parse_detail(pno)
        except Exception as e:  # noqa: BLE001
            print(f"[detail] {pno} 실패: {e}", file=sys.stderr)
            continue
        # resolved style_code: 상세 상품코드 우선, 없으면 이미지 style
        if not row["style_code"] and img_style:
            row["style_code"] = img_style
        if best is None or _filled(row) > _filled(best):
            best = row
        if row.get("origin") and row.get("material"):
            break  # 충분히 채워짐
    if best is None:
        best = {k: "" for k in HEADER}
        best["source"] = "blackyak"
        best["brand"] = "블랙야크"
        if img_style:
            best["style_code"] = img_style
    best["_gkey"] = gkey
    return best


def phase_detail(state):
    styles = state["styles"]
    unknown = state["unknown"]

    # 작업 단위: 각 style 그룹(이미지 style) + unknown pno 개별 그룹
    groups = []  # (group_key, [pnos])
    for st in sorted(styles.keys()):
        groups.append((st, styles[st]))
    for pno in unknown:
        groups.append(("PNO:" + pno, [pno]))

    capped = False
    if len(groups) > CAP:
        groups = groups[:CAP]
        capped = True

    done_keys = set()
    if os.path.exists(DETAIL_CKPT):
        with open(DETAIL_CKPT, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    done_keys.add(rec["_gkey"])
                except Exception:  # noqa: BLE001
                    pass
        print(f"[detail] 체크포인트 재개: {len(done_keys)}건 완료", file=sys.stderr)

    pending = [(gk, ps) for gk, ps in groups if gk not in done_keys]
    print(f"[detail] 대상 {len(pending)}/{len(groups)} (워커 {WORKERS})",
          file=sys.stderr)
    lock = threading.Lock()
    counter = {"n": 0}
    fh = open(DETAIL_CKPT, "a", encoding="utf-8")
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(_process_group, gk, ps): gk
                    for gk, ps in pending}
            for fut in as_completed(futs):
                try:
                    rec = fut.result()
                except Exception as e:  # noqa: BLE001
                    print(f"[detail] group 실패: {e}", file=sys.stderr)
                    continue
                with lock:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    counter["n"] += 1
                    if counter["n"] % 100 == 0:
                        print(f"[detail] {counter['n']}/{len(pending)} ...",
                              file=sys.stderr)
    finally:
        fh.close()
    return capped, len(groups)


# ---------- Phase 3: finalize ----------
def phase_final():
    rows = {}
    with open(DETAIL_CKPT, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rec.pop("_gkey", None)
            key = rec.get("style_code", "").strip() or rec.get("url", "")
            # 더 잘 채워진 행 우선
            if key not in rows or _filled(rec) > _filled(rows[key]):
                rows[key] = rec
    ordered = sorted(rows.values(), key=lambda r: r.get("style_code", ""))
    with open(FINAL, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in ordered:
            w.writerow({k: r.get(k, "") for k in HEADER})
    return len(ordered)


def main():
    os.makedirs(OUT, exist_ok=True)
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    capped = False
    ngroups = 0
    if phase in ("all", "list"):
        state = phase_list()
    else:
        with open(LIST_CKPT, encoding="utf-8") as f:
            state = json.load(f)
    print(f"[list] 완료: unique styles={len(state['styles'])} "
          f"unknown={len(state['unknown'])}", file=sys.stderr)
    if phase in ("all", "detail"):
        capped, ngroups = phase_detail(state)
    if phase in ("all", "final"):
        n = phase_final()
        print(f"\n=== 완료 === 최종 행수: {n} | cap_hit={capped} "
              f"| groups={ngroups}")
        print("경로:", FINAL)


if __name__ == "__main__":
    main()
