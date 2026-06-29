#!/usr/bin/env python3
"""
공식몰 정형 데이터 '자동' 추출 프레임워크 (의존성 없음, stdlib만).

설계: 공통 스키마 + 재사용 추출 전략 + 소스별 어댑터.
  · 추출 전략(보편 후크 우선순위): JSON-LD(schema.org Product) → Next.js __NEXT_DATA__ → DOM/정규식
  · 어댑터: 각 공식몰의 list()/detail()을 공통 스키마로 정규화
  · 전송: 전부 서버측 urllib (Chrome UA). 단, Akamai 계열은 '집/거주지 IP'에서만 통과
    (데이터센터 IP면 403 → 거주지 프록시 또는 실제 브라우저 필요).

검증된 소스(2026-06-26):
  nike    : nike.com/kr  · __NEXT_DATA__(검색 Wall + 상품 selectedProduct) · 스타일코드/컬러/사이즈/GTIN/원산지
  dongwon : dongwonmall.com · JSON-LD Product · 가격/브랜드/productID/중량/평점
  nb      : nbkorea.com · 상세=DOM정규식(사이즈·제조국·소재), 리스트=AJAX-gated → seed(outputs/nb_seed.json)

사용:
  python3 official_extract.py nike "에어포스1" 8     # 검색→상위 8개 상세까지
  python3 official_extract.py dongwon 000064505      # 상품ID(쉼표로 여러개)
  python3 official_extract.py nb 10                  # nb_seed.json 상위 10개 신발 상세
  python3 official_extract.py demo                   # 세 소스 합쳐 outputs/official_structured.{csv,json}
출력: outputs/extract_<source>.{json,csv} (+ demo는 official_structured.*) — 모두 공통 스키마.
"""
import csv
import html as _html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 공통 스키마 — 모든 어댑터가 이 키로 정규화해 반환
FIELDS = ["source", "brand", "style_code", "name", "color", "base_color",
          "category", "gender", "sport", "price", "currency", "origin",
          "material", "n_sizes", "sizes", "gtins", "image", "url", "attributes"]


def rec(**kw):
    """공통 스키마 레코드(빠진 키는 빈값)."""
    r = {k: kw.get(k, "") for k in FIELDS}
    if isinstance(r["sizes"], list):
        r["sizes"] = "|".join(str(s) for s in r["sizes"])
    if isinstance(r["gtins"], list):
        r["gtins"] = "|".join(str(s) for s in r["gtins"])
    if isinstance(r["attributes"], (dict, list)):
        r["attributes"] = json.dumps(r["attributes"], ensure_ascii=False)
    return r


# --------------------------------------------------------------------- HTTP
# curl_cffi(크롬 TLS/JA3 지문 위장)가 있으면 우선 사용 — TLS지문만 검사하는 Akamai를
# 브라우저 없이 통과(언더아머 418 등). 단 Akamai 'JS 행동챌린지'(아디다스)는 못 넘음 →
# 그건 browser_get(Playwright)로 처리. 미설치 환경은 stdlib urllib로 폴백.
try:
    from curl_cffi import requests as _ccffi
    _HAS_CCFFI = True
except ImportError:  # pragma: no cover
    _HAS_CCFFI = False

# Akamai/Kasada 행동챌린지 셸 페이지 감지(작은 본문 + 센서 마커)
_CHALLENGE = re.compile(r'sec-if-cpt-container|_abck|Pardon Our Interruption|'
                        r'kasada|"cpr_chlge"|behavioral-content', re.I)


def looks_blocked(body):
    return len(body) < 3000 and bool(_CHALLENGE.search(body or ""))


def http_get(url, retries=2, timeout=20, impersonate="chrome"):
    """크롬 위장 GET. curl_cffi 우선, 폴백 urllib. 챌린지 셸이면 예외."""
    url = urllib.parse.quote(url, safe=":/?=&%#+,")
    last = None
    for i in range(retries + 1):
        try:
            if _HAS_CCFFI:
                r = _ccffi.get(url, impersonate=impersonate, timeout=timeout,
                               headers={"Accept-Language": "ko-KR,ko;q=0.9"})
                body = r.text
            else:
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
                    "Accept": "text/html,application/xhtml+xml"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", "ignore")
            if looks_blocked(body):
                raise RuntimeError("Akamai/Kasada JS 챌린지 — browser_get 필요")
            return body
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


