#!/usr/bin/env python3
"""아디다스 코리아(Akamai BMP) 서버측 자동 추출 — patchright(CDP패치 Playwright) headed.
바닐라 Playwright/curl_cffi는 Akamai 행동챌린지에 막히지만, patchright headed +
persistent context는 통과(검증됨). 서버/cron에선 headed를 xvfb 가상디스플레이로 돌리면 됨.
PDP의 ld+json(Product)+__NEXT_DATA__+DOM에서 정형필드+고시 추출.
출력: outputs/extract_brand_adidas.csv (공통 14컬럼)"""
import csv
import html as H
import json
import os
import re
import time
import urllib.parse
import unblocker  # 언블로커 API(키 있으면) — 없으면 patchright 폴백

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
BASE = "https://www.adidas.co.kr"
COLS = ["source", "brand", "style_code", "name", "color", "price", "currency",
        "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]


def content(pg):
    try:
        return pg.content()
    except Exception:
        return ""


def wait_real(pg, needle_re, tries=25):
    for _ in range(tries):
        pg.wait_for_timeout(1000)
        b = content(pg)
        if needle_re.search(b):
            return b
    return content(pg)


def parse_pdp(body, url):
    prod = None
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', body, re.S):
        try:
            j = json.loads(m.group(1).strip())
        except Exception:
            continue
        for n in (j if isinstance(j, list) else [j]):
            if isinstance(n, dict) and n.get("@type") == "Product":
                prod = n
    name = (prod or {}).get("name", "")
    sku = (prod or {}).get("sku") or (prod or {}).get("mpn") or ""
    color = (prod or {}).get("color", "")
    if not sku:
        m = re.search(r'/([A-Z]{2}\d{4})\.html', url)
        sku = m.group(1) if m else ""
    # 가격: ld+json offers 없으면 본문 '원' 최저값
    price = ""
    offers = (prod or {}).get("offers")
    if isinstance(offers, dict):
        price = offers.get("price", "") or offers.get("lowPrice", "")
    if not price:
        cands = [int(x.replace(",", "")) for x in re.findall(r'(\d{2,3},\d{3})\s*원', body)]
        if cands:
            price = str(min(cands))
    # 사이즈: __NEXT_DATA__ 또는 size 버튼
    sizes = sorted(set(re.findall(r'data-testid="[^"]*size[^"]*"[^>]*>\s*([0-9]{2,3}(?:\.\d)?|[XSML]{1,3})\s*<', body)))
    # 고시(상품정보제공고시): 라벨 다음의 값 셀. 쓰레기값(정보/제공/공백/구두점)은 버림.
    JUNK = {"정보", "제공", "고시", "상품정보제공고시", ",", ":", "-", "정보제공고시"}
    def near(label):
        for pat in (label + r'\s*[:：]?\s*</[^>]+>\s*<[^>]+>([^<]{1,40})</',
                    label + r'</[^>]+>\s*<[^>]+>\s*([^<]{1,40})\s*<',
                    label + r'["\s:：]+([가-힣A-Za-z0-9 ,/().%·~+]{2,30})'):
            m = re.search(pat, body)
            if m:
                v = H.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip(" ,:·-")
                if v and v not in JUNK and not re.fullmatch(r'[,\s:·\-]+', v):
                    return v
        return ""
    return {
        "name": name, "sku": sku, "color": color,
        "price": str(price).replace(".0", ""),
        "sizes": sizes, "origin": near("제조국") or near("원산지"),
        "material": near("제품소재") or near("소재"), "mfg_date": near("제조연월"),
        "url": url,
    }


CATS = ["신발", "의류", "액세서리", "men/신발", "women/신발", "kids"]
CAP = 90
PATH = os.path.join(OUT, "extract_brand_adidas.csv")


def _save(rows):
    with open(PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows.values())


def _row(d, u):
    gender = "WOMEN" if re.search(r'-w/|women|여성', u.lower()) else ("MEN" if "men" in u.lower() else "")
    return {"source": "adidas", "brand": "아디다스", "style_code": d["sku"], "name": d["name"],
            "color": d["color"], "price": d["price"], "currency": "KRW", "category": "",
            "gender": gender, "sizes": "|".join(d["sizes"]), "origin": d["origin"],
            "material": d["material"], "mfg_date": d["mfg_date"], "url": u}


def main_unblocker():
    """언블로커 API 경유 — 순수 HTTP, cron/서버 친화적(브라우저 불필요). 키 설정 시 사용."""
    print(f"[adidas] 언블로커 모드: {unblocker.PROVIDER}")
    rows = {}
    if os.path.exists(PATH):
        for r in csv.DictReader(open(PATH, encoding="utf-8-sig")):
            rows[r["url"]] = r
    links, seen = [], set()
    for cat in CATS:
        try:
            b = unblocker.fetch(f"{BASE}/{urllib.parse.quote(cat)}", country="kr", render=True)
        except Exception as e:
            print(f"  카테고리 {cat} 실패: {str(e)[:50]}")
            continue
        for u in re.findall(r'/[^"\' >]+/[A-Z]{2}\d{4}\.html', b):
            full = ("https:" + u) if u.startswith("//") else (BASE + u)
            if full not in seen:
                seen.add(full)
                links.append(full)
        print(f"  {cat} → 누적 {len(links)}")
    links = [u for u in links if u not in rows][:CAP]
    print(f"처리 대상 {len(links)} (기존 {len(rows)})")
    for i, u in enumerate(links):
        try:
            b = unblocker.fetch(u, country="kr", render=True)
            d = parse_pdp(b, u)
            if d["name"]:
                rows[u] = _row(d, u)
        except Exception as e:
            print(f"  PDP 실패 …{u[-22:]}: {str(e)[:40]}")
        if (i + 1) % 10 == 0:
            _save(rows)
            print(f"  …{i+1}/{len(links)} (총 {len(rows)}행)")
    _save(rows)
    print(f"아디다스 {len(rows)}행 (언블로커) → {PATH}")


def main():
    from patchright.sync_api import sync_playwright
    # 재개: 기존 CSV 로드(이미 채운 style_code 스킵)
    rows = {}
    if os.path.exists(PATH):
        for r in csv.DictReader(open(PATH, encoding="utf-8-sig")):
            rows[r["url"]] = r
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pr_adidas", headless=False, locale="ko-KR",
            viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        links, seen = [], set()
        for cat in CATS:
            try:
                pg.goto(f"{BASE}/{urllib.parse.quote(cat)}", timeout=50000, wait_until="domcontentloaded")
                pg.wait_for_timeout(2000)
                # 무한스크롤로 더 로드
                for _ in range(3):
                    pg.mouse.wheel(0, 4000)
                    pg.wait_for_timeout(1500)
            except Exception as e:
                print(f"  카테고리 {cat} 실패: {str(e)[:40]}")
                continue
            b = content(pg)
            for u in re.findall(r'/[^"\' >]+/[A-Z]{2}\d{4}\.html', b):
                full = ("https:" + u) if u.startswith("//") else (BASE + u)
                if full not in seen:
                    seen.add(full)
                    links.append(full)
            print(f"  {cat} → 누적 {len(links)}")
        links = [u for u in links if u not in rows][:CAP]
        print(f"처리 대상 {len(links)} (기존 {len(rows)})")
        for i, u in enumerate(links):
            ok = False
            for attempt in range(2):  # 챌린지 미통과 시 1회 재시도
                try:
                    pg.goto(u, timeout=50000, wait_until="domcontentloaded")
                    b = wait_real(pg, re.compile(r'"@type"\s*:\s*"Product"'), tries=18)
                    d = parse_pdp(b, u)
                    if d["name"]:
                        gender = "WOMEN" if re.search(r'-w/|women|여성', u.lower()) else ("MEN" if "men" in u.lower() else "")
                        rows[u] = {"source": "adidas", "brand": "아디다스",
                                   "style_code": d["sku"], "name": d["name"], "color": d["color"],
                                   "price": d["price"], "currency": "KRW", "category": "",
                                   "gender": gender, "sizes": "|".join(d["sizes"]),
                                   "origin": d["origin"], "material": d["material"],
                                   "mfg_date": d["mfg_date"], "url": u}
                        ok = True
                        break
                except Exception as e:
                    print(f"  PDP 시도{attempt+1} 실패 …{u[-22:]}: {str(e)[:35]}")
                pg.wait_for_timeout(2500)  # 재시도 전 대기
            pg.wait_for_timeout(1200)  # 레이트리밋 회피 페이싱
            if (i + 1) % 8 == 0:
                _save(rows)
                print(f"  …{i+1}/{len(links)} (총 {len(rows)}행)")
        ctx.close()
    _save(rows)
    filled = sum(1 for r in rows.values() if r["price"])
    print(f"아디다스 {len(rows)}행 (가격있음 {filled}) → {PATH}")


if __name__ == "__main__":
    # 언블로커 키 있으면 그걸로(빠르고 cron 가능), 없으면 patchright headed 폴백
    if unblocker.available():
        main_unblocker()
    else:
        print("[adidas] 언블로커 미설정 → patchright headed 폴백")
        main()
