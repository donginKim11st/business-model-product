#!/usr/bin/env python3
"""식품 카탈로그(ctlg_no) 크로스몰 가격(offers) 적재 — product-identity-graph 식품 버전.

신발(nike)이 SKU별 크로스몰 가격사다리(price_summary/offers)를 갖듯, 식품 카탈로그(ctlg_no=풀네임 SKU)에도
네이버 쇼핑 API(search_shop)로 같은 가격 정체성을 붙인다 → 결합 화면(bundle_view) 우측에 가격사다리.

  패키지(bndl_grp) → 카탈로그(ctlg_no) → 크로스몰 offers(mall·price·중고/새) + price_summary(min~max·n_malls·최저몰)

저장:
  · offers 컬렉션: _id=ctlg_no|offN, product_uid=str(ctlg_no), package_uid, mall, platform, price, used, url, title, category_naver
  · 패키지 product.catalogs[i].price_summary = {min,max,median,n_malls,low_mall,n_listings,spread_pct,fetched_at}

재개 안전: price_summary 있는 카탈로그는 건너뜀(--refresh 로 재수집). 네이버 쇼핑 일일 한도(25k)는 넉넉.

  set -a; eval "$(grep '^export NAVER_' run.sh)"; set +a
  INSIGHTS_DB=insights_demo MONGO_URI=... python3 db/food_price_backfill.py [--limit 12] [--with-rep-only] [--display 30]
"""
import os
import re
import sys
import time
import argparse
from datetime import datetime, timezone

_COUNT_X = re.compile(r'[x×*]\s*(\d+)')                       # "490g x 3개", "x3"
_COUNT_UNIT = re.compile(r'(\d+)\s*(?:개|입|팩|봉|포|매|병|캔|구)')  # "3개", "5팩" (g/ml/L 단위는 제외)


def parse_count(title):
    """리스팅 제목에서 팩 개수 추정. 'x N' 우선, 없으면 마지막 'N개/입/팩…'. 없으면 None."""
    if not title:
        return None
    m = _COUNT_X.search(title)
    if m:
        return int(m.group(1))
    found = _COUNT_UNIT.findall(title)
    return int(found[-1]) if found else None


def count_num(count_str):
    """카탈로그 count('3개','단품'…) → 정수. 숫자 없으면 None(=단품 취급은 호출부에서)."""
    if not count_str:
        return None
    m = re.search(r'\d+', str(count_str))
    return int(m.group()) if m else None


def catalog_count(c):
    """카탈로그 묶음 개수: count 필드 우선 → 비었으면 disp 명에서 파싱(x2개·770gx3·200g*2 등) → 그래도
    없으면 단품(1). 소스 데이터에서 count 필드가 비어도 이름의 'x2개' 표기로 개수매칭이 되도록(1개짜리
    가격이 x2개 카탈로그에 새는 것 차단). disp 의 'xN/*N' 곱표기는 parse_count 의 _COUNT_X 가 우선 처리."""
    return count_num(c.get("count")) or parse_count(c.get("disp")) or 1