# -------------------------------------------------- 추출 전략(재사용 가능)
_LD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_ND_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def parse_jsonld(html):
    """페이지의 모든 JSON-LD 객체를 평탄화해 반환."""
    out = []
    for m in _LD_RE.finditer(html):
        try:
            j = json.loads(m.group(1).strip())
        except Exception:  # noqa: BLE001
            continue
        out.extend(j if isinstance(j, list) else [j])
    return out


def first_product_ld(html):
    for n in parse_jsonld(html):
        if isinstance(n, dict) and n.get("@type") in ("Product", "ProductGroup"):
            return n
    return None


def parse_nextdata(html):
    m = _ND_RE.search(html)
    return json.loads(m.group(1)) if m else None


def _arr(v):
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return list(v.values())
    return [v] if v else []


# --------------------------------------------------------------- 어댑터: NIKE
class NikeAdapter:
    source, brand = "nike", "nike"
    BASE = "https://www.nike.com/kr"

    def search(self, query, limit=24):
        html = http_get(f"{self.BASE}/w?q={urllib.parse.quote(query)}")
        nd = parse_nextdata(html)
        prods = []
        try:
            gs = nd["props"]["pageProps"]["initialState"]["Wall"]["productGroupings"]
        except (KeyError, TypeError):
            return []
        for g in gs:
            for p in g.get("products", []):
                prods.append(p)
        return prods[:limit]

    def _wide(self, p):
        pr, dc = p.get("prices") or {}, p.get("displayColors") or {}
        return rec(source=self.source, brand=self.brand, style_code=p.get("productCode", ""),
                   name=(p.get("copy") or {}).get("title", "").strip(),
                   color=dc.get("colorDescription", ""),
                   base_color=(dc.get("simpleColor") or {}).get("label", ""),
                   category=(p.get("copy") or {}).get("subTitle", ""),
                   price=pr.get("currentPrice", ""), currency=pr.get("currency", "KRW"),
                   image=(p.get("colorwayImages") or {}).get("squarishURL", ""),
                   url=(p.get("pdpUrl") or {}).get("url", ""),
                   attributes={"badge": p.get("badgeLabel", ""), "is_new": bool(p.get("isNewUntil"))})

    def detail(self, code, pdp_url):
        html = http_get(pdp_url)
        nd = parse_nextdata(html)
        pp = nd["props"]["pageProps"]
        sp = pp.get("selectedProduct")
        if not sp or sp.get("styleColor") != code:
            for g in pp.get("productGroups", []):
                cand = (g.get("products") or {}).get(code)
                if cand:
                    sp = cand
                    break
        if not sp:
            return None
        pi = sp.get("productInfo") or {}
        tax = sp.get("taxonomyLabels") or {}
        material = ""
        for blk in _arr(pi.get("productDetails")):
            if isinstance(blk, dict):
                material += " ".join(blk.get("body", []) if isinstance(blk.get("body"), list) else []) + " "
        sizes = [s.get("localizedLabel") or s.get("label") for s in (sp.get("sizes") or [])]
        gtins = [((s.get("gtins") or [{}])[0].get("gtin", "")) for s in (sp.get("sizes") or [])]
        cat = " > ".join(tax.get("Collections", []) + tax.get("Product Type", [])) if isinstance(tax, dict) else ""
        return rec(source=self.source, brand=self.brand, style_code=code,
                   name=pi.get("fullTitle") or pi.get("title", ""),
                   color=sp.get("colorDescription", ""),
                   category=cat, gender="|".join(_arr(sp.get("genders"))),
                   sport="|".join(_arr(sp.get("sportTags"))),
                   price=(sp.get("prices") or {}).get("currentPrice", ""), currency="KRW",
                   origin="|".join(_arr(sp.get("manufacturingCountriesOfOrigin"))),
                   material=material.strip(), n_sizes=len(sizes), sizes=sizes, gtins=gtins,
                   url=(sp.get("pdpUrl") or {}).get("url", pdp_url),
                   attributes={"desc": pi.get("productDescription", "")[:200]})

    def run(self, query, n=8):
        wide = self.search(query)
        rows = []
        for p in wide[:n]:
            try:
                d = self.detail(p.get("productCode"), (p.get("pdpUrl") or {}).get("url", ""))
                rows.append(d or self._wide(p))
            except Exception as e:  # noqa: BLE001
                print(f"  [nike] {p.get('productCode')} 상세 실패: {e}", file=sys.stderr)
                rows.append(self._wide(p))
            time.sleep(0.2)
        return rows


