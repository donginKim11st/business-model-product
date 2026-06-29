#!/usr/bin/env python3
"""Phase 2: 마스터 product_no 집합 → 상세 JSON-LD 파싱 → 전수 CSV.
- 재개 가능: 파싱 결과를 outputs/_odp_rows.jsonl 에 append, 재실행 시 done pid skip.
- 기존 CSV/gosi 의 origin/material/mfg_date 를 style_code 로 join(보존, 갱신 아님 wipe 아님).
- 출력: outputs/extract_brand_outdoorproducts.csv (지정 헤더, utf-8-sig), product_no(url) 단위 dedup.
- 상한 5000 (초과 시 멈춤, notes 기록).
"""
import csv, json, os, re, ssl, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
BASE = "https://outdoorproducts.co.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
CKPT = os.path.join(OUT, "_odp_list.json")
ROWS_JL = os.path.join(OUT, "_odp_rows.jsonl")
LOG = os.path.join(OUT, "_odp_detail.log")
CSV_OUT = os.path.join(OUT, "extract_brand_outdoorproducts.csv")
CAP = 5000

# WOMEN(902) 서브트리(이름 기반 추정): 902 자체 + 여성 전용 타입 카테고리
WOMEN_CATS = {902, 905, 907, 908, 909, 910, 1017}

_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)

CAT_RULES = [
    ("버뮤다", "숏츠"), ("숏츠", "숏츠"), ("쇼츠", "숏츠"), ("반바지", "숏츠"),
    ("조거", "팬츠"), ("팬츠", "팬츠"), ("바지", "팬츠"), ("슬랙스", "팬츠"),
    ("레깅스", "레깅스"),
    ("후드집업", "집업"), ("집업", "집업"), ("후디", "후드"), ("후드", "후드"),
    ("맨투맨", "맨투맨"), ("스웨트셔츠", "맨투맨"), ("스웻", "맨투맨"),
    ("바람막이", "자켓"), ("아노락", "자켓"), ("재킷", "자켓"), ("자켓", "자켓"),
    ("점퍼", "자켓"), ("코치", "자켓"), ("윈드", "자켓"),
    ("베스트", "베스트"), ("조끼", "베스트"),
    ("니트", "니트"), ("카디건", "카디건"),
    ("셋업", "셋업"), ("SET", "셋업"),
    ("슬리브리스", "슬리브리스"), ("민소매", "슬리브리스"), ("나시", "슬리브리스"),
    ("티셔츠", "티셔츠"), ("반팔티", "티셔츠"), ("긴팔티", "티셔츠"),
    ("셔츠", "셔츠"),
    ("원피스", "원피스"), ("스커트", "스커트"), ("치마", "스커트"),
    ("바이져", "모자"), ("바이저", "모자"), ("버켓햇", "모자"), ("버킷햇", "모자"),
    ("버켓", "모자"), ("버킷", "모자"), ("캡", "모자"), ("비니", "모자"),
    ("햇", "모자"), ("모자", "모자"),
    ("백팩", "가방"), ("크로스백", "가방"), ("슬링백", "가방"), ("메신저", "가방"),
    ("토트백", "가방"), ("토트", "가방"), ("더플", "가방"), ("힙색", "가방"),
    ("웨이스트", "가방"), ("파우치", "가방"), ("가방", "가방"), ("백", "가방"),
    ("양말", "양말"), ("삭스", "양말"), ("장갑", "장갑"), ("머플러", "머플러"),
    ("벨트", "벨트"), ("타월", "타월"), ("수건", "타월"),
    ("샌들", "신발"), ("슬리퍼", "신발"), ("슈즈", "신발"), ("운동화", "신발"),
    ("레인부츠", "신발"), ("부츠", "신발"),
    # 보강(앞 규칙 미매칭 시): 소매/풀오버/아우터/가방/잡화 추가 어휘
    ("하프 슬리브", "티셔츠"), ("하프슬리브", "티셔츠"), ("롱슬리브", "티셔츠"),
    ("롱 슬리브", "티셔츠"), ("숏슬리브", "티셔츠"), ("숏 슬리브", "티셔츠"),
    ("슬리브", "티셔츠"), ("져지", "티셔츠"), ("크루넥", "맨투맨"), ("풀오버", "맨투맨"),
    ("원드브레이커", "자켓"), ("윈드브레이커", "자켓"), ("브레이커", "자켓"),
    ("푸퍼", "자켓"), ("파카", "자켓"), ("패딩", "자켓"), ("다운", "자켓"),
    ("메신져", "가방"), ("메신저", "가방"), ("숄더", "가방"), ("보스턴", "가방"),
    ("데이팩", "가방"), ("포켓", "가방"), ("월렛", "가방"), ("지갑", "가방"),
    ("넥워머", "용품"), ("워머", "용품"), ("헤어밴드", "용품"), ("스카프", "용품"),
    ("키링", "용품"), ("우산", "용품"),
    ("티", "티셔츠"),
]