# ── 이름 매칭 (카탈로그명 ↔ 리스팅 상품명) ───────────────────────────────────────
# 네이버 쇼핑 검색이 키워드 fuzzy 라 *다른 상품* 리스팅이 섞여 반환된다(같은 규격의 다른 브랜드·다른 맛).
# 개수매칭만으론 못 거른다(예: '쿡시 사골미역국 12개' 검색에 '쿡시 해물맛 12개'가 끼어 최저가 오염).
# 카탈로그명 문자 bigram 중 리스팅 제목에 존재하는 비율(recall)로 동일상품 여부를 판정.
# 한글 형태소 분석기 없이 복합어(서리태흑미밥↔흑미밥)에 강하도록 문자 bigram 사용. 임계값 0.4는 골드셋
# (LLM 라벨 232건) 보정값 — 다른 브랜드·무관 카테고리(recall≈0)는 잡고, 진짜 동일은 99% 보존.
# 한계: 같은 브랜드 다른 변형(참기름↔들기름)·묶음(A+B)은 문자유사도로 못 가른다(잔존, 별도 LLM 게이트 필요).
_NM_X   = re.compile(r'[x×*]\s*\d+', re.I)
_NM_SZ  = re.compile(r'\d+(?:\.\d+)?\s*(?:kg|g|ml|l|리터|개입|개|입|팩|봉|포|매|병|캔|구|호|인분|인용|set|세트|박스|box|pet|p|매입|봉지|컵|병입)\b', re.I)
_NM_NUM = re.compile(r'\d+')
_NM_STOP = {"무라벨","무료배송","대용량","국내산","초특가","특가","본상품","낱개판매","낱개","정품","공식","공식몰",
 "천연암반수","미네랄워터","깨끗한물","안전한생수","먹는샘물","생수","즉석","간편","간편식","업소용","캠핑","산모",
 "사은품","증정","랜덤","랜덤발송","랜덤라벨","유라벨","택배","묶음","세트","멀티팩","멀티","신선","윤기","행사",
 "할인","당일발송","무배","최저가","무료","정기구독","단품","대량","소량","리뷰","gift","new","best"}
_NM_ALIAS = {"빽쿡":"더본","백종원":"더본","백종원의":"더본","the미식":"더미식","더미식의":"더미식",
 "deeps":"딥스","글로벌심층수":"딥스","해양심층수":"딥스","오뚜기참치":"오뚜기","동원참치":"동원","동원f":"동원",
 "동원fnb":"동원","cj제일제당":"cj","제일제당":"cj"}


def _nm_toks(s):
    s = (s or "").lower()
    s = _NM_X.sub(" ", s); s = _NM_SZ.sub(" ", s); s = _NM_NUM.sub(" ", s)
    out = []
    for t in re.findall(r"[가-힣a-z]+", s):
        if len(t) < 2:
            continue
        t = _NM_ALIAS.get(t, t)
        if t in _NM_STOP:
            continue
        out.append(t)
    return out


def _nm_bigrams(s):
    s = "".join(_nm_toks(s))
    return set(s[i:i+2] for i in range(len(s) - 1)) if len(s) >= 2 else ({s} if s else set())


def name_recall(catalog_name, offer_title):
    """카탈로그명 bigram 중 offer 제목에 존재하는 비율(0~1). 카탈로그 토큰 없으면 1.0(판정 보류)."""
    a = _nm_bigrams(catalog_name)
    if not a:
        return 1.0
    return len(a & _nm_bigrams(offer_title)) / len(a)


# ── LLM 의미 게이트 (문자필터가 못 가르는 잔존: 같은 브랜드 다른 변형 · 묶음상품) ──────────────
# 문자 bigram recall 은 같은 브랜드 다른 맛(딥스 골드↔에코그린·국간장↔진간장)이나 묶음(A+B,골라담기)을
# 못 가른다(식별 토큰이 작거나 catalog⊆offer). '의심구간'만 LLM 으로 동일/다름 판정해 다른 상품을 추가 제거.
#   의심 = recall ∈ [lo,hi)  OR  묶음신호(+품목·N종·or·골라담기·택N·외(·/품목).  hi 이상이고 묶음신호 없으면 신뢰.
# 비용: (카탈로그명, 상품명) 쌍 verdict 를 offer_match_cache 에 캐시 → 일배치 --refresh 는 신규 쌍만 호출.
# 기본 gpt-4o-mini(비용 우선). 개선된 프롬프트(동의어·대조 예시)로 정확도 보완. 캐시로 일배치 비용 작음.
# 정확도 우선 시 PRICE_MATCH_MODEL=gpt-4o (골드셋 대비 미묘한 변형/묶음 판정 일치율 ↑).
LLM_MATCH_MODEL = os.environ.get("PRICE_MATCH_MODEL") or "gpt-4o-mini"
# 라우팅용(LLM 이 최종판정)이라 recall 우선 — 묶음 가능성 신호를 넓게 잡는다(오탐은 캐시된 LLM 1콜 비용뿐).
_COMBO_MARK = re.compile(r'\S\s*\+\s*\S|\bor\b|\d\s*종|골라\s*담|선택|택\s*\d|외\s*\(|/\s*[가-힣]', re.I)
_UNIT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(kg|g|ml|l|리터)\b', re.I)
_LLM_CLIENT = None