# ------------------------------------------------------------ 어댑터: DONGWON
class DongwonAdapter:
    source, brand = "dongwon", "동원"
    BASE = "https://www.dongwonmall.com"

    def detail(self, product_id):
        html = http_get(f"{self.BASE}/product/detail.do?productId={product_id}")
        ld = first_product_ld(html)
        if not ld:
            return None
        offers = ld.get("offers") or {}
        attrs = {p.get("name"): p.get("value") for p in _arr(ld.get("additionalProperty"))
                 if isinstance(p, dict)}
        rate = ld.get("aggregateRating") or {}
        if rate:
            attrs["rating"] = rate.get("ratingValue")
            attrs["reviews"] = rate.get("reviewCount")
        return rec(source=self.source, brand=(ld.get("brand") or {}).get("name", self.brand),
                   style_code=ld.get("productID", product_id), name=ld.get("name", ""),
                   price=offers.get("price", ""), currency=offers.get("priceCurrency", "KRW"),
                   category="식품", image=ld.get("image", ""),
                   url=offers.get("url", f"{self.BASE}/product/detail.do?productId={product_id}"),
                   attributes=attrs)

    def run(self, ids, n=None):
        rows = []
        for pid in (ids if isinstance(ids, list) else [ids]):
            try:
                d = self.detail(pid.strip())
                if d:
                    rows.append(d)
            except Exception as e:  # noqa: BLE001
                print(f"  [dongwon] {pid} 실패: {e}", file=sys.stderr)
            time.sleep(0.2)
        return rows