def get(u, retries=3, timeout=25):
    u = urllib.parse.quote(u, safe=":/?=&%#+,")
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html,application/xhtml+xml"})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e; time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"GET fail {u}: {last}")


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def first_product_ld(html):
    for b in _LD.findall(html):
        try:
            o = json.loads(b.strip())
        except Exception:
            continue
        for it in (o if isinstance(o, list) else [o]):
            if isinstance(it, dict) and it.get("@type") in ("Product", "ProductGroup"):
                return it
    return None


def categorize(name):
    for kw, cat in CAT_RULES:
        if kw in name:
            return cat
    return ""


def parse_product(pid, slug, gender):
    url = f"{BASE}/product/{slug}/{pid}/"
    html = get(url)
    ld = first_product_ld(html)
    if not ld:
        return None, "no-jsonld"
    name = (ld.get("name") or "").strip()
    style = (ld.get("description") or "").strip()
    brand = ((ld.get("brand") or {}).get("name") if isinstance(ld.get("brand"), dict)
             else ld.get("brand")) or "아웃도어프로덕츠"
    offers = ld.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    pm = re.search(r'\(([^)]*)\)\s*$', name)
    paren = pm.group(1).strip() if pm else ""
    sizes, vcolors, prices, currency = [], [], [], "KRW"
    for of in offers:
        lab = (of.get("name") or "")
        var = lab[len(name):].strip() if lab.startswith(name) else lab.strip()
        if of.get("price") not in (None, ""):
            prices.append(of["price"])
        if of.get("priceCurrency"):
            currency = of["priceCurrency"]
        if "-" in var:
            col, size = var.rsplit("-", 1)
            size = size.strip()
            if size and size not in sizes:
                sizes.append(size)
            col = col.strip()
            if col and col not in vcolors:
                vcolors.append(col)
        elif var:
            if var not in sizes:
                sizes.append(var)
    color = paren or ("|".join(vcolors))
    price = prices[0] if prices else ""
    rec = {
        "source": "outdoorproducts", "brand": brand, "style_code": style,
        "name": name, "color": color, "price": price, "currency": currency,
        "category": categorize(name), "gender": gender, "sizes": "|".join(sizes),
        "origin": "", "material": "", "mfg_date": "", "url": url,
    }
    return rec, "ok"


def load_enrich():
    """기존 CSV + gosi CSV 의 (style_code -> origin/material/mfg_date) 비어있지 않은 값."""
    enr = {}
    for path, keys in [(CSV_OUT, ("origin", "material", "mfg_date")),
                       (os.path.join(OUT, "gosi_outdoorproducts.csv"),
                        ("origin", "material", "mfg_date"))]:
        if not os.path.exists(path):
            continue
        try:
            for r in csv.DictReader(open(path, encoding="utf-8-sig")):
                sc = (r.get("style_code") or "").strip()
                if not sc:
                    continue
                d = enr.setdefault(sc, {})
                for k in keys:
                    v = (r.get(k) or "").strip()
                    if v and not d.get(k):
                        d[k] = v
        except Exception as e:
            log(f"enrich load skip {path}: {e}")
    return enr