def _combo_marker(title):
    return bool(_COMBO_MARK.search(title or ""))


def _sizes(s):
    """문자열에서 단위용량 집합 추출(g·ml 로 정규화). kg→g, l/리터→ml."""
    out = set()
    for num, unit in _UNIT_RE.findall((s or "").lower()):
        v = float(num); u = unit.lower()
        if u == "kg":
            v *= 1000; u = "g"
        if u in ("l", "리터"):
            v *= 1000; u = "ml"
        out.add((u, round(v)))
    return out


def _size_mismatch(cat_name, title):
    """카탈로그·리스팅에 같은 단위(g/ml)가 다 있는데 값이 하나도 안 겹치면 다른 용량 = 다른 상품 신호.
    이름 bigram 은 용량을 제거하므로(노이즈 방지) 500g↔250g 같은 차이를 못 본다 → 별도 신호로 LLM 라우팅."""
    cs = _sizes(cat_name)
    ts = _sizes(title)
    if not cs or not ts:
        return False
    for u in {u for u, _ in cs}:
        cv = {v for uu, v in cs if uu == u}
        tv = {v for uu, v in ts if uu == u}
        if tv and not (cv & tv):
            return True
    return False


def _match_cache_key(cat_name, title):
    import hashlib
    return "m:" + hashlib.sha1((str(cat_name) + "\x01" + str(title)).encode("utf-8")).hexdigest()


def _get_llm():
    global _LLM_CLIENT
    if _LLM_CLIENT is None:
        import run_batch
        _LLM_CLIENT = run_batch.make_client()
    return _LLM_CLIENT


def _llm_judge(cat_name, titles):
    """카탈로그명 vs 리스팅 제목들 → 동일상품 여부 list[bool]. 실패 시 전부 True(보수적: 안 지움)."""
    from pydantic import BaseModel

    class _V(BaseModel):
        i: int
        same: bool

    class _Out(BaseModel):
        verdicts: list[_V]

    listing = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    sys_msg = (
        "너는 한국 식품/생필품 가격비교 데이터 검수자다. 카탈로그 상품과 각 쇼핑 리스팅이 '동일 상품'인지"
        "(즉 이 리스팅 가격을 카탈로그의 가격으로 써도 되는지) 판정한다. 핵심은 **무엇이 들었는지(정체)**다.\n\n"
        "[same=true 로 보는 무시 요소] 판매자/몰 이름 · 마케팅 수식어(무료배송·사은품·대용량·간편 등) · 표기/띄어쓰기/"
        "영한 차이 · 묶음 개수(낱개수·캔수·박스수). 예: '동원 고추참치 150g 3캔' ↔ '동원 고추참치 150g x 5캔'=동일.\n"
        "[동의어·같은 회사는 same=true] 조선간장=국간장(재래식간장) · 해표=사조해표(같은 회사) · The미식=더미식 · "
        "빽쿡=백종원=더본 · Deeps=딥스. 이런 표기/브랜드 동일성은 같은 상품으로 본다.\n\n"
        "[same=false — 반드시 다름으로] 아래 중 하나라도 해당하면 다른 상품이다:\n"
        "1) 다른 브랜드/제조사. 한 단어가 겹쳐도 제조사가 다르면 다름. 예: 화이트(세제)↔비트 화이트플러스, 스파클↔미라클,"
        " 풀무원↔커클랜드, 사조↔해표(서로 다른 회사).\n"
        "2) 같은 브랜드라도 맛·종류·재료·색·매운정도가 다름. 예: 골드↔에코그린, 국간장↔진간장↔조림간장↔맛간장,"
        " 발아현미↔현미↔흑미↔서리태흑미, 오곡밥↔잡곡밥, 감식초↔사과식초, 약간매운맛↔매운맛, 파인애플↔후르츠칵테일,"
        " 김치만두↔고기만두↔감자만두↔왕만두, 자른당면↔사리당면, 참기름↔들기름.\n"
        "3) 기본 단위 용량/규격이 다름(묶음개수 말고 1개 용량). 예: 500ml↔330ml, 900ml↔1.5L, 250g↔85g, 1kg↔200g.\n"
        "4) 서로 다른 SKU 의 묶음/세트/골라담기/선택. 제목에 '+'로 다른 품목이 붙거나(케찹+마요, 본된장+쌈장, 왕만두+"
        "김치왕만두), 'A or B', 'N종 택1', '여러 종류 나열(라이트/고추/야채 등)'이면 단일 카탈로그 가격으로 부적절 → 다름.\n\n"
        "애매하면 '겉이름이 비슷한지'가 아니라 '정확히 같은 맛·종류·용량의 같은 제조사 제품인지'로 판단하라.")
    usr = (f"카탈로그 상품: {cat_name}\n\n쇼핑 리스팅(번호. 제목):\n{listing}\n\n"
           "각 번호가 카탈로그와 동일 상품이면 same=true, 위 1~4 중 하나라도 해당하면 same=false 로 verdicts 반환.")
    try:
        resp = _get_llm().chat.completions.parse(
            model=LLM_MATCH_MODEL, temperature=0,
            messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": usr}],
            response_format=_Out)
        vmap = {v.i: v.same for v in resp.choices[0].message.parsed.verdicts}
        return [vmap.get(i, True) for i in range(len(titles))]
    except Exception as e:
        print(f"      ⚠ LLM 판정 실패(보수적 유지): {str(e)[:70]}", flush=True)
        return [True] * len(titles)