# ----------------------------------------------------------------- 어댑터: NB
class NBAdapter:
    source, brand = "nb", "newbalance"
    BASE = "https://www.nbkorea.com"
    # 카테고리: (cateGrpCode, cIdx, gender) — productList.action 의 '신발' 그룹코드(250110 공통,
    # cIdx로 성별 구분). 새 카테고리는 사이트 필터 링크의 cateGrpCode/cIdx를 그대로 추가.
    CATS = {"men_shoes": ("250110", "1280", "MEN"),
            "women_shoes": ("250110", "1320", "WOMEN"),
            "kids_shoes": ("250110", "1353", "KIDS")}
    _SEL = re.compile(r'<a[^>]*id="selDetail"[^>]*>')
    _SIZE = re.compile(r'<input[^>]*name="pr_size"[^>]*>')
    _VAL = re.compile(r'value="([^"]+)"')
    _OG = re.compile(r'<meta property="og:title" content="([^"]*)"')
    # PDP 상품정보제공고시: <strong class="ttl">라벨</strong><div ...>값</div> 쌍
    # (컬러·소재·제조국·제조년월·품질보증기간·제조자/수입자 등 의무표기가 여기 다 있음)
    _TTL = re.compile(r'<strong class="ttl">([^<]+)</strong>\s*<[^>]*>(.*?)</div>', re.S)

    @staticmethod
    def _attr(tag, name):
        m = re.search(name + r'="([^"]*)"', tag)
        return m.group(1) if m else ""

    @classmethod
    def _gosi(cls, html):
        """상품정보제공고시 라벨→값 dict (HTML 태그/엔티티 정리)."""
        out = {}
        for lab, val in cls._TTL.findall(html):
            v = re.sub(r"<[^>]+>", " ", val)
            v = _html.unescape(v).replace("\xa0", " ")
            v = re.sub(r"\s+", " ", v).strip()
            if v and v not in ("-", ":"):
                out[lab.strip()] = v
        return out

    def list_category(self, key="men_shoes", max_pages=200):
        """카테고리 전 페이지를 서버측으로 크롤(상품데이터는 selDetail 앵커의 data-*에 박혀있음)."""
        grp, cidx, cat_gender = self.CATS.get(key, self.CATS["men_shoes"])
        rows, seen = [], set()
        for page in range(1, max_pages + 1):
            html = http_get(f"{self.BASE}/product/productList.action"
                            f"?cateGrpCode={grp}&cIdx={cidx}&pageNo={page}")
            tags = self._SEL.findall(html)
            if not tags:
                break
            fresh = 0
            for tag in tags:
                sc, cc = self._attr(tag, "data-style"), self._attr(tag, "data-color")
                if not sc or (sc + cc) in seen:
                    continue
                seen.add(sc + cc)
                fresh += 1
                price = self._attr(tag, "data-price").replace(",", "")
                nor = self._attr(tag, "data-nor-price").replace(",", "")
                rows.append(rec(source=self.source, brand=self.brand, style_code=sc,
                                name=self._attr(tag, "data-display-name"), category="신발",
                                gender=cat_gender, price=price, currency="KRW",
                                attributes={"col_code": cc, "nor_price": nor,
                                            "discounted": bool(nor and price != nor),
                                            "category_key": key, "page": page}))
            if fresh == 0:
                break
        return rows

    def enrich(self, key="men_shoes", limit=None):
        """카테고리 전수 리스트 + 각 SKU 상세(사이즈·제조국·소재) 병합."""
        listing = self.list_category(key)
        if limit:
            listing = listing[:limit]
        out = []
        for i, r in enumerate(listing):
            a = json.loads(r["attributes"]) if r["attributes"] else {}
            try:
                d = self.detail(r["style_code"], a.get("col_code", ""), r["price"],
                                r["name"], r.get("gender", ""))
                out.append(d)
            except Exception as e:  # noqa: BLE001
                print(f"  [nb] {r['style_code']} 상세 실패: {e}", file=sys.stderr)
                out.append(r)
            if (i + 1) % 50 == 0:
                print(f"  …{i + 1}/{len(listing)}", file=sys.stderr)
            time.sleep(0.1)
        return out

    def enrich_all(self):
        """모든 신발 카테고리(남/여/키즈) 리스트 크롤 → SKU별 등장 성별 수집 →
        유니크 SKU만 상세 1회. 남+여 동시 등장은 UNISEX로 표기(유니섹스 모델 다수)."""
        membership, seed = {}, {}
        for key in self.CATS:
            print(f"  [nb] list {key}…", file=sys.stderr)
            for r in self.list_category(key):
                a = json.loads(r["attributes"]) if r["attributes"] else {}
                k = (r["style_code"], a.get("col_code", ""))
                membership.setdefault(k, set()).add(r.get("gender", ""))
                seed.setdefault(k, r)
        out = []
        items = list(seed.items())
        for i, (k, r) in enumerate(items):
            g = membership[k]
            gender = "UNISEX" if {"MEN", "WOMEN"} <= g else "|".join(sorted(x for x in g if x))
            a = json.loads(r["attributes"]) if r["attributes"] else {}
            try:
                out.append(self.detail(r["style_code"], a.get("col_code", ""),
                                       r["price"], r["name"], gender))
            except Exception as e:  # noqa: BLE001
                print(f"  [nb] {r['style_code']} 상세 실패: {e}", file=sys.stderr)
            if (i + 1) % 100 == 0:
                print(f"  …{i + 1}/{len(items)}", file=sys.stderr)
            time.sleep(0.1)
        return out

    def seed(self):
        p = os.path.join(OUT, "nb_seed.json")
        if not os.path.exists(p):
            return []
        d = json.load(open(p, encoding="utf-8"))
        return d.get("list", [])

    def detail(self, style_code, col_code, seed_price="", seed_name="", seed_gender="", category="신발"):
        url = f"{self.BASE}/product/productDetail.action?styleCode={style_code}&colCode={col_code}"
        html = http_get(url)
        sizes, soldout = [], []
        for tag in self._SIZE.findall(html):
            v = self._VAL.search(tag)
            if not v:
                continue
            sizes.append(v.group(1))
            if "disabled" in tag:
                soldout.append(v.group(1))
        og = self._OG.search(html)
        name = (og.group(1) if og else seed_name) or seed_name
        # 상품정보제공고시 블록에서 의무표기 정형필드 일괄 추출
        g = self._gosi(html)
        color = re.sub(r"^\(\d+\)\s*", "", g.get("컬러", ""))  # "(15)Gray" → "Gray"
        origin = g.get("제조국", "")
        mat = g.get("소재", "")
        mfg_date = g.get("제조년월", "") or g.get("제조연월", "")
        warranty = g.get("품질보증기간", "")
        maker = g.get("제조자", "") or g.get("수입자", "") or g.get("제조자/수입자", "") or g.get("제조원", "")
        # 이름 끝 "(...)" = 발볼 폭/성별 스펙. NB 남성 신발 D·2E·4E·EE = '발볼 넓이'(width).
        cm = re.search(r'\(([^)]+)\)\s*$', name or "")
        paren = cm.group(1).strip() if cm else ""
        is_spec = bool(re.search(r'남성|여성|발볼', paren)) or bool(re.fullmatch(r'\d?[A-E]E?', paren))
        # 발볼/성별 스펙 paren 만 제거 — 의류 등 정상 이름의 끝 괄호는 보존
        model = re.sub(r'\s*\([^)]+\)\s*$', "", name or "") if is_spec else (name or "")
        wm = re.search(r'\d?E{1,2}|[A-E](?![A-Za-z가-힣])', paren) if is_spec else None
        width = wm.group(0) if wm else ""
        gender = "MEN" if "남성" in paren else ("WOMEN" if "여성" in paren else seed_gender)
        return rec(source=self.source, brand=self.brand, style_code=style_code,
                   name=model, color=color, gender=gender, category=category,
                   price=seed_price, currency="KRW",
                   origin=origin, material=mat, n_sizes=len(sizes), sizes=sizes,
                   gtins="",  # NB는 GTIN 미노출
                   url=url, attributes={"col_code": col_code, "width": width,
                                        "mfg_date": mfg_date, "warranty": warranty,
                                        "maker": maker, "soldout_sizes": soldout})

    def run(self, n=10):
        seeds = self.seed()
        shoes = [s for s in seeds if re.search(r'\([A-Z]', s.get("name", ""))][:n]
        rows = []
        for s in shoes:
            try:
                rows.append(self.detail(s["style_code"], s.get("col_code", ""),
                                        s.get("price", ""), s.get("name", "")))
            except Exception as e:  # noqa: BLE001
                print(f"  [nb] {s.get('style_code')} 실패: {e}", file=sys.stderr)
            time.sleep(0.15)
        return rows


