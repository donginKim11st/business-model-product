#!/usr/bin/env python3
"""Phase 2 (병렬): 마스터 product_no 상세 JSON-LD 파싱 → _odp_rows.jsonl 채움(재개 가능).
스레드풀로 가속. 최종 CSV 쓰기는 _odp_detail.py 가 담당(이 스크립트는 jsonl 만 채움)."""
import json, os, re, ssl, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
BASE = "https://outdoorproducts.co.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CKPT = os.path.join(OUT, "_odp_list.json")
ROWS_JL = os.path.join(OUT, "_odp_rows.jsonl")
LOG = os.path.join(OUT, "_odp_detail.log")
WOMEN_CATS = {902, 905, 907, 908, 909, 910, 1017}
WORKERS = 12

_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
CAT_RULES = [
    ("버뮤다","숏츠"),("숏츠","숏츠"),("쇼츠","숏츠"),("반바지","숏츠"),
    ("조거","팬츠"),("팬츠","팬츠"),("바지","팬츠"),("슬랙스","팬츠"),("레깅스","레깅스"),
    ("후드집업","집업"),("집업","집업"),("후디","후드"),("후드","후드"),
    ("맨투맨","맨투맨"),("스웨트셔츠","맨투맨"),("스웻","맨투맨"),
    ("바람막이","자켓"),("아노락","자켓"),("재킷","자켓"),("자켓","자켓"),
    ("점퍼","자켓"),("코치","자켓"),("윈드","자켓"),("베스트","베스트"),("조끼","베스트"),
    ("니트","니트"),("카디건","카디건"),("셋업","셋업"),("SET","셋업"),
    ("슬리브리스","슬리브리스"),("민소매","슬리브리스"),("나시","슬리브리스"),
    ("티셔츠","티셔츠"),("반팔티","티셔츠"),("긴팔티","티셔츠"),("셔츠","셔츠"),
    ("원피스","원피스"),("스커트","스커트"),("치마","스커트"),
    ("바이져","모자"),("바이저","모자"),("버켓햇","모자"),("버킷햇","모자"),
    ("버켓","모자"),("버킷","모자"),("캡","모자"),("비니","모자"),("햇","모자"),("모자","모자"),
    ("백팩","가방"),("크로스백","가방"),("슬링백","가방"),("메신저","가방"),("토트백","가방"),
    ("토트","가방"),("더플","가방"),("힙색","가방"),("웨이스트","가방"),("파우치","가방"),
    ("가방","가방"),("백","가방"),("양말","양말"),("삭스","양말"),("장갑","장갑"),
    ("머플러","머플러"),("벨트","벨트"),("타월","타월"),("수건","타월"),
    ("샌들","신발"),("슬리퍼","신발"),("슈즈","신발"),("운동화","신발"),
    ("레인부츠","신발"),("부츠","신발"),("티","티셔츠"),
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
            last = e; time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"GET fail {u}: {last}")


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
        return None
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
        elif var and var not in sizes:
            sizes.append(var)
    return {
        "source": "outdoorproducts", "brand": brand, "style_code": style,
        "name": name, "color": paren or "|".join(vcolors),
        "price": prices[0] if prices else "", "currency": currency,
        "category": categorize(name), "gender": gender, "sizes": "|".join(sizes),
        "origin": "", "material": "", "mfg_date": "", "url": url, "_pid": pid,
    }


def main():
    master = json.load(open(CKPT, encoding="utf-8"))
    done = set()
    if os.path.exists(ROWS_JL):
        for line in open(ROWS_JL, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["_pid"])
            except Exception:
                pass
    todo = [p for p in master if p not in done]
    print(f"master={len(master)} done={len(done)} todo={len(todo)}", flush=True)

    lock = threading.Lock()
    fout = open(ROWS_JL, "a", encoding="utf-8")
    counts = {"ok": 0, "fail": 0, "nold": 0}

    def work(pid):
        info = master[pid]
        cats = set(info["cats"])
        gender = "WOMEN" if (cats & WOMEN_CATS) and (901 not in cats) else "UNISEX"
        try:
            rec = parse_product(pid, info["slug"], gender)
            return pid, rec, None
        except Exception as e:
            return pid, None, str(e)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, pid) for pid in todo]
        n = 0
        for fut in as_completed(futs):
            pid, rec, err = fut.result()
            n += 1
            with lock:
                if rec:
                    fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fout.flush()
                    counts["ok"] += 1
                elif err:
                    counts["fail"] += 1
                else:
                    counts["nold"] += 1
            if n % 100 == 0:
                line = time.strftime("%H:%M:%S ") + f"fast …{n}/{len(todo)} {counts}"
                print(line, flush=True)
                with open(LOG, "a", encoding="utf-8") as lf:
                    lf.write(line + "\n")
    fout.close()
    line = time.strftime("%H:%M:%S ") + f"FAST_FETCH_DONE {counts} jsonl_total={len(done)+counts['ok']}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as lf:
        lf.write(line + "\n")


if __name__ == "__main__":
    main()