def llm_verify(db, cat_name, offers, lo=0.4, hi=0.7):
    """문자필터 통과 offers 중 '의심'만 LLM 으로 재판정해 다른 상품 제거. (kept, n_dropped) 반환.
    캐시(offer_match_cache) 우선 — 신규 (카탈로그,제목) 쌍만 LLM 호출."""
    if not offers or not cat_name:
        return offers, 0
    suspects = [o for o in offers
                if (lo <= name_recall(cat_name, o.get("title")) < hi)
                or _combo_marker(o.get("title")) or _size_mismatch(cat_name, o.get("title"))]
    if not suspects:
        return offers, 0
    cache = db.offer_match_cache
    verdict = {}                                    # id(o) -> same(bool)
    to_ask = []
    for o in suspects:
        doc = cache.find_one({"_id": _match_cache_key(cat_name, o.get("title"))})
        if doc is not None:
            verdict[id(o)] = doc["same"]
        else:
            to_ask.append(o)
    if to_ask:
        for o, same in zip(to_ask, _llm_judge(cat_name, [o.get("title") for o in to_ask])):
            verdict[id(o)] = same
            cache.update_one({"_id": _match_cache_key(cat_name, o.get("title"))},
                             {"$set": {"same": same, "cat": cat_name, "title": o.get("title"),
                                       "model": LLM_MATCH_MODEL, "ts": now_iso()}}, upsert=True)
    kept = [o for o in offers if verdict.get(id(o), True)]   # 의심 아닌 것은 그대로 유지
    return kept, len(offers) - len(kept)


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import naver_review_geo as nrg
from pymongo import MongoClient


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def snapshot_history(db, ctlg, pkg_uid, ps, date):
    """일자별 가격 스냅샷(추이). 같은 날 재실행은 덮어씀(멱등), 날짜가 바뀌면 누적 → 시계열.
    네이버 쇼핑은 과거 가격을 안 주므로 이렇게 매일 스냅샷을 쌓아 추이를 만든다."""
    db.price_history.update_one(
        {"_id": f"{ctlg}@{date}"},
        {"$set": {"ctlg_no": ctlg, "package_uid": pkg_uid, "date": date, "ts": now_iso(),
                  "min": ps["min"], "max": ps["max"], "median": ps["median"],
                  "n_malls": ps["n_malls"], "low_mall": ps["low_mall"], "source": "naver_shop"}},
        upsert=True)