ADAPTERS = {"nike": NikeAdapter, "dongwon": DongwonAdapter, "nb": NBAdapter}


# ----------------------------------------------------------------- 출력/CLI
def write(rows, base):
    os.makedirs(OUT, exist_ok=True)
    jp = os.path.join(OUT, base + ".json")
    cp = os.path.join(OUT, base + ".csv")
    json.dump(rows, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(cp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return jp, cp


def demo():
    rows = []
    print("[demo] nike 에어포스1 (검색→상세 6)…")
    rows += NikeAdapter().run("에어포스1", n=6)
    print("[demo] dongwon (동원참치)…")
    rows += DongwonAdapter().run(["000064505"])
    print("[demo] nb (seed 신발 상세 6)…")
    rows += NBAdapter().run(n=6)
    jp, cp = write(rows, "official_structured")
    print(f"\n총 {len(rows)} 레코드 (소스별 "
          + ", ".join(f"{s}={sum(1 for r in rows if r['source']==s)}" for s in ADAPTERS) + ")")
    for r in rows:
        print(f"  [{r['source']:7}] {r['style_code']:12} {r['name'][:24]:26} "
              f"{str(r['price']):>8} | 컬러 {r['color'][:14]:16} | 사이즈 {r['n_sizes']} | 원산지 {r['origin']}")
    print(f"→ {os.path.relpath(jp)}, {os.path.relpath(cp)}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "demo":
        return demo()
    src = sys.argv[1]
    if src not in ADAPTERS:
        print(f"알 수 없는 소스: {src} (가능: {', '.join(ADAPTERS)}, demo)")
        sys.exit(1)
    ad = ADAPTERS[src]()
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if src == "nike":
        rows = ad.run(arg or "에어포스", n=int(sys.argv[3]) if len(sys.argv) > 3 else 8)
    elif src == "dongwon":
        rows = ad.run([x for x in arg.split(",") if x])
    else:  # nb
        if arg == "list":              # 카테고리 전수 크롤(서버측)
            rows = ad.list_category(sys.argv[3] if len(sys.argv) > 3 else "men_shoes")
        elif arg == "all":             # 전 신발 카테고리(남/여/키즈) 크롤+상세
            rows = ad.enrich_all()
        elif arg == "enrich":          # 전수 리스트 + 상세 병합: nb enrich [category]
            rows = ad.enrich(sys.argv[3] if len(sys.argv) > 3 else "men_shoes")
        elif arg == "detail":          # 단일 상세: nb detail <style> <col>
            rows = [ad.detail(sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")]
        else:                          # seed 기반 상세 N개(기존)
            rows = ad.run(n=int(arg) if arg.isdigit() else 10)
    jp, cp = write(rows, f"extract_{src}")
    print(f"{src}: {len(rows)} 레코드 → {os.path.relpath(jp)}, {os.path.relpath(cp)}")
    for r in rows[:15]:
        print(f"  {r['style_code']:12} {r['name'][:26]:28} {str(r['price']):>8} | 사이즈 {r['n_sizes']} | {r['origin']}")


if __name__ == "__main__":
    main()
