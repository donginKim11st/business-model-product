#!/usr/bin/env python3
"""
반스(Vans) 공식몰(www.vans.co.kr / VF Korea 자체몰) 전수(全數) 추출 (stdlib only).

extract_vans.py 의 어댑터 로직을 그대로 쓰되 표본 캡(TARGET=120)·페이지 상한(MAX_PAGES=5)을
제거하고 전 카테고리·전 페이지를 끝(빈 페이지)까지 크롤한다.

전수 근거(사전 정찰):
  4개 대분류 totalCount  shoes=599, clothing=322, accessories=229, kids=136
  대분류 union = 1205 unique. 프로모(sale/new/bestseller/authentic/collab) 추가분 ≈ +2.
  => 전 우주 ≈ 1207. 페이지 크기 25, 마지막 다음 페이지는 빈 응답.

재개 가능(중간 저장):
  outputs/_vans_styles.tsv    수집 styleCode 체크포인트 (style<TAB>category<TAB>gender)
  outputs/_vans_cats_done.txt  수집 완료 카테고리 경로
  outputs/_vans_rows.csv       상세 추출 행 (상품마다 append) -> 재개시 done skip
  outputs/extract_brand_vans.csv  최종 (style_code dedup, utf-8-sig) 덮어쓰기

상한: 수집 styleCode > 5000 이면 5000 에서 절단(notes 기록).
"""
import csv
import html as ihtml
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
OUT_CSV = os.path.join(OUT, "extract_brand_vans.csv")
STYLES_TSV = os.path.join(OUT, "_vans_styles.tsv")
CATS_DONE = os.path.join(OUT, "_vans_cats_done.txt")
PARTIAL = os.path.join(OUT, "_vans_rows.csv")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE = "https://www.vans.co.kr"

HEADER = ["source", "brand", "style_code", "name", "color", "price",
          "currency", "category", "gender", "sizes", "origin",
          "material", "mfg_date", "url"]

CAP = 5000

# (category path, category label or None=name-infer, gender) — 첫 발견 우선(specific 먼저)
ORDERED = [
    ("men/outer", "의류", "남성"),
    ("men/top-shirts", "의류", "남성"),
    ("men/pants", "의류", "남성"),
    ("men/fleece", "의류", "남성"),
    ("men/allclothe", "의류", "남성"),
    ("women/outer", "의류", "여성"),
    ("women/top-shirts", "의류", "여성"),
    ("women/onepiece-pants", "의류", "여성"),
    ("women/onepiece-skirt", "의류", "여성"),
    ("women/fleece", "의류", "여성"),
    ("women/allclothe", "의류", "여성"),
    ("kids/allshoes", "신발", "키즈"),
    ("kids/toddler", "신발", "키즈"),
    ("kids/boys-clothe", "의류", "키즈"),
    ("kids/kids-clothe", "의류", "키즈"),
    ("kids/allclothe", "의류", "키즈"),
    ("kids/hat", "모자", "키즈"),
    ("kids/socks", "양말", "키즈"),
    ("kids/all-etc", "기타", "키즈"),
    ("accessories/bags", "가방", "공용"),
    ("accessories/hat", "모자", "공용"),
    ("accessories/socks", "양말", "공용"),
    ("accessories/etc", "기타", "공용"),
    ("shoes", "신발", "공용"),
    ("clothing", "의류", "공용"),
    ("accessories", "액세서리", "공용"),
    ("kids/kids", None, "키즈"),
    ("kids", None, "키즈"),
    ("sale", None, "공용"),
    ("new", None, "공용"),
]