def real_offers(items):
    """가격비교 대표행(mallName=네이버 / /catalog/) 제외 = 실제 개별 몰 리스팅만.
    중고 판정 — 네이버 쇼핑 productType 공식 스펙: 1·2·3=일반(새)상품, 4·5·6=중고,
    7~9=단종, 10~12=판매예정. (이전엔 2를 중고로 오판 → 새상품이 전부 '중고'로 찍히고 누락됐음)."""
    out = []
    for it in items:
        if not (it.get("lprice") and it.get("mallName")):
            continue
        if it["mallName"] == "네이버" or "/catalog/" in (it.get("link") or ""):
            continue
        pt = it.get("productType")
        cat = " > ".join(x for x in [it.get("category2"), it.get("category3")] if x)
        out.append({"mall": it["mallName"], "platform": it["mallName"], "price": it["lprice"],
                    "used": pt in (4, 5, 6),               # 4~6 만 중고 (2는 새상품!)
                    "product_type": pt, "url": it.get("link"),
                    "title": it.get("title"), "category_naver": cat})
    return out


def clean_offers(offers, target_count=None, match_count=True,
                 exclude_used=False, ratio=4.0, iqr_k=0.0, pct_trim=0,
                 cat_name=None, match_name=True, name_thresh=0.4):
    """가격 이상치 제거. 네이버 쇼핑 검색이 키워드 fuzzy 라 *다른 상품/팩 개수* 리스팅이 섞여 반환된다
    (예: 3개 카탈로그 검색에 1개 단품 ₩3,500 이 끼어 최저가를 오염, 또는 다른 브랜드가 끼어 오염). 그래서:

    - match_name(이름 게이트): 카탈로그명(cat_name)과 리스팅 제목의 bigram recall < name_thresh 인 것 제거.
      ★ 다른 단계와 달리 '폴백 없음' — 전부 불일치면 [] 반환(틀린 가격보다 가격 없음이 옳다. 호출부가 정리).
    - match_count(주력): 카탈로그 개수(target_count)와 리스팅 제목의 팩 개수가 일치하는 것만.
      단품(target_count=1)은 '개수 표기 없음'도 단품으로 허용. 매칭이 2건 미만이면 폴백(안 비움).
    - ratio(4.0): 중앙값 기준 [median/ratio, median*ratio] 밖 제거(남은 극단 정리).
    - exclude_used: 중고(productType 4~6) 제외(식품엔 거의 없음 · 폴백 보유).
    - iqr_k / pct_trim: 보조. 이름 게이트 외 어떤 단계도 결과를 비우지 않는다."""
    pool = [o for o in offers if o.get("price")]
    if not pool:
        return [], 0
    if match_name and cat_name:                 # 다른-상품 리스팅 제거(폴백 없음 — 빈 풀 허용)
        matched = [o for o in pool if name_recall(cat_name, o.get("title")) >= name_thresh]
        if not matched:                         # 전부 다른 상품 → 유효 가격 없음
            return [], len(offers)
        pool = matched
    if match_count and target_count:
        def _ok(o):
            n = parse_count(o.get("title"))
            return (n in (None, 1)) if target_count == 1 else (n == target_count)
        matched = [o for o in pool if _ok(o)]
        if matched:                         # 개수 일치 리스팅이 하나라도 있으면 그것만(단품 누수 차단)
            pool = matched                  # 전부 불일치/미파싱이면 폴백(안 비움)
    if exclude_used:
        nonused = [o for o in pool if not o.get("used")]
        if nonused:
            pool = nonused
    def med(ps):
        ps = sorted(ps); n = len(ps)
        return ps[n // 2] if n % 2 else (ps[n // 2 - 1] + ps[n // 2]) // 2
    if ratio and len(pool) >= 3:
        m = med([o["price"] for o in pool]) or 1
        kept = [o for o in pool if m / ratio <= o["price"] <= m * ratio]
        pool = kept or pool
    if pct_trim and len(pool) >= 5:
        ps = sorted(o["price"] for o in pool); n = len(ps)
        lo = ps[max(0, n * pct_trim // 100)]; hi = ps[min(n - 1, n - 1 - n * pct_trim // 100)]
        kept = [o for o in pool if lo <= o["price"] <= hi]
        pool = kept or pool
    if iqr_k and len(pool) >= 4:
        ps = sorted(o["price"] for o in pool); n = len(ps)
        q1, q3 = ps[n // 4], ps[(3 * n) // 4]; iqr = q3 - q1
        kept = [o for o in pool if q1 - iqr_k * iqr <= o["price"] <= q3 + iqr_k * iqr]
        pool = kept or pool
    return pool, len(offers) - len(pool)


def price_summary(offers):
    prices = sorted(o["price"] for o in offers if o.get("price"))
    if not prices:
        return None
    malls = {o["mall"] for o in offers if o.get("mall")}
    n = len(prices)
    median = prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) // 2
    low = min(offers, key=lambda o: o["price"])
    spread = round((prices[-1] - prices[0]) / prices[0] * 100) if prices[0] else None
    return {"min": prices[0], "max": prices[-1], "median": median, "n_malls": len(malls),
            "n_listings": len(offers), "low_mall": low.get("mall"), "spread_pct": spread,
            "fetched_at": now_iso()}


def do_reclean(db, clean_kw, snap_date, use_llm=False, llm_hi=0.7):
    """API 호출 없이 기존 offers 를 이상치/이름 필터로 재정제 → price_summary/추이 재계산, offer 삭제.
    use_llm=True 면 의심구간을 LLM 의미게이트로 추가 검증(OPENAI 키 필요 · 캐시로 비용 절감)."""
    # $elemMatch 필수: 'catalogs.price_summary.min:{$ne:None}' 는 배열 중 하나라도 min=None 이면 패키지 전체를
    # 제외한다(가격 있는 카탈로그 + 빈 카탈로그가 섞인 패키지가 통째로 누락). 한 원소라도 min!=None 이면 매칭.
    pkgs = list(db.products.find({"type": "package",
                                  "catalogs": {"$elemMatch": {"price_summary.min": {"$ne": None}}}},
                                 {"_id": 1, "catalogs": 1}))
    print(f"[reclean] 가격 보유 패키지 {len(pkgs)} 재정제 (API 없음 · {clean_kw})")
    import time as _t
    t0 = _t.time(); n_cat = n_drop_tot = n_changed = 0
    for pkg in pkgs:
        cats = pkg.get("catalogs") or []
        changed = False
        for c in cats:
            ctlg = c.get("ctlg_no")
            if not (ctlg and (c.get("price_summary") or {}).get("min")):
                continue
            cuid = str(ctlg)
            raw = list(db.offers.find({"product_uid": cuid}))
            if not raw:
                continue
            n_cat += 1
            kept, n_drop = clean_offers(raw, target_count=catalog_count(c),
                                        cat_name=c.get("disp"), **clean_kw)
            if use_llm and kept:                            # 의심구간 LLM 의미판정(변형·묶음 제거)
                kept, n_llm = llm_verify(db, c.get("disp"), kept,
                                         lo=clean_kw.get("name_thresh", 0.4), hi=llm_hi)
                n_drop += n_llm
            ps = price_summary(kept)
            if not ps:                          # 이름 게이트로 전부 탈락 = 유효 가격 없음
                db.offers.delete_many({"product_uid": cuid})        # 틀린 offers 정리
                c["price_summary"] = {"min": None, "n_listings": 0,
                                      "n_dropped": n_drop, "fetched_at": now_iso()}
                n_drop_tot += n_drop; changed = True
                continue
            ps["n_dropped"] = n_drop; n_drop_tot += n_drop
            keep_ids = [o["_id"] for o in kept]
            db.offers.delete_many({"product_uid": cuid, "_id": {"$nin": keep_ids}})
            c["price_summary"] = ps
            snapshot_history(db, ctlg, pkg["_id"], ps, snap_date)
            changed = True
        if changed:
            db.products.update_one({"_id": pkg["_id"]}, {"$set": {"catalogs": cats}}); n_changed += 1
    print(f"[reclean] 카탈로그 {n_cat} 재정제 · 이상치 제거 {n_drop_tot:,} offers · 패키지 {n_changed} 갱신 · {_t.time()-t0:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="처리 패키지 수(0=전체)")
    ap.add_argument("--with-rep-only", action="store_true",
                    help="대표 인사이트 있는 패키지만(=화면에 보이는 것만, 데모 비용 절약)")
    ap.add_argument("--display", type=int, default=30, help="카탈로그당 네이버 쇼핑 수집 수")
    ap.add_argument("--per-pkg-cap", type=int, default=8, help="패키지당 가격수집 카탈로그 상한")
    ap.add_argument("--refresh", action="store_true", help="이미 price_summary 있어도 재수집")
    ap.add_argument("--date", default=None, help="스냅샷 일자(기본 오늘 UTC). 매일 cron이면 자동 누적")
    ap.add_argument("--max-calls", type=int, default=int(os.environ.get("NAVER_DAILY_CALLS", "24000")),
                    help="이번 실행 네이버 쇼핑 호출 상한(일일 쿼터 보호). 소진 시 우아하게 중단·재개")
    # 이상치 필터
    ap.add_argument("--ratio", type=float, default=float(os.environ.get("PRICE_RATIO", "4.0")),
                    help="중앙값 기준 [median/ratio, median*ratio] 밖 가격 제거(0=끔)")
    ap.add_argument("--keep-used", action="store_true",
                    help="중고(productType 4~6) 유지. 기본은 제외(식품엔 거의 없음) — 단 전부 중고면 폴백")
    ap.add_argument("--no-match-count", action="store_true",
                    help="팩 개수 매칭 끔(기본은 카탈로그 개수=리스팅 개수 일치만 — 1개가 3개 카탈로그에 새는 것 차단)")
    ap.add_argument("--no-match-name", action="store_true",
                    help="이름 매칭 끔(기본은 카탈로그명 bigram recall >= name-thresh 인 리스팅만 — 다른 브랜드/상품 새는 것 차단)")
    ap.add_argument("--name-thresh", type=float, default=float(os.environ.get("PRICE_NAME_THRESH", "0.4")),
                    help="이름 매칭 임계값(카탈로그명 bigram recall). 골드셋 보정값 0.4. 전부 미달이면 가격 비움")
    ap.add_argument("--llm-verify", action="store_true",
                    help="의심구간(이름 recall<llm-hi 또는 묶음신호) offer 를 LLM 으로 동일/다름 재판정(변형·묶음 제거). OPENAI 키 필요")
    ap.add_argument("--llm-hi", type=float, default=float(os.environ.get("PRICE_MATCH_HI", "0.7")),
                    help="LLM 검증 상한. 이름 recall 이 이 값 미만(또는 묶음신호)인 offer 만 LLM 판정. 이상은 신뢰(호출 절약)")
    ap.add_argument("--iqr-k", type=float, default=0.0, help="IQR 펜스 계수(0=끔)")
    ap.add_argument("--pct-trim", type=int, default=0, help="상하위 pct%% 절단(0=끔)")
    ap.add_argument("--reclean", action="store_true",
                    help="API 호출 없이 기존 offers를 이상치 필터로 재정제(가격/추이 재계산)")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상만 출력")
    args = ap.parse_args()
    snap_date = args.date or today_str()
    clean_kw = dict(exclude_used=not args.keep_used, ratio=args.ratio,
                    iqr_k=args.iqr_k, pct_trim=args.pct_trim,
                    match_count=not args.no_match_count,
                    match_name=not args.no_match_name, name_thresh=args.name_thresh)

    nid = os.environ.get("NAVER_CLIENT_ID"); nsec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (args.dry_run or args.reclean) and not (nid and nsec):
        sys.exit("✗ NAVER_CLIENT_ID/SECRET 필요 (run.sh export 로드)")
    if args.llm_verify and not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
        sys.exit("✗ --llm-verify 는 OPENAI_API_KEY 필요 (run.sh export 로드)")

    db = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[
        os.environ.get("INSIGHTS_DB", "insights")]

    if args.reclean:
        do_reclean(db, clean_kw, snap_date, use_llm=args.llm_verify, llm_hi=args.llm_hi)
        return

    q = {"type": "package", "catalogs.0": {"$exists": True}}
    if args.with_rep_only:
        q["representative.dims.0"] = {"$exists": True}
    pkgs = list(db.products.find(q, {"_id": 1, "keyword": 1, "catalogs": 1}))
    if args.limit:
        pkgs = pkgs[:args.limit]
    print(f"가격 backfill 대상 패키지 {len(pkgs)}개 "
          f"(display={args.display}, with_rep_only={args.with_rep_only}, dry_run={args.dry_run})")
    print("=" * 64)

    t0 = time.time(); n_cat = n_priced = n_calls = 0; stop = False
    for pi, pkg in enumerate(pkgs, 1):
        if stop:
            break
        cats = pkg.get("catalogs") or []
        changed = False
        todo = [c for c in cats if c.get("ctlg_no")][:args.per_pkg_cap]
        print(f"[{pi}/{len(pkgs)}] {pkg['_id']} {pkg.get('keyword','')[:24]} · 카탈로그 {len(todo)}", flush=True)
        for c in cats:
            ctlg = c.get("ctlg_no")
            if not ctlg or c not in todo:
                continue
            n_cat += 1
            if c.get("price_summary") and not args.refresh:
                continue
            if args.dry_run:
                print(f"      · (dry) {ctlg} {c.get('disp','')[:38]}")
                continue
            if n_calls >= args.max_calls:               # 쿼터 예산 소진 → 우아하게 중단(재개 안전)
                print(f"\n■ 네이버 호출 예산 소진({n_calls}/{args.max_calls}) → 중단. 다음 실행이 이어받음.", flush=True)
                stop = True
                break
            cuid = str(ctlg)
            try:
                items = nrg.search_shop(c["disp"], nid, nsec, display=args.display)
                n_calls += 1
            except Exception as e:
                print(f"      ✗ {ctlg} 수집오류: {str(e)[:60]}", flush=True)
                continue
            offers, n_drop = clean_offers(real_offers(items), target_count=catalog_count(c),
                                          cat_name=c.get("disp"), **clean_kw)  # 이름게이트+이상치+개수매칭
            if args.llm_verify and offers:                  # 의심구간 LLM 의미판정(변형·묶음 제거)
                offers, n_llm = llm_verify(db, c.get("disp"), offers, lo=args.name_thresh, hi=args.llm_hi)
                n_drop += n_llm
            ps = price_summary(offers)
            if not ps:
                db.offers.delete_many({"product_uid": cuid})        # 이전(틀린) offers 정리(--refresh)
                c["price_summary"] = {"min": None, "n_listings": 0,
                                      "n_dropped": n_drop, "fetched_at": now_iso()}
                changed = True
                continue
            ps["n_dropped"] = n_drop
            # offers 컬렉션 교체(멱등) — 정제된 것만 저장
            db.offers.delete_many({"product_uid": cuid})
            odocs = [dict(o, _id=f"{cuid}|off{i}", product_uid=cuid, ctlg_no=ctlg,
                          package_uid=pkg["_id"]) for i, o in enumerate(sorted(offers, key=lambda x: x["price"]))]
            db.offers.insert_many(odocs, ordered=False)
            c["price_summary"] = ps
            snapshot_history(db, ctlg, pkg["_id"], ps, snap_date)   # 추이 스냅샷 누적
            changed = True; n_priced += 1
            print(f"      → {ctlg} {c.get('disp','')[:30]} : ₩{ps['min']:,}~{ps['max']:,} · {ps['n_malls']}몰 (이상치 {n_drop})", flush=True)
        if changed and not args.dry_run:
            db.products.update_one({"_id": pkg["_id"]}, {"$set": {"catalogs": cats}})
    db.offers.create_index("product_uid"); db.offers.create_index("package_uid")
    db.price_history.create_index("ctlg_no"); db.price_history.create_index("date")
    print("=" * 64)
    print(f"완료 · 카탈로그 {n_cat} 중 가격적재 {n_priced} · 네이버 호출 {n_calls} · {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