def main():
    master = json.load(open(CKPT, encoding="utf-8"))
    log(f"master product_no = {len(master)}")
    # resume: 이미 파싱한 pid
    done = {}
    if os.path.exists(ROWS_JL):
        for line in open(ROWS_JL, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[rec["_pid"]] = rec
            except Exception:
                pass
        log(f"resume: {len(done)} pid already parsed")

    pids = list(master.keys())
    cap_hit = False
    fout = open(ROWS_JL, "a", encoding="utf-8")
    fails = []
    for i, pid in enumerate(pids):
        if pid in done:
            continue
        if len(done) >= CAP:
            cap_hit = True
            log(f"CAP {CAP} reached, stopping detail parse")
            break
        info = master[pid]
        cats = set(info["cats"])
        gender = "WOMEN" if (cats & WOMEN_CATS) and (901 not in cats) else "UNISEX"
        try:
            rec, status = parse_product(pid, info["slug"], gender)
            if not rec:
                fails.append((pid, status)); continue
            rec["_pid"] = pid
            done[pid] = rec
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()
        except Exception as e:
            fails.append((pid, str(e)))
        if (i + 1) % 50 == 0:
            log(f"  …{i+1}/{len(pids)} parsed={len(done)} fails={len(fails)}")
        time.sleep(0.06)
    fout.close()
    log(f"detail done: parsed={len(done)} fails={len(fails)} cap_hit={cap_hit}")
    if fails:
        log(f"  sample fails: {fails[:8]}")

    # enrich join + dedup by (style_code,color); empty style_code → fallback to pid.
    # first-seen wins; done 는 ALL_CATS(901 우선) 순서 → 정상 리스팅이 아웃렛보다 우선.
    enr = load_enrich()
    # 비상품 제외: 룩북(cate66, price='lookbook', style='SUMMER 26')·아웃도어크루(price='crew',
    #   style 공란, 상품카테고리에 교차등재됨). 실제 품절상품(유효 style + price 공란)은 보존.
    CONTENT_ONLY = {66, 9, 13}

    def isnum(p):
        try:
            int(str(p)); return True
        except Exception:
            return False
    rows, seen_keys = [], set()
    n_pid = 0
    n_content = 0
    for pid, rec in done.items():
        cats = set(master.get(pid, {}).get("cats", []))
        sc = (rec.get("style_code") or "").strip()
        price = rec.get("price")
        if (cats and cats <= CONTENT_ONLY) or (str(price) in ("lookbook", "crew")) \
                or (not sc and not isnum(price)):
            n_content += 1
            continue
        n_pid += 1
        col = (rec.get("color") or "").strip()
        # 모델코드(WO336UDHP103류)만 (style,color) dedup; 콜라보명/공란은 product_no 유지.
        is_model = bool(re.fullmatch(r"[A-Za-z0-9\-]+", sc)) and any(c.isdigit() for c in sc)
        key = (sc, col) if is_model else ("__pid__", pid)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        r = {k: rec.get(k, "") for k in HEADER}
        r["category"] = categorize(r["name"])   # 확장된 규칙으로 재분류
        e = enr.get(r["style_code"], {})
        for k in ("origin", "material", "mfg_date"):
            if not str(r.get(k, "")).strip() and e.get(k):
                r[k] = e[k]
        rows.append(r)
    with open(CSV_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    u_sc_color = len({(r["style_code"], r["color"]) for r in rows})
    u_sc = len({r["style_code"] for r in rows if r["style_code"]})
    nonnum = sum(1 for r in rows if not isnum(r.get("price")))
    log(f"WROTE {len(rows)} rows -> {CSV_OUT}")
    log(f"counts: product_no(real)={n_pid} content_excluded={n_content} "
        f"unique(style,color)={u_sc_color} unique_style={u_sc} nonnumeric_price={nonnum}")
    filled = {k: sum(1 for r in rows if str(r.get(k, '')).strip()) for k in
              ('origin', 'material', 'mfg_date', 'sizes', 'category', 'price')}
    log(f"filled: {filled}")
    print(f"FINAL_ROWS={len(rows)} PRODUCT_NO={n_pid} CONTENT_EXCL={n_content} "
          f"SC_COLOR={u_sc_color} SC={u_sc} NONNUM={nonnum}")


if __name__ == "__main__":
    main()