def fetch(url, timeout=30, retries=3):
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
            time.sleep(0.7 * (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def pipe(parts):
    seen, keep = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            keep.append(p)
    return "|".join(keep)


def list_styles(cat_path, page):
    st, h = fetch(f"{BASE}/category/{cat_path}?page={page}")
    out, seen = [], set()
    for m in re.finditer(
            r'data-product-id="\d+"[^>]*href="/PRODUCT/([A-Za-z0-9]+)"', h):
        s = m.group(1)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def infer_category(name):
    n = name or ""
    if re.search(r"양말|삭스|SOCK", n, re.I):
        return "양말"
    if re.search(r"캡|모자|비니|버킷|스냅백|햇|HAT|CAP|BEANIE", n, re.I):
        return "모자"
    if re.search(r"백팩|가방|토트|크로스|파우치|월렛|지갑|더플|새첼|BAG|TOTE|BACKPACK", n, re.I):
        return "가방"
    if re.search(r"티셔츠|맨투맨|후디|후드|팬츠|쇼츠|반바지|자켓|재킷|코트|니트|스웻|스웨트|"
                 r"셔츠|원피스|스커트|집업|플리스|점퍼|조거|레깅스|풀오버|베스트|TEE|HOOD|"
                 r"PANT|SHORT|JACKET|CREW", n, re.I):
        return "의류"
    if re.search(r"슬립온|올드스쿨|어센틱|스케이트|로우 ?프로|슈즈|스니커|뮬|로퍼|클로그|"
                 r"SK8|SLIP|OLD ?SKOOL|AUTHENTIC|ERA|HALF ?CAB", n, re.I):
        return "신발"
    return "기타"


def parse_detail(style, cat_label, gender):
    url = f"{BASE}/PRODUCT/{style}"
    st, h = fetch(url)
    rec = {k: "" for k in HEADER}
    rec["source"] = "vans"
    rec["brand"] = "반스"
    rec["style_code"] = style.upper()
    rec["url"] = url

    mn = re.search(r'data-name="([^"]*)"', h)
    rec["name"] = ihtml.unescape(re.sub(r"\s+", " ", mn.group(1)).strip()) if mn else ""

    mc = re.search(r'class="variation-color selectable selected"[^>]*data-color="([^"]+)"', h)
    if not mc:
        mc = re.search(r'data-color="([^"]+)"[^>]*class="variation-color selectable selected"', h)
    color = mc.group(1).strip() if mc else ""

    mp = re.search(r'data-price="([0-9]+)"', h)
    rec["price"] = mp.group(1) if mp else ""
    rec["currency"] = "KRW" if rec["price"] else ""

    sizes = []
    for m in re.finditer(r'<input\b[^>]*\bdata-attributename="[A-Z_]*SIZE"[^>]*>', h):
        mv = re.search(r'data-value="([^"]+)"', m.group(0))
        if mv:
            sizes.append(mv.group(1).strip())
    rec["sizes"] = pipe(sizes)

    g = {}
    for m in re.finditer(
            r'info-name="[^"]+"[^>]*>([^<]+)</[^>]+>\s*'
            r'<[^>]*class="[^"]*tag-value[^"]*"[^>]*>(.*?)</', h, re.S):
        key = ihtml.unescape(m.group(1).strip())
        val = ihtml.unescape(re.sub(r"\s+", " ",
                             re.sub(r"<[^>]+>", "", m.group(2))).strip())
        if key and key not in g:
            g[key] = val
    for k, v in g.items():
        if "소재" in k and not rec["material"]:
            rec["material"] = v
        elif "제조국" in k and not rec["origin"]:
            rec["origin"] = v
        elif ("제조연월" in k or "제조년월" in k) and not rec["mfg_date"]:
            rec["mfg_date"] = v
    if not color:
        color = g.get("색상", "")
    rec["color"] = color

    rec["category"] = cat_label or infer_category(rec["name"])
    if gender == "공용" and re.search(r"키즈|주니어|유아|아동|토들러|TODDLER|KIDS", rec["name"], re.I):
        gender = "키즈"
    rec["gender"] = gender
    return rec


def collect():
    """전 카테고리 전 페이지 수집 -> STYLES_TSV (재개 가능). 첫 발견 우선."""
    order = {}      # style -> (idx, category, gender)
    if os.path.exists(STYLES_TSV):
        with open(STYLES_TSV, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3 and parts[0] not in order:
                    order[parts[0]] = (len(order), parts[1], parts[2])
    done_cats = set()
    if os.path.exists(CATS_DONE):
        with open(CATS_DONE, encoding="utf-8") as f:
            done_cats = {l.strip() for l in f if l.strip()}

    sf = open(STYLES_TSV, "a", encoding="utf-8")
    cf = open(CATS_DONE, "a", encoding="utf-8")
    for cat_path, label, gender in ORDERED:
        if cat_path in done_cats:
            continue
        page = 1
        cat_added = 0
        while True:
            try:
                styles = list_styles(cat_path, page)
            except Exception as e:  # noqa: BLE001
                print(f"[list] {cat_path} p{page} 실패: {e}", file=sys.stderr)
                styles = []
            if not styles:
                break
            for s in styles:
                if s not in order:
                    order[s] = (len(order), label or "", gender)
                    sf.write(f"{s}\t{label or ''}\t{gender}\n")
                    cat_added += 1
            sf.flush()
            page += 1
            time.sleep(0.15)
        cf.write(cat_path + "\n")
        cf.flush()
        print(f"[list] {cat_path}: +{cat_added} new (pages={page-1}) 누적 {len(order)}",
              file=sys.stderr)
    sf.close()
    cf.close()
    # 순서 보존
    items = sorted(order.items(), key=lambda kv: kv[1][0])
    return [(s, meta[1], meta[2]) for s, meta in items]


def load_done():
    done = set()
    if os.path.exists(PARTIAL):
        with open(PARTIAL, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                sc = (row.get("style_code") or "").strip().upper()
                if sc:
                    done.add(sc)
    return done


def main():
    os.makedirs(OUT, exist_ok=True)
    collected = collect()
    capped = False
    if len(collected) > CAP:
        collected = collected[:CAP]
        capped = True
    print(f"수집 styleCode {len(collected)}개 (cap_hit={capped})", file=sys.stderr)

    done = load_done()
    new_partial = not os.path.exists(PARTIAL)
    pf = open(PARTIAL, "a", encoding="utf-8-sig", newline="")
    w = csv.DictWriter(pf, fieldnames=HEADER)
    if new_partial:
        w.writeheader()
        pf.flush()

    fail = 0
    todo = [t for t in collected if t[0].upper() not in done]
    print(f"상세 대상 {len(todo)}개 (이미 {len(done)}개 완료)", file=sys.stderr)
    for n, (style, label, gender) in enumerate(todo, 1):
        try:
            rec = parse_detail(style, label, gender)
            w.writerow(rec)
            pf.flush()
            done.add(rec["style_code"])
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[detail] {style} 실패: {e}", file=sys.stderr)
        if n % 50 == 0:
            print(f"[detail] {n}/{len(todo)} (누적완료 {len(done)}) ...", file=sys.stderr)
        time.sleep(0.18)
    pf.close()

    # 최종 dedup -> OUT_CSV (수집 순서)
    rows_by_sc = {}
    with open(PARTIAL, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            sc = (row.get("style_code") or "").strip().upper()
            if sc and sc not in rows_by_sc:
                rows_by_sc[sc] = row
    ordered_sc = [s.upper() for s, _, _ in collected]
    final = [rows_by_sc[sc] for sc in ordered_sc if sc in rows_by_sc]
    # collected 에 없는데 partial 에 있는 잔여(있다면) 뒤에
    extra = [r for sc, r in rows_by_sc.items() if sc not in set(ordered_sc)]
    final += extra

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        wf = csv.DictWriter(f, fieldnames=HEADER)
        wf.writeheader()
        for r in final:
            wf.writerow({k: r.get(k, "") for k in HEADER})

    filled = {k: sum(1 for r in final if str(r.get(k, "")).strip()) for k in HEADER}
    print(f"\n=== 완료 === 최종 행수 {len(final)} (수집 {len(collected)}, 실패 {fail}, cap_hit={capped})")
    print("채워진 컬럼:", {k: v for k, v in filled.items() if v})
    print("경로:", OUT_CSV)


if __name__ == "__main__":
    main()
