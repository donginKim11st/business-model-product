#!/usr/bin/env python3
"""범용 가구몰 추출 엔진 — cafe24 / godomall / makeshop 공통.

  python3 extract_furniture_engine.py --slug jakomo             # 레지스트리 기반 전체 추출
  python3 extract_furniture_engine.py --slug jakomo --limit 30  # 상한
  python3 extract_furniture_engine.py --url https://… --platform godomall --slug x --name 엑스

브랜드별 전용 스크립트(extract_furniture_<slug>.py)가 없어도, 플랫폼만 알면
카테고리 자동발견 → 목록 순회 → 상세 파싱으로 표준 CSV를 만든다.
출력: outputs/extract_furniture_<slug>.csv (HEADER는 extract_furniture_base 기준)
"""
import argparse
import html as html_mod
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_furniture_base import HEADER, write_csv, parse_dimensions, parse_bed_size  # noqa: E402
import brand_profile  # noqa: E402

REGISTRY = os.path.join(HERE, "brands_furniture.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PAGE_CAP = 100
SLEEP = 0.2  # 레지스트리에 없는 slug(--url/--platform 직접 지정) 대비 폴백


def resolve_delay(slug):
    """A층 crawl_profile에서 이 브랜드의 요청 간 딜레이(초)를 읽는다.
    레지스트리 미등록 slug(--url/--platform 애드혹 실행)는 SLEEP 폴백."""
    try:
        return brand_profile.load_crawl_profile(slug)["delay_s"]
    except KeyError:
        return SLEEP

# 중복성 카테고리 (BEST/신상 등 — 상품이 정규 카테고리와 겹침)
DUP_CATEGORY_WORDS = ["best", "베스트", "신상", "new", "sale", "세일", "기획", "이벤트",
                      "전체", "개인결제", "브랜드 소개", "할인"]


def fetch(url, retries=2, size=0):
    enc = urllib.parse.quote(url, safe=":/?=&%#+,@")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(enc, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
                raw = r.read(size) if size else r.read()
            return raw.decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(0.4 * (attempt + 1))
    raise last


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def clean_name(title, brand_ko):
    """<title> → 상품명: 사이트명 접미사·프로모 태그 제거."""
    t = html_mod.unescape(title or "").strip()
    t = re.split(r"\s*[|:–-]\s*" + re.escape(brand_ko) + r"\s*$", t)[0]
    t = re.sub(r"\s*[|]\s*[^|]{0,20}$", "", t) if "|" in t else t
    t = re.sub(r"^\s*\[[^\]]{1,30}\]\s*", "", t)  # [프로모션] 접두 제거
    return t.strip()


def is_dup_category(name):
    low = (name or "").lower()
    return any(w in low for w in DUP_CATEGORY_WORDS)


def extract_gosi(body):
    """고시 테이블(th/td 또는 td/td)에서 소재/제조국/크기/KC 추출."""
    out = {"material": "", "origin": "", "safety_cert": "", "dims": ""}
    pairs = re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", body, re.DOTALL)
    pairs += re.findall(r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", body, re.DOTALL)
    for k_raw, v_raw in pairs:
        k, v = strip_tags(k_raw), strip_tags(v_raw)
        if not v or v in ("상세페이지 참조", "*상세페이지 참조", "-"):
            continue
        if not out["material"] and re.search(r"소재|재질|구성재료", k):
            out["material"] = v[:120]
        elif not out["origin"] and re.search(r"제조국|원산지", k):
            out["origin"] = v[:60]
        elif not out["safety_cert"] and re.search(r"KC|인증", k):
            if not v.startswith("<img") and "img" not in v[:10]:
                out["safety_cert"] = v[:120]
        elif not out["dims"] and re.search(r"크기|사이즈|치수", k):
            out["dims"] = v[:120]
    return out


def extract_options(body):
    """<select> 옵션에서 색상 후보 추출 ('|' join)."""
    colors = []
    for sel in re.findall(r"<select[^>]*>(.*?)</select>", body, re.DOTALL):
        opts = [strip_tags(o) for o in re.findall(r"<option[^>]*>(.*?)</option>", sel, re.DOTALL)]
        opts = [o for o in opts if o and not re.search(
            r"선택|옵션|택배|배송|추가|필수|=|-{2,}|\d+원|language|한국어|english"
            r"|구매\s*안|상세\s*페이지|^[A-Z0-9_-]{6,}$", o, re.IGNORECASE) and len(o) < 30]
        if opts and len(opts) <= 30:
            colors.extend(opts)
    seen, out = set(), []
    for c in colors:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return "|".join(out[:20])


def extract_price(body):
    """플랫폼 공통 가격 패턴."""
    pats = [
        r'set_goods_price[^0-9]*?([1-9][\d,]{3,})',            # godomall
        r'"basePrice"\s*:\s*"?([1-9][\d,]{3,})',               # cafe24
        r'product_price\s*=\s*["\']?([1-9][\d,]{3,})',         # makeshop/cafe24
        r'<meta[^>]+property="product:price:amount"[^>]+content="([\d.]+)"',
        r'"price"\s*:\s*"?([1-9][\d,.]{3,})',                  # JSON-LD
        r'판매가[^0-9]{0,60}([1-9][\d,]{4,})\s*원',
    ]
    for p in pats:
        m = re.search(p, body)
        if m:
            v = m.group(1).replace(",", "").split(".")[0]
            if v.isdigit() and 100 <= int(v) <= 500000000:
                return v
    return ""


def make_row(slug, brand_ko, model_no, name, url, body, category):
    gosi = extract_gosi(body)
    w, d, h = parse_dimensions(gosi["dims"] or "")
    return {
        "source": slug, "brand": brand_ko, "model_no": model_no,
        "name": name, "color": extract_options(body),
        "price": extract_price(body), "currency": "KRW",
        "category": category, "material": gosi["material"],
        "width_cm": w, "depth_cm": d, "height_cm": h,
        "bed_size": parse_bed_size(name),
        "assembly": "", "installation_service": "",
        "origin": gosi["origin"], "safety_cert": gosi["safety_cert"],
        "url": url,
    }


# ── 플랫폼별 구현 ──────────────────────────────────────────────────────────────

def discover_categories(body, pattern, dedup_words=True):
    """홈 HTML에서 (코드, 카테고리명) 목록."""
    menu = re.findall(pattern, body)
    seen = {}
    for code, name in menu:
        n = strip_tags(name)
        if code not in seen and n and len(n) < 30:
            if dedup_words and is_dup_category(n):
                continue
            seen[code] = n
    return seen


def run_godomall(slug, brand_ko, base):
    body = fetch(base)
    cats = discover_categories(body, r'<a[^>]+cateCd=(\w+)[^>]*>(.*?)</a>')
    # 최상위(3자리)만 — 하위는 중복
    cats = {c: n for c, n in cats.items() if len(c) == 3}
    print(f"[{slug}] 카테고리 {len(cats)}개: {list(cats.values())[:10]}")
    goods = {}  # goodsNo → category
    for code, cname in cats.items():
        prev = set()
        for page in range(1, PAGE_CAP + 1):
            try:
                lb = fetch(f"{base}/goods/goods_list.php?cateCd={code}&page={page}")
            except Exception:
                break
            gnos = set(re.findall(r"goodsNo=(\d+)", lb))
            new = gnos - set(goods) - prev
            if not gnos or gnos == prev or not new:
                break
            prev = gnos
            for g in gnos:
                goods.setdefault(g, cname)
            time.sleep(resolve_delay(slug))
    print(f"[{slug}] 상품 {len(goods)}개 발견")
    return [(g, f"{base}/goods/goods_view.php?goodsNo={g}", c) for g, c in goods.items()]


def _cafe24_pnos(list_html):
    """cafe24 목록 HTML → product_no 집합 (쿼리형 + SEO URL형 모두)."""
    pnos = set(re.findall(r"product_no=(\d+)", list_html))
    # SEO URL: /product/상품명/1234/ 또는 /product/상품명/1234/category/…
    pnos |= set(re.findall(r'href="[^"]*/product/[^"/]+/(\d+)/', list_html))
    return pnos


def run_cafe24(slug, brand_ko, base):
    body = fetch(base)
    cats = discover_categories(body, r'<a[^>]+cate_no=(\d+)[^>]*>(.*?)</a>')
    # SEO 카테고리 URL: /category/이름/123/
    for name, code in re.findall(r'href="[^"]*/category/([^/"]+)/(\d+)/"', body):
        n = urllib.parse.unquote(name)
        if code not in cats and not is_dup_category(n) and len(n) < 30:
            cats[code] = n
    print(f"[{slug}] 카테고리 {len(cats)}개: {list(cats.values())[:10]}")
    goods = {}
    for code, cname in cats.items():
        prev = set()
        for page in range(1, PAGE_CAP + 1):
            try:
                lb = fetch(f"{base}/product/list.html?cate_no={code}&page={page}")
            except Exception:
                break
            pnos = _cafe24_pnos(lb)
            new = pnos - set(goods) - prev
            if not pnos or pnos == prev or not new:
                break
            prev = pnos
            for p in pnos:
                goods.setdefault(p, cname)
            time.sleep(resolve_delay(slug))
    print(f"[{slug}] 상품 {len(goods)}개 발견")
    return [(p, f"{base}/product/detail.html?product_no={p}", c) for p, c in goods.items()]


def run_makeshop(slug, brand_ko, base):
    body = fetch(base)
    cats = discover_categories(body, r'<a[^>]+xcode=(\d+)[^>]*>(.*?)</a>')
    print(f"[{slug}] 카테고리 {len(cats)}개: {list(cats.values())[:10]}")
    goods = {}
    for code, cname in cats.items():
        prev = set()
        for page in range(1, PAGE_CAP + 1):
            try:
                lb = fetch(f"{base}/shop/shopbrand.html?xcode={code}&page={page}")
            except Exception:
                break
            buids = set(re.findall(r"branduid=(\d+)", lb))
            new = buids - set(goods) - prev
            if not buids or buids == prev or not new:
                break
            prev = buids
            for b in buids:
                goods.setdefault(b, cname)
            time.sleep(resolve_delay(slug))
    print(f"[{slug}] 상품 {len(goods)}개 발견")
    return [(b, f"{base}/shop/shopdetail.html?branduid={b}", c) for b, c in goods.items()]


PLATFORM_RUNNERS = {
    "godomall": run_godomall,
    "cafe24": run_cafe24,
    "makeshop": run_makeshop,
}


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--url", default="")
    ap.add_argument("--platform", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    base_url, platform, name_ko = args.url, args.platform, args.name
    if not (base_url and platform):
        reg = json.load(open(REGISTRY, encoding="utf-8"))
        entry = next((b for b in reg["brands"] if b["slug"] == args.slug), None)
        if not entry:
            print(f"레지스트리에 '{args.slug}' 없음 — onboard_brand.py 로 먼저 등록")
            sys.exit(1)
        base_url = base_url or entry["base_url"]
        platform = platform or entry["platform"]
        name_ko = name_ko or entry["name_ko"]
    name_ko = name_ko or args.slug

    runner = PLATFORM_RUNNERS.get(platform)
    if not runner:
        print(f"플랫폼 '{platform}' 은 범용 엔진 미지원 (지원: {list(PLATFORM_RUNNERS)})")
        sys.exit(2)

    items = runner(args.slug, name_ko, base_url.rstrip("/"))
    if args.limit:
        items = items[: args.limit]

    rows = []
    for i, (model_no, url, category) in enumerate(items):
        try:
            body = fetch(url)
        except Exception as e:
            print(f"  [{i+1}/{len(items)}] {model_no} 실패: {e}")
            continue
        tm = re.search(r"<title[^>]*>([^<]+)</title>", body)
        name = clean_name(tm.group(1) if tm else "", name_ko)
        if not name:
            continue
        rows.append(make_row(args.slug, name_ko, model_no, name, url, body, category))
        if (i + 1) % 20 == 0:
            print(f"  … {i+1}/{len(items)}")
        time.sleep(resolve_delay(args.slug))

    write_csv(rows, args.slug)
    filled = {c: sum(1 for r in rows if r[c]) for c in
              ("name", "price", "color", "material", "origin")}
    print(f"[{args.slug}] 채움: " + ", ".join(f"{k}={v}/{len(rows)}" for k, v in filled.items()))


if __name__ == "__main__":
    main()
