#!/usr/bin/env python3
"""Phase 1: 아웃도어프로덕츠 전 카테고리·전 페이지 리스트 크롤 → 마스터 product_no 집합.
체크포인트: outputs/_odp_list.json  (product_no -> {slug, cats:[...]})
"""
import json, os, re, ssl, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
BASE = "https://outdoorproducts.co.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CKPT = os.path.join(OUT, "_odp_list.json")
LOG = os.path.join(OUT, "_odp_list.log")

_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE
PROD = re.compile(r'href="/product/([^"]*?)/(\d+)/category/(\d+)/display/\d+/"')

# 55 cats discovered via BFS (룩북/아웃도어크루 = 콘텐츠지만 무해; 상품 0이면 즉시 종료)
ALL_CATS = [9,13,66,69,92,93,94,95,96,97,139,243,263,574,892,901,902,905,907,908,
            909,910,919,922,923,924,925,926,927,928,930,934,935,936,937,939,940,
            944,945,946,947,957,958,959,996,997,998,999,1017,1230,1231,1232,1233,1234,1236]


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


def main():
    master = {}
    if os.path.exists(CKPT):
        master = json.load(open(CKPT, encoding="utf-8"))
        log(f"resume: {len(master)} product_no loaded")
    for cate in ALL_CATS:
        page = 1
        cat_seen = set()
        while True:
            html = get(f"{BASE}/product/list.html?cate_no={cate}&page={page}")
            raw = PROD.findall(html)
            if not raw:                       # 빈 페이지 = 마지막
                break
            page_pids = {pid for _, pid, _ in raw}
            new_in_cat = page_pids - cat_seen
            if not new_in_cat:                # 이 카테고리 기준 신규 0 → 페이지 반복/끝
                break
            for slug, pid, cat in raw:
                cat_seen.add(pid)
                rec = master.get(pid)
                if rec is None:
                    master[pid] = {"slug": slug, "cats": [cate]}
                elif cate not in rec["cats"]:
                    rec["cats"].append(cate)
            log(f"cate {cate} p{page}: anchors={len(raw)} uniq_in_cat={len(cat_seen)} master={len(master)}")
            page += 1
            time.sleep(0.05)
            if page > 200:                    # 안전 상한
                log(f"  cate {cate} hit page cap 200"); break
        json.dump(master, open(CKPT, "w", encoding="utf-8"), ensure_ascii=False)
    log(f"DONE list crawl: {len(master)} unique product_no")
    json.dump(master, open(CKPT, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
