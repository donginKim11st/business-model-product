"""
네이버 블로그 + 유튜브 댓글 → 광고/협찬 필터 → LLM(OpenAI)으로 FAQ·속성 추출
→ schema.org FAQPage JSON-LD 생성. 시장조사용(내 상품 콘텐츠/GEO 개선).
원문 재게시가 아니라 '인사이트'만 추출합니다.

사용법:
    export NAVER_CLIENT_ID=...        # https://developers.naver.com 에서 발급
    export NAVER_CLIENT_SECRET=...
    export OPENAI_API_KEY=...
    export YOUTUBE_API_KEY=...        # 선택 — 있으면 유튜브 댓글도 같이 수집(API 키만, OAuth 불필요)
    export BRAVE_SEARCH_API_KEY=...   # 선택 — L4(공식몰 정보 추출) 활성화
                                      #         https://api.search.brave.com 에서 발급, 무료 2,000건/월
    export PRODUCT_NAME="나이키 에어포스 1 블랙"   # 선택 — FAQ JSON-LD의 about에 연결
    # L4 사용 시 권장: pip install playwright && playwright install chromium
    python naver_review_geo.py "에어포스 블랙 후기" "에어포스 사이즈" "에어포스 발볼"

출력: insights.json + faq.jsonld + faq_snippet.html
       insights.json의 각 키워드에 'shop' 키 포함:
         · summary       : 가격 분포·상위 몰·브랜드·카테고리 (L1 — 네이버 쇼핑 API)
         · items         : 정규화된 상품 리스트 (L2 — 제목 → 브랜드/모델/색상/사이즈/SKU)
         · catalog_specs : 가격비교 상위 N건의 스펙·평점 (L3 — catalog 페이지)
         · official_info : 공식몰 정가/색상/사이즈/소재/출시일 (L4 — Google CSE + Playwright)
"""

from __future__ import annotations

import os
import re
import sys
import json
import html
import time
import difflib
import unicodedata
from collections import Counter
from typing import List, Optional, Dict, Tuple

import requests           # 네이버/유튜브 API는 공식 SDK가 없어 raw HTTP 사용
from openai import OpenAI  # LLM 추출은 공식 OpenAI SDK
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────────
# 1) 네이버 블로그 검색 API
# ──────────────────────────────────────────────────────────────────────────
NAVER_ENDPOINT = "https://openapi.naver.com/v1/search/blog.json"
TAG_RE = re.compile(r"<[^>]+>")                       # <b> 등 검색 하이라이트 태그 제거
# 협찬/광고성 글을 거르는 신호 (한국 블로그 표기 관행)
AD_PATTERNS = [
    "제공받아", "제공 받아", "지원받아", "지원 받아", "원고료",
    "협찬", "광고", "유료광고", "소정의", "체험단", "서포터즈",
    "쿠팡파트너스", "파트너스 활동", "수수료를 제공",
]


def search_blog(keyword: str, client_id: str, client_secret: str,
                display: int = 50, sort: str = "sim") -> List[dict]:
    """키워드로 블로그 글 제목·요약·링크·작성일을 수집 (최대 100)."""
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": keyword, "display": min(display, 100), "sort": sort}
    resp = requests.get(NAVER_ENDPOINT, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    cleaned = []
    for it in items:
        cleaned.append({
            "title": html.unescape(TAG_RE.sub("", it.get("title", ""))),
            "desc": html.unescape(TAG_RE.sub("", it.get("description", ""))),
            "link": it.get("link", ""),
            "postdate": it.get("postdate", ""),
            "bloggername": it.get("bloggername", ""),
            "source": "naver",
        })
    return cleaned


def ad_signals(item: dict) -> List[str]:
    """제목+요약에서 발견된 광고/협찬 신호 단어 목록 (없으면 빈 리스트)."""
    text = f"{item.get('title', '')} {item.get('desc', '')}"
    return [p for p in AD_PATTERNS if p in text]


def is_ad(item: dict) -> bool:
    """제목+요약에 협찬/광고 신호가 있으면 광고성으로 판정."""
    return bool(ad_signals(item))


# ──────────────────────────────────────────────────────────────────────────
# 1-b) 유튜브 Data API v3 — 공개 영상 댓글 수집 (API 키만, OAuth 불필요)
# ──────────────────────────────────────────────────────────────────────────
YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
YT_COMMENTS = "https://www.googleapis.com/youtube/v3/commentThreads"


def yt_search_videos(keyword: str, api_key: str, max_results: int = 3) -> List[str]:
    """키워드로 관련 영상 ID 목록 (search.list = 호출당 100 units, 적게 사용)."""
    params = {
        "part": "snippet", "q": keyword, "type": "video",
        "maxResults": min(max_results, 10), "order": "relevance", "key": api_key,
    }
    resp = requests.get(YT_SEARCH, params=params, timeout=10)
    resp.raise_for_status()
    return [it["id"]["videoId"] for it in resp.json().get("items", [])
            if it.get("id", {}).get("videoId")]


def _yt_item(sn: dict, video_id: str, kind: str) -> dict:
    """유튜브 댓글/답글 snippet → 블로그 아이템과 동일 형태로 변환.
    kind: "comment"(원댓글) | "reply"(답글=질문에 대한 답변)."""
    return {
        "title": "",                              # 댓글엔 제목이 없음
        "desc": sn.get("textDisplay", ""),        # 댓글 본문 → desc로 통일
        "link": f"https://youtu.be/{video_id}",
        "video_id": video_id,                     # distinct 영상수 집계 등에 사용
        "postdate": sn.get("publishedAt", "")[:10].replace("-", ""),
        "bloggername": sn.get("authorDisplayName", ""),
        "source": "youtube",
        "kind": kind,
    }


def yt_fetch_comments(video_id: str, api_key: str, max_comments: int = 50) -> List[dict]:
    """영상의 상위 댓글 + 답글(대댓글) 수집 (commentThreads.list = 호출당 1 unit).
    답글은 질문에 대한 '답변'인 경우가 많으므로 함께 모은다. replies는 part=replies로
    응답에 최대 5개까지 번들로 실려와 추가 API 호출 없이 확보된다."""
    params = {
        "part": "snippet,replies", "videoId": video_id,
        "maxResults": min(max_comments, 100), "order": "relevance",
        "textFormat": "plainText", "key": api_key,
    }
    try:
        resp = requests.get(YT_COMMENTS, params=params, timeout=10)
        resp.raise_for_status()
    except requests.HTTPError:
        # 쿼터 소진(403 quotaExceeded/dailyLimitExceeded)은 재raise → 상위(collect→QuotaStop /
        # youtube_backfill)가 '항목 보존 + 중단'으로 처리. '댓글 사용중지' 등 양성 403만 []로 흡수.
        body = (getattr(resp, "text", "") or "").lower()
        if "quotaexceeded" in body or "dailylimitexceeded" in body:
            raise
        return []
    out = []
    for it in resp.json().get("items", []):
        sn = it["snippet"]["topLevelComment"]["snippet"]
        out.append(_yt_item(sn, video_id, "comment"))
        for rep in (it.get("replies") or {}).get("comments", []):
            rsn = rep.get("snippet") or {}
            if rsn.get("textDisplay"):
                out.append(_yt_item(rsn, video_id, "reply"))
    return out


def collect_youtube(keyword: str, api_key: str,
                    n_videos: int = 3, n_comments: int = 50) -> List[dict]:
    """키워드 → 관련 영상 → 댓글들을 블로그 아이템과 같은 형태로 반환."""
    comments = []
    for vid in yt_search_videos(keyword, api_key, n_videos):
        comments.extend(yt_fetch_comments(vid, api_key, n_comments))
    return comments


# ──────────────────────────────────────────────────────────────────────────
# 1-d) 다나와 쇼핑몰 통합 상품평 — 실제 구매 후기 (API 키 불필요)
#      검색(dsearch.php HTML)에서 pcode를 얻어, 상품평 ajax로 구매후기를 모은다.
#      ── robots.txt 준수: 우리가 호출하는 두 경로는 모두 다나와 robots.txt 허용 범위다.
#         · search.danawa.com/dsearch.php          → 검색결과(허용)
#         · prod.danawa.com/info/dpg/ajax/companyProductReview.ajax.php → 쇼핑몰 통합 상품평(허용)
#      ── 반면 다나와 robots.txt가 막는 경로(/info/ajax/ 한줄평·/community·/list/ajax·/api)는
#         의도적으로 쓰지 않는다. 즉 다나와 '자체 한줄평/Q&A/커뮤니티'는 수집하지 않고,
#         쇼핑몰 통합 '구매 상품평'만 수집한다.
# ──────────────────────────────────────────────────────────────────────────
DANAWA_SEARCH = "https://search.danawa.com/dsearch.php"
# 내부 vssearch JSON API — dsearch.php(6MB HTML) 대신 가벼운 JSON으로 pcode와
# 가격/평점/리뷰수 메타를 얻는다. dsearch.php를 Referer로 동반 호출하므로
# robots 허용 범위(dsearch 검색결과) 안에서 동작하며 봇차단(403) 위험도 낮다.
DANAWA_VSSEARCH_API = "https://prod.danawa.com/api/vssearch/searchProducts.php"
DANAWA_REVIEW_AJAX = (
    "https://prod.danawa.com/info/dpg/ajax/companyProductReview.ajax.php"
)
DANAWA_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# 검색결과 상품 블록 분리 단위 + 블록 내 상품명·pcode·정형 스펙요약(spec_list) 추출.
# spec_list는 네이버 카탈로그 봇차단으로 막힌 L3 정형 스펙을 robots 허용 경로로 보강한다.
_DANAWA_BLOCK_SPLIT = '<div class="prod_main_info"'
_DANAWA_NAME_RE = re.compile(
    r'class="prod_name">\s*<a[^>]*?pcode=(\d+)[^>]*>(.*?)</a>', re.S)
_DANAWA_SPEC_RE = re.compile(r'class="spec_list"[^>]*>(.*?)</(?:div|dd|p)>', re.S)
# 리뷰 한 건은 <div class="rvw_atc" ...> 단위로 분리되고, 본문은 그 안의 <div class="atc">
_DANAWA_BODY_RE = re.compile(r'<div class="atc">(.*?)</div>', re.S)
_DANAWA_STAR_RE = re.compile(r'width:\s*(\d+)%')
_DANAWA_DATE_RE = re.compile(r'(20\d{2})[.\-](\d{2})[.\-](\d{2})')
# 작성자(마스킹 ID, 예: ro****) / 쇼핑몰명 흔적
_DANAWA_MALL_RE = re.compile(r'class="[^"]*mall[^"]*"[^>]*>\s*(?:<[^>]+>)*\s*([^<]{1,20})')


def _danawa_text(raw: str) -> str:
    """다나와 HTML 조각 → 평문 (태그 제거 + 공백 정리)."""
    return _WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", raw))).strip()


def _parse_danawa_spec(spec_raw: str) -> Tuple[List[dict], List[str]]:
    """다나와 spec_list 문자열을 정형 key:value 스펙과 단순 라벨(features)로 분리.
    예: '봉지라면 / 라면종류: 국물라면 / 매운맛 / [영양정보] / 열량: 500kcal'
        → spec=[{key:라면종류,value:국물라면},{key:열량,value:500kcal}],
          features=[봉지라면, 매운맛]  ('[영양정보]' 같은 섹션 헤더는 스킵)."""
    specs: List[dict] = []
    feats: List[str] = []
    for part in spec_raw.split("/"):
        part = part.strip()
        if not part or part.startswith("["):    # [영양정보] 등 섹션 헤더는 건너뜀
            continue
        if ":" in part:
            k, v = part.split(":", 1)
            k, v = k.strip(), v.strip()
            if k and v:
                specs.append({"key": k, "value": v})
            elif k:
                feats.append(k)
        else:
            feats.append(part)
    return specs, feats


# 묶음/혼합(다른 제품이 섞인 세트) 신호 — 그 상품 '전용' 리뷰가 아니므로 제외한다.
# '+'(제품 나열), 'N종', '외 ~', '모음', '골라담기'. '120g 40개' 같은 단일상품 수량은 막지 않음.
_DANAWA_BUNDLE_RE = re.compile(r"\+|\d\s*종|외\s|모음|골라담기|혼합구성")


def _danawa_relevant(name: str, core_tokens: List[str]) -> bool:
    """상품명이 키워드와 '상품 단위'로 맞는지 정밀 판정.
    (1) 핵심 제품명 토큰을 '모두' 포함해야 한다(any가 아니라 all):
        '농심 신라면' → '농심'과 '신라면'이 둘 다 있어야 함.
        → '농심 봉지라면 3종 세트'(신라면 없음)는 제외된다.
        ※ 다나와는 상품명을 띄어쓰므로('비비고 왕 교자', '도브 바디 워시') 공백을 무시하고
          비교한다 — 키워드 '왕교자/바디워시'가 '왕 교자/바디 워시'에도 매칭되도록.
    (2) 여러 제품이 섞인 묶음/혼합 세트는 제외한다(리뷰가 그 상품 전용이 아니므로):
        '신라면 컵6개 + 튀김우동컵6개', '왕교자 1.05kg x 3개 + 수제 김치만두' 등.
    (3) 키워드에 없는 '추가 수식어(sub-type)'가 붙은 다른 제품은 제외한다 — 상품 단위 정밀화.
        메이커 접두(첫 핵심토큰 앞 'CJ제일제당')를 떼고, 핵심토큰·용량·개수를 지운 뒤
        남는 한글 토큰(2자+)이 있으면 다른 라인이다: '비비고 김치 왕 교자'→'김치' 남음→제외,
        '도브 유자 바디 워시'→'유자'→제외, '신라면 건면'→'건면'→제외. (고기/일반 왕교자만 남김)"""
    name_ns = re.sub(r"\s+", "", name)
    core_ns = [re.sub(r"\s+", "", t) for t in core_tokens]
    if core_ns and not all(t in name_ns for t in core_ns):
        return False
    if _DANAWA_BUNDLE_RE.search(name):
        return False
    if core_ns:
        # 측정 표기를 '공백 있는 원문'에서 먼저 제거(정규식 경계 정확) → 공백제거 → 메이커접두·핵심토큰 제거.
        # 공백 제거 후 제거하면 '120g40개입'이 붙어 경계가 깨지므로 순서가 중요하다.
        region = re.sub(r"\s+", "", _strip_measures(name))
        first = min((region.find(t) for t in core_ns if t in region), default=0)
        region = region[first:]                           # 메이커 접두 제거
        for t in core_ns:
            region = region.replace(t, "")
        if re.search(r"[가-힣]{2,}", region):              # 남는 한글 수식어 → 다른 sub-type
            return False
    return True


def search_danawa(keyword: str, top_n: int = 5) -> List[dict]:
    """다나와 검색 → 키워드와 관련된 상위 N개 상품의 정형 데이터.
    반환: [{pcode, name, spec:[{key,value}], features:[str], spec_raw}].
    dsearch.php HTML(robots 허용)의 spec_list에서 스펙을 추출 — 네이버 카탈로그
    봇차단으로 막힌 L3 정형 스펙을 보강한다. 상품 단위 relevance gate(_danawa_relevant)로
    핵심 제품명 토큰을 '모두' 포함하고 혼합/묶음 세트가 아닌 상품만 채택한다.
    dominant_model 등 정형 파이프라인에 의존하지 않아 INSIGHTS_ONLY에서도 동작한다."""
    try:
        resp = requests.get(
            DANAWA_SEARCH, params={"query": keyword},
            headers={"User-Agent": DANAWA_UA}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return []
    page = resp.content.decode("utf-8", "replace")   # dsearch는 UTF-8 (명시적 디코딩)
    tokens = [t for t in re.split(r"\s+", keyword.strip()) if len(t) >= 2]
    # 핵심 제품명 토큰(숫자·용량 표기로 시작하는 토큰 제외) — 상품 단위 정밀 매칭에 사용.
    # 예: '농심 신라면 120g 40개' → ['농심','신라면'] (120g·40개는 매칭 강제에서 제외)
    core = [t for t in tokens if not t[0].isdigit()] or tokens
    out: List[dict] = []
    seen: set = set()
    for blk in page.split(_DANAWA_BLOCK_SPLIT)[1:]:   # 상품 블록 단위로 분리
        nm = _DANAWA_NAME_RE.search(blk)
        if not nm:
            continue
        pcode, name = nm.group(1), _danawa_text(nm.group(2))
        if not name or pcode in seen:
            continue
        # 상품 단위 정밀 relevance gate — 핵심 토큰 전부 포함 + 혼합/묶음 세트 제외
        if not _danawa_relevant(name, core):
            continue
        seen.add(pcode)
        sm = _DANAWA_SPEC_RE.search(blk)
        spec_raw = _danawa_text(sm.group(1)) if sm else ""
        spec, feats = _parse_danawa_spec(spec_raw)
        out.append({"pcode": pcode, "name": name, "spec": spec,
                    "features": feats, "spec_raw": spec_raw})
        if len(out) >= top_n:
            break
    return out


def _parse_danawa_reviews(page: str, pcode: str, product_name: str) -> List[dict]:
    """상품평 ajax 응답(HTML) → 블로그/유튜브 아이템과 같은 형태의 리뷰 리스트.
    리뷰 한 건 = <div class="rvw_atc"> 블록. 본문(atc)/별점(star width%)/날짜/작성자 추출."""
    link = f"https://prod.danawa.com/info/?pcode={pcode}"
    blocks = page.split('<div class="rvw_atc"')[1:]   # 첫 조각은 헤더 → 버림
    out: List[dict] = []
    for blk in blocks:
        m = _DANAWA_BODY_RE.search(blk)
        if not m:
            continue
        body = _danawa_text(m.group(1))
        if len(body) < 5:           # 빈/잡음 본문 제외
            continue
        star = _DANAWA_STAR_RE.search(blk)
        rating = round(int(star.group(1)) / 20, 1) if star else None   # width 100%→5점
        dm = _DANAWA_DATE_RE.search(blk)
        postdate = f"{dm.group(1)}{dm.group(2)}{dm.group(3)}" if dm else ""
        mm = _DANAWA_MALL_RE.search(blk)
        author = _danawa_text(mm.group(1)) if mm else ""
        out.append({
            "title": product_name,          # 어떤 상품의 후기인지 맥락(검증 텍스트에 포함)
            "desc": body,                   # 실제 구매후기 본문
            "link": link,
            "postdate": postdate,
            "bloggername": author,          # 마스킹 작성자ID 또는 쇼핑몰명
            "source": "danawa",
            "kind": "danawa_review",
            "rating": rating,               # 별점(5점 만점, 없으면 None)
        })
    return out


def fetch_danawa_reviews(pcode: str, product_name: str,
                         max_pages: int = 2) -> List[dict]:
    """한 상품(pcode)의 쇼핑몰 통합 상품평을 max_pages 페이지까지 수집."""
    reviews: List[dict] = []
    for page_no in range(1, max_pages + 1):
        try:
            resp = requests.get(
                DANAWA_REVIEW_AJAX,
                params={"prodCode": pcode, "page": page_no},
                headers={"User-Agent": DANAWA_UA,
                         "Referer": f"https://prod.danawa.com/info/?pcode={pcode}",
                         "X-Requested-With": "XMLHttpRequest"},
                timeout=15)
            resp.raise_for_status()
        except requests.RequestException:
            break
        page = resp.content.decode("utf-8", "replace")   # 상품평 ajax도 UTF-8 (명시적)
        batch = _parse_danawa_reviews(page, pcode, product_name)
        if not batch:
            break                       # 더 이상 리뷰 없음
        reviews.extend(batch)
    return reviews


def search_danawa_api(keyword: str, top_n: int = 5) -> List[dict]:
    """다나와 내부 vssearch JSON API → 키워드 관련 상위 N개 상품의 pcode + 메타.
    반환: [{pcode, name, min_price, star_point, review_count, category}].
    dsearch.php HTML 파싱 대비 가볍고(수십 KB) 가격·평점·리뷰수 메타를 바로 준다.
    단 정형 스펙(spec_list)은 주지 않으므로, 정형 보강은 search_danawa(HTML)가 담당한다.
    search_danawa와 동일한 핵심토큰 relevance gate(_danawa_relevant)로 묶음/오탐을 거른다."""
    from urllib.parse import quote
    try:
        resp = requests.get(
            DANAWA_VSSEARCH_API,
            params={"keyword": keyword, "page": 1, "limit": max(top_n * 4, 30)},
            headers={"User-Agent": DANAWA_UA,
                     "Referer": f"{DANAWA_SEARCH}?query={quote(keyword)}",
                     "X-Requested-With": "XMLHttpRequest",
                     "Accept": "application/json, text/javascript, */*; q=0.01"},
            timeout=15)
        resp.raise_for_status()
        products = (resp.json().get("result") or {}).get("products") or []
    except (requests.RequestException, ValueError):
        return []
    tokens = [t for t in re.split(r"\s+", keyword.strip()) if len(t) >= 2]
    core = [t for t in tokens if not t[0].isdigit()] or tokens
    out: List[dict] = []
    seen: set = set()
    for p in products:
        pcode = str(p.get("productCode") or "")
        # vssearch는 검색어를 글자단위 <b> 하이라이트로 상품명에 끼워넣는다
        # ('APPLE <b>에어</b><b>팟</b>…') → 태그를 벗겨야 relevance gate가 정상 매칭된다.
        name = _danawa_text(p.get("productName") or "")
        if not pcode or not name or pcode in seen:
            continue
        if not _danawa_relevant(name, core):   # search_danawa와 동일한 정밀 게이트
            continue
        seen.add(pcode)
        out.append({
            "pcode": pcode,
            "name": name,
            "min_price": _to_int(p.get("minPrice")),
            "star_point": p.get("starPoint"),
            "review_count": _to_int(p.get("reviewCount")),
            "category": p.get("category"),
        })
        if len(out) >= top_n:
            break
    return out


def collect_danawa(keyword: str, n_products: int = 5,
                   max_pages: int = 2) -> List[dict]:
    """키워드 → 다나와 검색 → 상위 상품들의 쇼핑몰 통합 구매후기를 모아 반환.
    블로그/유튜브 아이템과 동일한 형태라 그대로 비정형 파이프라인에 합류한다.
    pcode 획득은 가벼운 vssearch JSON API를 우선 쓰고(가격/평점/리뷰수 메타 동반),
    실패 시 기존 dsearch.php HTML 파싱으로 폴백한다."""
    prods = search_danawa_api(keyword, top_n=n_products)
    if not prods:                              # API 실패 → 기존 HTML 경로 폴백
        prods = search_danawa(keyword, top_n=n_products)
    items: List[dict] = []
    for prod in prods:
        # API가 reviewCount=0이라 알려준 상품은 빈 후기탭 — ajax 호출을 아껴 건너뛴다.
        if prod.get("review_count") == 0:
            continue
        reviews = fetch_danawa_reviews(prod["pcode"], prod["name"], max_pages=max_pages)
        for rv in reviews:                     # 상품 메타를 후기 아이템에 부착(있을 때만)
            if prod.get("min_price") is not None:
                rv["product_min_price"] = prod["min_price"]
            if prod.get("star_point") is not None:
                rv["product_star_point"] = prod["star_point"]
            if prod.get("review_count") is not None:
                rv["product_review_count"] = prod["review_count"]
        items.extend(reviews)
    return items


# ──────────────────────────────────────────────────────────────────────────
# 1-c) 네이버 쇼핑 검색 API — 정형 상품 데이터 (가격/몰/브랜드/카테고리)
#      블로그 API와 동일한 NAVER_CLIENT_ID/SECRET 재사용. 추가 발급 불필요.
# ──────────────────────────────────────────────────────────────────────────
NAVER_SHOP_ENDPOINT = "https://openapi.naver.com/v1/search/shop.json"


def _to_int(v) -> Optional[int]:
    """가격 필드는 문자열로 오므로 안전 변환."""
    try:
        return int(v) if v not in (None, "", "0") else None
    except (ValueError, TypeError):
        return None


def search_shop(keyword: str, client_id: str, client_secret: str,
                display: int = 50, sort: str = "sim") -> List[dict]:
    """키워드로 상품의 제목·가격·몰·카테고리·브랜드 수집 (최대 100).
    sort: sim(정확도) | date(등록일) | asc/dsc(가격 오름/내림차순)."""
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": keyword, "display": min(display, 100), "sort": sort}
    resp = requests.get(NAVER_SHOP_ENDPOINT, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    cleaned = []
    for it in items:
        cleaned.append({
            "title": html.unescape(TAG_RE.sub("", it.get("title", ""))),
            "link": it.get("link", ""),
            "image": it.get("image", ""),
            "lprice": _to_int(it.get("lprice")),     # 최저가
            "hprice": _to_int(it.get("hprice")),     # 가격비교 상한 (없으면 None)
            "mallName": it.get("mallName", ""),
            "productId": it.get("productId", ""),
            # productType(네이버 공식): 1·2·3=일반(새)상품, 4·5·6=중고, 7~9=단종, 10~12=판매예정.
            # (2를 중고로 보면 안 됨 — 2는 '가격비교 비매칭 일반상품'. 중고는 4~6.)
            "productType": _to_int(it.get("productType")),
            "brand": it.get("brand", ""),
            "maker": it.get("maker", ""),
            "category1": it.get("category1", ""),
            "category2": it.get("category2", ""),
            "category3": it.get("category3", ""),
            "category4": it.get("category4", ""),
        })
    return cleaned


def _model_norm(it: dict) -> Optional[str]:
    """item의 정규화 모델키(공백제거+소문자). attrs 없으면 None."""
    m = _clean_attr_str((it.get("attrs") or {}).get("model"))
    return re.sub(r'\s+', '', m).lower() if m else None


def _price_stats(prices: List[int]) -> dict:
    """가격 리스트 → 분포(선형보간 백분위). 비면 전부 None."""
    prices = sorted(prices)
    n = len(prices)
    if not n:
        return {"min": None, "p25": None, "median": None,
                "p75": None, "max": None, "avg": None}

    def _pct(p: float) -> int:
        k = (n - 1) * p / 100.0
        lo = int(k)
        hi = min(lo + 1, n - 1)
        return int(round(prices[lo] + (prices[hi] - prices[lo]) * (k - lo)))

    return {"min": prices[0], "p25": _pct(25), "median": _pct(50),
            "p75": _pct(75), "max": prices[-1], "avg": int(sum(prices) / n)}


def summarize_shop(items: List[dict]) -> dict:
    """가격 분포·상위 몰/브랜드/카테고리 요약. items가 비어도 안전.
    attrs.model이 있으면 지배(최빈) 모델 기준 price와 전체 price_all을 분리해,
    키워드스터핑으로 섞인 타 제품이 헤드라인 가격대를 오염시키지 않게 한다.
    네이버 가격비교 카탈로그 대표행(mallName='네이버'/'/catalog/' 링크)은 몰 순위에서 제외."""
    all_prices = [it["lprice"] for it in items if it.get("lprice")]

    # 지배 모델(정규화 표기 최빈) 식별 → 그 모델만의 가격 분포
    dom_counter = Counter(m for m in (_model_norm(it) for it in items) if m)
    dom_key = dom_counter.most_common(1)[0][0] if dom_counter else None
    dom_prices = [it["lprice"] for it in items
                  if it.get("lprice") and _model_norm(it) == dom_key] if dom_key else []

    malls = Counter(
        it["mallName"] for it in items
        if it.get("mallName") and it["mallName"] != "네이버"
        and "/catalog/" not in (it.get("link") or ""))
    brands = Counter(it["brand"] for it in items if it["brand"])
    cats = Counter(
        " > ".join(filter(None, [it.get("category1"), it.get("category2"),
                                 it.get("category3"), it.get("category4")]))
        for it in items if it.get("category1")
    )

    return {
        "count": len(items),
        "dominant_model": dom_key,
        "price": _price_stats(dom_prices or all_prices),
        "price_all": _price_stats(all_prices),
        "top_malls": malls.most_common(5),
        "top_brands": brands.most_common(5),
        "top_categories": cats.most_common(5),
    }


# ──────────────────────────────────────────────────────────────────────────
# 1-d) L2 — 상품 제목 LLM 정규화 (브랜드/모델/색상/사이즈/SKU/키워드 추출)
#      50개 제목을 한 번에 배치 추출 → 각 item에 'attrs' 필드 부착.
# ──────────────────────────────────────────────────────────────────────────
MODEL_FAST = "gpt-4o-mini"   # 단순 추출용 — 비용↓ 속도↑


class TitleAttrs(BaseModel):
    brand: Optional[str] = Field(description="브랜드명 (없으면 null)")
    model: Optional[str] = Field(description="모델명/제품명 (예: '에어포스 1')")
    variant: Optional[str] = Field(description="에디션·시즌·세부 라인 (예: \"'07\", 'Low', 'GS')")
    colors: List[str] = Field(description="색상 키워드 (한글 우선, 없으면 빈 배열)")
    size: Optional[str] = Field(description="사이즈 (mm/US/EU 등, 표기 그대로)")
    sku: Optional[str] = Field(description="제품 코드/스타일 코드 (예: CW2288-102)")
    keywords: List[str] = Field(description="기타 속성 키워드 (한정판, 키즈, 정품, 우먼스 등)")


class TitleAttrsBatch(BaseModel):
    items: List[TitleAttrs]


TITLE_PROMPT = """다음은 쇼핑몰 상품 제목 리스트입니다.
각 제목에서 정형 속성을 추출하세요. 입력 순서와 출력 순서를 정확히 맞추고,
입력 N줄이면 정확히 N개를 출력하세요. 모르는 값은 null/빈 배열로 두세요
(빈 문자열 ''이나 문자열 'null'이 아니라 진짜 null). 추측해서 채우지 마세요.

표기 통일 규칙 (그룹화를 위해 중요):
- brand는 한국어 표기 사용 (예: 'Adidas' / 'ADIDAS' → '아디다스').
  줄 끝에 [API브랜드: X] 힌트가 있으면 그 브랜드를 사용하세요.
- model은 한국어 표기 사용 (예: 'Climacool' / 'CLIMACOOL' → '클라이마쿨';
  'Air Force 1' → '에어포스 1'). 영문·한글이 함께 있어도 항상 한글로.
- variant는 모델의 세부 라인만 (예: '레이스드', '벤타니아', 'AC').
  모델명 본체는 model로, 세부 라인만 variant로 분리.
- 한 제목에 여러 모델명이 나열된 키워드 스터핑('A 운동화 B C 1130 1090 D')이면,
  그 줄의 **대표(첫) 상품 1개**만 model로 추출하고 나머지 모델명은 무시하세요.
- colors에는 **실제 색상명만**(블랙/화이트/네이비/그레이/실버/시트락 등).
  '공용/남녀/런닝화/운동화/발볼넓은/와이드/우먼스' 등 핏·성별·용도·모델코드는 색상이 아닙니다.
- sku는 그 제목에 실제로 적힌 제품/스타일 코드만(예: 1201A967-100). 없으면 null.

[입력]
{numbered_titles}
"""


# 색상이 아닌데 제목에 섞여 colors로 오추출되는 토큰 (denylist)
NON_COLOR_TOKENS = {
    "공용", "남녀", "남녀공용", "우먼스", "맨즈", "키즈", "런닝화", "러닝화",
    "운동화", "마라톤화", "조깅화", "발볼넓은", "발편한", "와이드", "정품",
    "국내매장판", "매장판", "신상", "커플", "고프코어", "남성", "여성",
}


def _clean_attr_str(v) -> Optional[str]:
    """LLM이 JSON null 대신 'null'/'none'/'' 문자열을 반환하는 경우를 None으로."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in ("null", "none", "n/a", "nan", "") else s


def _canon_sku(sku) -> Optional[str]:
    """SKU 표기 정규화 — 공백/하이픈 제거 + 대문자. dedup·매칭용 단일 정규형."""
    s = _clean_attr_str(sku)
    if not s:
        return None
    return re.sub(r'[^A-Za-z0-9]', '', s).upper() or None


def _filter_colors(colors) -> List[str]:
    """colors에서 비색상 토큰·모델코드성 항목 제거(순서 보존, 중복 제거)."""
    out: List[str] = []
    for c in colors or []:
        c = _clean_attr_str(c)
        if not c or c in NON_COLOR_TOKENS:
            continue
        # '조그100s'·'1201A967' 같은 모델/코드성 토큰 제외 (숫자+영숫자 4+)
        if re.search(r'\d', c) and re.search(r'[A-Za-z0-9]{4,}', c):
            continue
        if c not in out:
            out.append(c)
    return out


TITLE_BATCH_SIZE = 12  # 50건 1배치는 출력 토큰 한도(16384)를 넘겨 실패 → 소배치로 분할


def _brand_only_attrs(items: List[dict]) -> None:
    """배치 실패/개수불일치 시 — API 브랜드만 채운 빈 attrs 부착(정렬 오염 방지)."""
    for it in items:
        it["attrs"] = {"brand": it.get("brand") or None, "model": None,
                       "variant": None, "colors": [], "size": None,
                       "sku": None, "sku_canon": None, "keywords": []}


def _normalize_title_batch(batch: List[dict], client: OpenAI) -> None:
    """소수 제목 1배치 정규화 → 각 item에 attrs 부착. 실패하면 brand-only로 폴백."""
    numbered = "\n".join(
        f"{i+1}. {it['title']}"
        + (f"   [API브랜드: {it['brand']}]" if it.get("brand") else "")
        for i, it in enumerate(batch))
    try:
        resp = client.chat.completions.parse(
            model=MODEL_FAST,
            temperature=0,
            messages=[{"role": "user",
                       "content": TITLE_PROMPT.format(numbered_titles=numbered)}],
            response_format=TitleAttrsBatch,
        )
        parsed = resp.choices[0].message.parsed
    except Exception as e:
        print(f"  [제목 정규화 실패-배치 {len(batch)}건] {e}")
        _brand_only_attrs(batch)
        return
    # 개수 불일치 → zip 정렬 오염 위험 → 이 배치만 brand-only
    if len(parsed.items) != len(batch):
        print(f"  [제목 정규화 경고] 배치 입력 {len(batch)} ≠ 출력 {len(parsed.items)}"
              f" → 이 배치는 API 브랜드만")
        _brand_only_attrs(batch)
        return
    for it, attrs in zip(batch, parsed.items):
        a = attrs.model_dump()
        # 1) 'null'/'' 문자열 → None
        for k in ("brand", "model", "variant", "size", "sku"):
            a[k] = _clean_attr_str(a.get(k))
        # 2) brand backfill — 네이버 API brand가 사실상 정답
        if not a.get("brand") and it.get("brand"):
            a["brand"] = it["brand"]
        # 3) colors 비색상 필터
        a["colors"] = _filter_colors(a.get("colors"))
        # 4) SKU 자기검증 — 자기 title에 없으면(공백·하이픈 무시) 배치 오염으로 보고 폐기
        sk = _canon_sku(a.get("sku"))
        if sk:
            title_canon = re.sub(r'[^A-Za-z0-9]', '', it.get("title", "")).upper()
            if sk not in title_canon and sk[:7] not in title_canon:
                a["sku"] = None
                sk = None
        # 5) SKU canonical 정규형 보존 (그룹 dedup용)
        a["sku_canon"] = sk
        it["attrs"] = a


def normalize_titles(items: List[dict], client: OpenAI) -> List[dict]:
    """각 item에 'attrs' 필드 추가. 네이버 API brand를 힌트·backfill로 사용.
    50건을 한 번에 추출하면 출력 토큰 한도로 실패하므로 소배치로 나눠 호출하고,
    배치 단위로 개수검증·SKU 자기검증·색상/null 정제·brand backfill을 수행한다."""
    if not items:
        return items
    for s in range(0, len(items), TITLE_BATCH_SIZE):
        _normalize_title_batch(items[s:s + TITLE_BATCH_SIZE], client)
    return items


# ──────────────────────────────────────────────────────────────────────────
# 1-e) L3 — 네이버 가격비교 catalog 페이지에서 스펙·평점 추출
#      productType=6(가격비교) 상위 N건만 대상. Next.js의 __NEXT_DATA__ 우선,
#      없으면 본문 텍스트로 fallback. LLM이 양쪽 모두 파싱.
# ──────────────────────────────────────────────────────────────────────────
HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', re.DOTALL,
)


def fetch_page_content(url: str, max_chars: int = 15000) -> Optional[str]:
    """카탈로그 페이지 → __NEXT_DATA__(JSON) 우선, 없으면 본문 텍스트.
    requests를 먼저 시도하고(빠름), 봇 차단(HTTP 418)·JS 렌더 페이지면 Playwright로 폴백.
    네이버 쇼핑 카탈로그(search.shopping.naver.com)는 requests에 418을 주므로 폴백이 필요하다."""
    raw = None
    try:
        resp = requests.get(url, headers=HEADERS_BROWSER, timeout=10)
        resp.raise_for_status()
        raw = resp.text
    except requests.RequestException:
        raw = None
    if not raw:  # 차단/실패 → Playwright 렌더링 폴백
        rendered = render_pages([url])
        if rendered and rendered[0]:
            raw = rendered[0]
    if not raw:
        return None

    # Next.js 페이지: 스펙 데이터가 __NEXT_DATA__의 JSON 안에 들어있음
    m = NEXT_DATA_RE.search(raw)
    if m:
        try:
            data = json.loads(m.group(1))
            payload = data.get("props", data)
            return json.dumps(payload, ensure_ascii=False)[:max_chars]
        except json.JSONDecodeError:
            pass

    # Fallback: 단순 HTML→텍스트
    stripped = re.sub(r'<script[^>]*>.*?</script>', ' ', raw, flags=re.DOTALL | re.IGNORECASE)
    stripped = re.sub(r'<style[^>]*>.*?</style>', ' ', stripped, flags=re.DOTALL | re.IGNORECASE)
    stripped = re.sub(r'<[^>]+>', ' ', stripped)
    text = html.unescape(re.sub(r'\s+', ' ', stripped)).strip()
    return text[:max_chars] or None


class SpecItem(BaseModel):
    key: str = Field(description="스펙 항목명 (예: '소재', '굽 높이', '원산지')")
    value: str = Field(description="스펙 값 (예: '천연가죽', '25mm', '베트남')")


class CatalogSpec(BaseModel):
    rating: Optional[float] = Field(description="평점 평균 0~5 (없으면 null)")
    review_count: Optional[int] = Field(description="리뷰 개수 (없으면 null)")
    specs: List[SpecItem] = Field(description="스펙표 key/value 쌍")
    description_summary: Optional[str] = Field(description="상품 설명 한 문장 요약")


SPEC_PROMPT = """다음은 상품 페이지에서 추출한 데이터입니다 (JSON 또는 텍스트).
상품의 정형 속성만 추출하세요.

- specs: '소재/사이즈/원산지/굽 높이/무게' 같은 스펙표 항목 (없으면 빈 배열)
- rating: 평점 평균 0~5 float (없으면 null)
- review_count: 리뷰 개수 정수 (없으면 null)
- description_summary: 상품 설명 한 문장 요약 (없으면 null)

광고 카피·관련 상품·navigation 메뉴·다른 상품 정보는 무시.
명시되어 있지 않으면 추측하지 말고 null/빈 배열로.

[페이지 데이터]
{content}
"""


def extract_product_spec(url: str, client: OpenAI) -> Optional[dict]:
    """페이지 1건 → CatalogSpec dict. 가져오기/파싱 실패 시 None."""
    content = fetch_page_content(url)
    if not content:
        return None
    try:
        resp = client.chat.completions.parse(
            model=MODEL_FAST,
            temperature=0,
            messages=[{"role": "user",
                       "content": SPEC_PROMPT.format(content=content)}],
            response_format=CatalogSpec,
        )
        return resp.choices[0].message.parsed.model_dump()
    except Exception as e:
        print(f"    [스펙 추출 실패] {url}: {e}")
        return None


def is_catalog_item(it: dict) -> bool:
    """네이버 가격비교 카탈로그 대표행인가 — link의 '/catalog/' 포함으로 판별.
    (productType 코드는 응답에 6이 실제로 없는 등 신뢰 불가 → link 기반이 견고)."""
    return "/catalog/" in (it.get("link") or "")


def enrich_with_specs(items: List[dict], client: OpenAI, top_n: int = 5) -> List[dict]:
    """가격비교 카탈로그(catalog 링크) 상위 top_n건의 스펙을 가져옴."""
    targets = [it for it in items if is_catalog_item(it)][:top_n]
    out = []
    for it in targets:
        spec = extract_product_spec(it["link"], client)
        out.append({
            "productId": it.get("productId"),
            "title": it.get("title"),
            "link": it.get("link"),
            "lprice": it.get("lprice"),
            "spec": spec,
        })
        time.sleep(0.5)  # 매너 대기
    return out


# ──────────────────────────────────────────────────────────────────────────
# 1-f) L4 — 브랜드 공식몰에서 상품 정보 가져오기 (Brave Search + Playwright + LLM)
#      attrs.brand + attrs.model 로 공식몰 검색 → Playwright로 렌더링 → LLM 추출.
#      BRAVE_SEARCH_API_KEY 없으면 자동 스킵, Playwright 없으면 requests로 폴백.
#      Brave 무료 한도: 2,000건/월, 1 qps (rate limit).
# ──────────────────────────────────────────────────────────────────────────
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# 브랜드명 → 공식몰 도메인. 키는 소문자/한글 모두 매칭.
# 신규 브랜드는 여기에 추가하거나, 매핑 없으면 LLM 검색 쿼리에 '공식'을 붙여 fallback.
BRAND_DOMAINS = {
    "나이키": "nike.com",
    "nike": "nike.com",
    "아디다스": "adidas.co.kr",
    "adidas": "adidas.co.kr",
    "뉴발란스": "nbkorea.com",
    "new balance": "nbkorea.com",
    "newbalance": "nbkorea.com",
    "푸마": "kr.puma.com",
    "puma": "kr.puma.com",
    "컨버스": "converse.co.kr",
    "converse": "converse.co.kr",
    "반스": "vans.co.kr",
    "vans": "vans.co.kr",
    "리복": "reebok.co.kr",
    "reebok": "reebok.co.kr",
    "아식스": "asics.co.kr",
    "asics": "asics.co.kr",
    "호카": "hoka.com",
    "hoka": "hoka.com",
    "살로몬": "salomon.com",
    "salomon": "salomon.com",
    "휠라": "fila.co.kr",
    "fila": "fila.co.kr",
    "노스페이스": "thenorthfacekorea.co.kr",
    "the north face": "thenorthfacekorea.co.kr",
    # 패션
    "자라": "zara.com/kr",
    "zara": "zara.com/kr",
    "유니클로": "uniqlo.com/kr",
    "uniqlo": "uniqlo.com/kr",
    "h&m": "hm.com/ko_kr",
    "에이치앤엠": "hm.com/ko_kr",
    "무신사": "musinsa.com",
    "musinsa": "musinsa.com",
}

# 브랜드 자체 검색 URL 템플릿. {q}에 모델명이 URL-인코딩되어 들어감.
# 매핑이 있으면 Brave Search보다 우선 시도 → 최신/한정판 매칭률 ↑.
BRAND_SEARCH_URLS = {
    "나이키": "https://www.nike.com/kr/w?q={q}",
    "nike": "https://www.nike.com/kr/w?q={q}",
    "아디다스": "https://www.adidas.co.kr/search?q={q}",
    "adidas": "https://www.adidas.co.kr/search?q={q}",
    "뉴발란스": "https://www.nbkorea.com/search/?q={q}",
    "new balance": "https://www.nbkorea.com/search/?q={q}",
    "newbalance": "https://www.nbkorea.com/search/?q={q}",
    "푸마": "https://kr.puma.com/search?q={q}",
    "puma": "https://kr.puma.com/search?q={q}",
    "컨버스": "https://www.converse.co.kr/search?q={q}",
    "converse": "https://www.converse.co.kr/search?q={q}",
    "아식스": "https://asics.co.kr/search?q={q}",
    "asics": "https://asics.co.kr/search?q={q}",
    "자라": "https://www.zara.com/kr/ko/search?searchTerm={q}",
    "zara": "https://www.zara.com/kr/ko/search?searchTerm={q}",
    "유니클로": "https://www.uniqlo.com/kr/ko/search?q={q}",
    "uniqlo": "https://www.uniqlo.com/kr/ko/search?q={q}",
    "h&m": "https://www2.hm.com/ko_kr/search-results.html?q={q}",
    "에이치앤엠": "https://www2.hm.com/ko_kr/search-results.html?q={q}",
    "무신사": "https://www.musinsa.com/search/musinsa/integration?q={q}",
    "musinsa": "https://www.musinsa.com/search/musinsa/integration?q={q}",
}


def get_brand_search_template(brand: Optional[str]) -> Optional[str]:
    """브랜드명 → 자체 검색 URL 템플릿 (없으면 None)."""
    if not brand:
        return None
    b = brand.strip().lower()
    if b in BRAND_SEARCH_URLS:
        return BRAND_SEARCH_URLS[b]
    for k, v in BRAND_SEARCH_URLS.items():
        if k in b or b in k:
            return v
    return None


def get_brand_domain(brand: Optional[str]) -> Optional[str]:
    """브랜드명 → 공식 도메인 (매핑 없으면 None)."""
    if not brand:
        return None
    b = brand.strip().lower()
    if b in BRAND_DOMAINS:
        return BRAND_DOMAINS[b]
    # 느슨한 매칭: '나이키 코리아' 같은 변형
    for k, v in BRAND_DOMAINS.items():
        if k in b or b in k:
            return v
    return None


ANCHOR_RE = re.compile(
    r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)


def extract_anchors_from_html(raw: str, domain_filter: str = "",
                              max_anchors: int = 50,
                              base_url: str = "") -> List[dict]:
    """HTML → [{href, text}] 절대 URL만, 중복 제거. domain_filter 주어지면 그 도메인만.
    base_url 주어지면 루트상대 경로(/p/AKR_...)를 절대 URL로 변환 — 많은 브랜드몰
    (ASICS 등)이 상품 링크를 상대경로로 두기 때문에, 이게 없으면 drill-down이 전부 실패한다."""
    from urllib.parse import urljoin
    out = []
    seen = set()
    for href, inner in ANCHOR_RE.findall(raw):
        href = href.strip()
        if base_url and href.startswith("/"):
            href = urljoin(base_url, href)
        if not href.startswith(("http://", "https://")):
            continue
        if domain_filter and domain_filter not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        text = re.sub(r'<[^>]+>', ' ', inner)
        text = html.unescape(re.sub(r'\s+', ' ', text)).strip()
        out.append({"href": href, "text": text[:160]})
        if len(out) >= max_anchors:
            break
    return out


class ProductURLPick(BaseModel):
    url: Optional[str] = Field(description="검색어와 가장 잘 매칭되는 상품 상세 페이지의 절대 URL")
    reason: Optional[str] = Field(description="왜 이 URL을 골랐는지 (또는 매칭 없는 이유)")


URL_PICK_PROMPT = """다음은 브랜드 공식몰 검색결과 페이지의 링크 목록과 본문 텍스트입니다.
검색어(원본 상품명 또는 제품코드 SKU): "{search_query}"

링크 중 검색어와 가장 잘 매칭되는 **개별 상품 상세 페이지 URL** 1건을 선택하세요.
- 카테고리/검색결과/홈/장바구니/계정 등의 페이지 URL은 제외
- 검색어가 제품코드(SKU)인 경우: URL 또는 snippet에 그 코드가 **정확히** 포함되어야 함.
  비슷한 코드(예: JQ8739 vs JQ8387)는 다른 상품이므로 거부 → url=null
- 검색어가 상품명인 경우: 상품명이 검색어와 부분이라도 매칭되어야 함
- 매칭되는 게 없으면 url=null

[링크 목록]
{anchors}

[본문 텍스트 (참고용)]
{text}
"""


def _sanitize_picked_url(url: Optional[str]) -> Optional[str]:
    """LLM이 JSON null 대신 문자열 'null'/'none'/'' 반환하는 경우 처리."""
    if not url:
        return None
    s = url.strip()
    if s.lower() in ("null", "none", "n/a", "nan", ""):
        return None
    if not s.startswith(("http://", "https://")):
        return None
    return s


def find_product_url_via_brand_search(brand: str, search_query: str,
                                      client: OpenAI) -> Optional[str]:
    """브랜드 자체 검색 URL 렌더링 → LLM이 첫 상품 detail URL 선택.
    search_query는 LLM 정규화된 model이 아닌 원본 상품명(Naver title)을 권장."""
    template = get_brand_search_template(brand)
    if not template:
        return None
    from urllib.parse import quote
    search_url = template.format(q=quote(search_query))
    rendered = render_pages([search_url])
    if not rendered or not rendered[0]:
        return None
    raw = rendered[0]
    domain = get_brand_domain(brand) or ""
    # 도메인은 'nike.com/kr' 같이 path 포함일 수 있으므로 host 부분만 사용
    host = domain.split("/")[0] if domain else ""
    anchors = extract_anchors_from_html(raw, domain_filter=host, max_anchors=80,
                                        base_url=search_url)
    if not anchors:
        return None
    # 카테고리/네비/계정 URL 사전 제외 (LLM picker가 잘못 고를 여지 차단)
    anchors = [a for a in anchors if not CATALOG_URL_RE.search(a["href"])]
    product_anchors = [
        a for a in anchors if PRODUCT_URL_PATTERNS.search(a["href"])
    ]
    if product_anchors and len(product_anchors) == 1:
        return product_anchors[0]["href"]
    candidates = product_anchors or anchors
    if not candidates:
        return None
    anchor_lines = "\n".join(
        f"- {a['href']} | {a['text']}" for a in candidates[:40])
    text = html_to_text(raw, max_chars=4000)
    picked = None
    try:
        resp = client.chat.completions.parse(
            model=MODEL_FAST,
            temperature=0,
            messages=[{"role": "user", "content": URL_PICK_PROMPT.format(
                search_query=search_query, anchors=anchor_lines, text=text)}],
            response_format=ProductURLPick,
        )
        picked = _sanitize_picked_url(resp.choices[0].message.parsed.url)
    except Exception as e:
        print(f"    [브랜드 검색 URL 선택 실패] {e}")
    # picker가 카테고리/패턴 미매칭 URL을 골랐으면 product_anchors 첫번째로 폴백
    if picked and PRODUCT_URL_PATTERNS.search(picked) \
            and not CATALOG_URL_RE.search(picked):
        return picked
    if product_anchors:
        return product_anchors[0]["href"]
    return picked  # product 패턴 매칭 anchor가 없으면 picker 결과라도 반환


def brave_search(query: str, api_key: str, num: int = 5) -> List[dict]:
    """Brave Search Web API → [{title, link, snippet}]. 실패 시 빈 배열.
    Brave도 'site:' 연산자 지원."""
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
    }
    params = {
        "q": query, "count": min(num, 20),
        "country": "KR", "search_lang": "ko",
    }
    try:
        resp = requests.get(BRAVE_SEARCH_ENDPOINT, headers=headers,
                            params=params, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"    [Brave Search 오류] {e}")
        return []
    web = resp.json().get("web", {}) or {}
    return [{"title": it.get("title", ""), "link": it.get("url", ""),
             "snippet": it.get("description", "")}
            for it in web.get("results", [])]


# product detail URL 패턴 — 카테고리/홈 URL 제외용 휴리스틱
PRODUCT_URL_PATTERNS = re.compile(
    r'(?:'
    r'-p\d+\.html'             # ZARA, H&M, Pull&Bear
    r'|/products?/[^/?]+'      # Uniqlo, Puma, NB 등
    r'|/t/[^/]+/[A-Z0-9-]+'    # Nike
    r'|/[A-Z0-9]{5,}\.html'    # Adidas 일부
    r'|/p/[A-Z0-9_][A-Z0-9_-]{4,}'  # Asics (/p/AKR_112619334-001 등)
    r')',
    re.IGNORECASE,
)


def _normalize_sku_variants(sku: str) -> List[str]:
    """SKU 표기 변형 후보를 생성 — 검색 엔진이 잘 잡도록 여러 형태로 시도.
    예: '2249/137/926' → ['2249/137/926', '2249137926', '2249/137', '2249137']"""
    sku = sku.strip()
    out = [sku]
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', sku)
    if cleaned and cleaned not in out:
        out.append(cleaned)
    parts = re.split(r'[/\-_\s]+', sku)
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        # 끝 segment는 보통 색상/사이즈 코드 → 제거하면 모델 단위로 매칭
        first_two = "/".join(parts[:2])
        if first_two not in out:
            out.append(first_two)
        joined = "".join(parts[:2])
        if joined not in out:
            out.append(joined)
    return out


def _resolve_zara_product_url(sku: str) -> Optional[str]:
    """ZARA 전용 — SKU만으로 product URL 직접 구성.
    `/kr/ko/-p{SKU}.html` 형태가 301 redirect를 주는데, Location 헤더에
    실제 product URL(slug 포함)이 들어있음. Brave 인덱스에 없는 신상품·한정판도
    SKU만 알면 잡을 수 있음."""
    from urllib.parse import urljoin
    for variant in _normalize_sku_variants(sku):
        digits = re.sub(r'[^0-9]', '', variant)
        if len(digits) < 6:  # 너무 짧은 건 가짜 SKU
            continue
        # ZARA SKU는 보통 7~8자리. 다양한 패딩으로 시도.
        for padded in [digits, digits.zfill(8), "0" + digits]:
            url = f"https://www.zara.com/kr/ko/-p{padded}.html"
            try:
                resp = requests.get(
                    url, headers=HEADERS_BROWSER,
                    allow_redirects=False, timeout=8,
                )
            except requests.RequestException:
                continue
            if resp.status_code in (301, 302):
                loc = resp.headers.get("Location", "")
                if loc and "-p" in loc and ".html" in loc:
                    return urljoin(url, loc)
    return None


def _filter_country_local(results: List[dict], path_prefix: str) -> List[dict]:
    """결과 URL 중 path_prefix(예: 'zara.com/kr')와 일치하는 것만.
    글로벌 브랜드의 타국가 사이트(.fr/.uk/.us)를 자동 제외."""
    if not path_prefix or "/" not in path_prefix:
        return results
    local = [r for r in results if path_prefix in r.get("link", "")]
    return local or results  # 한국 결과 없으면 원본 반환 (포기 X)


def _sku_matches_result(variant: str, result: dict) -> bool:
    """결과 URL/snippet에 SKU variant가 그대로 포함되어 있는지.
    검색 엔진이 유사 SKU 페이지를 fuzzy-match로 반환하는 경우(JQ8739 검색 →
    JQ8387 페이지 반환)를 잡아내기 위한 게이트."""
    blob = (result.get("link", "") + " " + result.get("snippet", "")).lower()
    return variant.lower() in blob


def _find_via_sku(brand: str, sku: str, api_key: str,
                  client: OpenAI) -> Optional[str]:
    """SKU 기반 Brave Search. 여러 SKU 변형으로 시도, product URL 패턴 우선.
    BRAND_DOMAINS에 path가 포함된 경우 (zara.com/kr) 그 국가 사이트만 채택.
    Brave가 유사 SKU 페이지를 1순위로 반환하는 경우를 막기 위해, URL/snippet에
    그 variant가 실제로 들어 있는 결과만 candidates로 채택한다."""
    domain = get_brand_domain(brand)
    host = domain.split("/")[0] if domain else None

    for variant in _normalize_sku_variants(sku):
        query = f"{variant} site:{host}" if host else f"{brand} {variant} 공식"
        results = brave_search(query, api_key, num=5)
        if not results:
            continue
        # 국가/언어 path 필터 (zara.com/kr 같이 path 매핑이 있을 때만)
        results = _filter_country_local(results, domain or "")
        # SKU variant 실매칭 게이트 — 유사 SKU 페이지 반환 사고 차단
        matched = [r for r in results if _sku_matches_result(variant, r)]
        if not matched:
            continue
        # product URL 패턴 매칭되는 것 우선
        product_results = [
            r for r in matched
            if PRODUCT_URL_PATTERNS.search(r.get("link", ""))
        ]
        candidates = product_results or matched
        if len(candidates) == 1:
            return candidates[0]["link"]
        # LLM picker — SKU 매칭으로 명시
        anchor_lines = "\n".join(
            f"- {r['link']} | {r.get('snippet', '')[:140]}"
            for r in candidates
        )
        try:
            resp = client.chat.completions.parse(
                model=MODEL_FAST,
                temperature=0,
                messages=[{"role": "user", "content": URL_PICK_PROMPT.format(
                    search_query=f"브랜드 {brand}, 제품코드 SKU '{variant}'",
                    anchors=anchor_lines, text="")}],
                response_format=ProductURLPick,
            )
            picked = _sanitize_picked_url(resp.choices[0].message.parsed.url)
            if picked:
                return picked
        except Exception as e:
            print(f"    [SKU 후보 선택 실패] {e}")
        # picker 실패해도 product URL 매칭이 있으면 top-1 채택
        if product_results:
            return product_results[0]["link"]
    return None


def find_official_url(brand: str, search_query: str, api_key: str,
                      client: OpenAI, sku: Optional[str] = None) -> Optional[str]:
    """브랜드+원본 상품명 (+선택적 SKU) → 공식몰 상품 URL.
    0a) ZARA + SKU → URL 직접 구성 (Brave 인덱스 우회, 가장 정확)
    0b) SKU 있으면 Brave Search SKU 기반
    1)  브랜드 자체 검색 URL 매핑이 있으면 시도
    2)  Brave Search 폴백 (top-5 → LLM이 product URL 선택)."""
    domain = get_brand_domain(brand) or ""

    # 0a) ZARA 전용 — SKU로 URL 직접 구성
    if sku and "zara.com" in domain:
        url = _resolve_zara_product_url(sku)
        if url:
            return url

    # 0a-2) ASICS 전용 — SPA라 Brave가 존재하지 않는 /p/ 코드를 1순위로 주는 일이 잦음.
    #       검색 페이지를 렌더해 실제 상품코드(AKR_)를 추출 → /p/ URL 구성 (가장 안정적).
    if "asics.co.kr" in domain:
        url = _resolve_asics_product_url(search_query, api_key)
        if url:
            return url

    # 0b) SKU 기반 Brave Search
    if sku:
        url = _find_via_sku(brand, sku, api_key, client)
        if url:
            return url

    # 1) 브랜드 자체 검색
    if get_brand_search_template(brand):
        url = find_product_url_via_brand_search(brand, search_query, client)
        if url:
            return url

    # 2) Brave Search 폴백 — 카테고리/홈 URL 회피 위해 top-5 중 LLM이 선택
    domain = get_brand_domain(brand)
    host = domain.split("/")[0] if domain else None
    query = f"{search_query} site:{host}" if host else f"{search_query} 공식"
    results = brave_search(query, api_key, num=5)
    if not results:
        return None
    # 국가/언어 path 필터 (zara.com/kr 같이 path 매핑이 있을 때만)
    results = _filter_country_local(results, domain or "")
    if len(results) == 1:
        return results[0]["link"]

    # 후보를 anchor 포맷으로 LLM에 전달 → product URL만 골라냄
    candidates = "\n".join(
        f"- {r['link']} | {r.get('snippet', '')[:140]}"
        for r in results
    )
    try:
        resp = client.chat.completions.parse(
            model=MODEL_FAST,
            temperature=0,
            messages=[{"role": "user", "content": URL_PICK_PROMPT.format(
                search_query=search_query, anchors=candidates, text="")}],
            response_format=ProductURLPick,
        )
        picked = _sanitize_picked_url(resp.choices[0].message.parsed.url)
        if picked:
            return picked
    except Exception as e:
        print(f"    [Brave 후보 선택 실패] {e}")
    # 못 고르면 top-1 (Brave 1순위 결과)
    return results[0]["link"]


def html_to_text(raw: str, max_chars: int = 15000) -> str:
    """HTML → 일반 텍스트 (script/style/태그 제거). 빈 문자열일 수 있음."""
    raw = re.sub(r'<script[^>]*>.*?</script>', ' ', raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r'<style[^>]*>.*?</style>', ' ', raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r'<[^>]+>', ' ', raw)
    text = html.unescape(re.sub(r'\s+', ' ', raw)).strip()
    return text[:max_chars]


def _dismiss_common_consent_banners(page) -> None:
    """OneTrust/Cookiebot 등 흔한 동의 배너 처리. 실패해도 silent."""
    selectors = [
        "#onetrust-accept-btn-handler",          # OneTrust
        "#CybotCookiebotDialogBodyButtonAccept", # Cookiebot
        'button[aria-label*="Accept" i]',
        'button:has-text("모두 수락")',
        'button:has-text("동의")',
    ]
    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=2000)
            page.wait_for_timeout(500)
            return
        except Exception:
            continue
    # 마지막 수단: 흔히 쓰이는 오버레이를 DOM에서 강제 제거
    try:
        page.evaluate(
            "document.querySelectorAll("
            "'#onetrust-consent-sdk, .onetrust-pc-dark-filter, "
            "#CybotCookiebotDialog, [id*=cookie-banner]'"
            ").forEach(e => e.remove())"
        )
    except Exception:
        pass


def _expand_zara_product_page(page) -> None:
    """ZARA 상품 페이지의 spec accordion 펼치기 (혼용률·세탁·원산지·사이즈)."""
    for sel in [
        '[data-qa-action="show-extra-detail"]',   # 혼용률·세탁·원산지 모달
        '[data-qa-action="size-guide-accordion"]', # 사이즈 가이드
    ]:
        try:
            page.locator(sel).first.click(timeout=3000)
            page.wait_for_timeout(1800)
        except Exception:
            pass  # 버튼 없거나 이미 열림


def _expand_adidas_product_page(page) -> None:
    """Adidas 상품 페이지의 accordion 펼치기 (정보·세부정보·리뷰).
    Adidas는 상세 정보를 하단 accordion에 숨겨둠 → 클릭으로 열어 DOM에 노출시켜야
    이후 page.content()에 포함되어 LLM 추출 대상이 됨."""
    # 1) 페이지 하단까지 점진적 스크롤 → lazy 마운트되는 accordion 확보
    try:
        for _ in range(4):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            page.wait_for_timeout(300)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:
        pass

    # 2) 알려진 Adidas accordion (data-auto-id) — 정보/세부정보/리뷰/사이즈
    #    버전마다 id가 다를 수 있어 여러 후보 시도. 내부 button 우선, 없으면 wrapper.
    auto_ids = [
        "product-information-accordion",
        "specifications-accordion",
        "reviews-accordion",
        "size-guide-accordion",
        "itemDescription",
        "product-description",
        "reviewer-accordion-title",
    ]
    for aid in auto_ids:
        for sel in (
            f'[data-auto-id="{aid}"] button',
            f'[data-auto-id="{aid}"]',
        ):
            try:
                loc = page.locator(sel).first
                loc.scroll_into_view_if_needed(timeout=1500)
                loc.click(timeout=1500)
                page.wait_for_timeout(500)
                break  # 같은 id에 대해서는 한 번 성공하면 다음 id로
            except Exception:
                continue

    # 3) 텍스트 라벨 fallback — data-auto-id 매칭 실패 대비 (한국어/영문)
    for label in ["정보", "세부정보", "리뷰",
                  "Description", "Specifications", "Reviews"]:
        try:
            btn = page.get_by_role(
                "button", name=re.compile(label, re.IGNORECASE)).first
            btn.scroll_into_view_if_needed(timeout=1500)
            btn.click(timeout=1500)
            page.wait_for_timeout(500)
        except Exception:
            pass

    # 4) accordion 열린 뒤 콘텐츠가 fetch될 시간 부여
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass


def _expand_asics_category_page(page) -> None:
    """ASICS 카테고리/컬렉션(/c/) 페이지의 상품 그리드 lazy-load.
    ASICS는 SPA라 상품 카드(AKR_ 코드)가 스크롤 시 마운트됨 → 스크롤로 노출시켜야
    이후 page.content()에서 상품코드를 추출해 /p/ 상세 URL을 구성할 수 있다."""
    try:
        for _ in range(6):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            page.wait_for_timeout(400)
        page.wait_for_timeout(1000)
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
    except Exception:
        pass


# 도메인별 페이지 후처리 hook. URL이 해당 키를 substring으로 포함하면 적용.
PAGE_POST_LOAD_HOOKS = {
    "zara.com": _expand_zara_product_page,
    "adidas.co.kr": _expand_adidas_product_page,
    "asics.co.kr": _expand_asics_category_page,
}


def render_pages(urls: List[str], timeout_ms: int = 20000) -> List[Optional[str]]:
    """Playwright(있으면, stealth 우선)로 일괄 렌더링, 없으면 requests 폴백.
    브라우저 1회 띄워 모든 URL에 재사용 → cold start 비용 분산.
    각 URL 도메인이 PAGE_POST_LOAD_HOOKS에 있으면 accordion 펼치기 등 후처리."""
    if not urls:
        return []

    # 1) Playwright 임포트
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        print("    [Playwright 미설치 → requests 폴백] "
              "정확한 결과를 위해 'pip install playwright && playwright install chromium' 권장")
        out: List[Optional[str]] = []
        for u in urls:
            try:
                resp = requests.get(u, headers=HEADERS_BROWSER, timeout=10)
                resp.raise_for_status()
                out.append(resp.text)
            except requests.RequestException:
                out.append(None)
            time.sleep(0.3)
        return out

    # 2) Stealth 모듈 (없으면 기본 Playwright)
    use_stealth = False
    try:
        from playwright_stealth import Stealth  # type: ignore
        use_stealth = True
    except ImportError:
        print("    [Playwright stealth 미설치 → 봇 차단 사이트는 막힐 수 있음] "
              "권장: pip install playwright-stealth")

    def _do_render(p, urls_):
        results_: List[Optional[str]] = []
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS_BROWSER["User-Agent"],
            viewport={"width": 1280, "height": 900},
            locale="ko-KR",
        )
        for u in urls_:
            page = ctx.new_page()
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                _dismiss_common_consent_banners(page)
                # 도메인별 후처리 (accordion 펼치기 등)
                for domain_key, hook in PAGE_POST_LOAD_HOOKS.items():
                    if domain_key in u:
                        try:
                            hook(page)
                        except Exception as e:
                            print(f"    [{domain_key} hook 실패] {e}")
                        break
                results_.append(page.content())
            except Exception as e:
                print(f"    [Playwright 렌더링 실패] {u}: {e}")
                results_.append(None)
            finally:
                page.close()
        browser.close()
        return results_

    try:
        if use_stealth:
            with Stealth().use_sync(sync_playwright()) as p:
                return _do_render(p, urls)
        else:
            with sync_playwright() as p:
                return _do_render(p, urls)
    except Exception as e:
        print(f"    [Playwright 초기화 실패] {e}")
        return [None] * len(urls)


class OfficialInfo(BaseModel):
    is_product_page: bool = Field(description="이 페이지가 검색한 상품의 실제 상세 페이지인지")
    official_name: Optional[str] = Field(description="공식 페이지에 표시된 상품명")
    list_price: Optional[int] = Field(description="정가 KRW 정수 (할인가 아님)")
    available_colors: List[str] = Field(description="선택 가능한 색상")
    available_sizes: List[str] = Field(description="선택 가능한 사이즈 (예: XS, S, M / 230, 240)")
    materials: List[str] = Field(description="소재 키워드 (예: 큐프로, 폴리에스터, 가죽)")
    material_composition: List[SpecItem] = Field(
        description="혼용률 — 부위별 소재 비율 (예: key='Outer', value='큐프로 100%')")
    washing_instructions: List[str] = Field(
        description="세탁/관리 지침 (예: '석유계 드라이클리닝만 가능', '건조기 사용 금지')")
    origin: Optional[str] = Field(description="원산지/제조국 (예: '모로코', '베트남')")
    size_chart: List[SpecItem] = Field(
        description="사이즈별 측정값 (예: key='M / 가슴둘레', value='86cm')")
    description: Optional[str] = Field(description="객관적 상품 설명 한 문장 (마케팅 카피 제외)")
    release_date: Optional[str] = Field(description="출시일 YYYY-MM-DD (없으면 null)")
    sku_codes: List[str] = Field(description="페이지에 나타난 SKU/스타일 코드")
    features: List[str] = Field(
        description="세부 특징 bullet (예: '일반 핏', '끈 묶음', '갑피: 스웨이드 100%'). "
                    "혼용률·소재처럼 더 구체적 필드에 매핑되는 항목은 중복 추가 금지.")
    rating: Optional[float] = Field(description="평균 별점 (5점 만점 실수, 없으면 null)")
    review_count: Optional[int] = Field(description="총 리뷰 개수 (없으면 null)")


OFFICIAL_PROMPT = """다음은 브랜드 공식몰에서 가져온 페이지 데이터입니다.
검색 대상: 브랜드 '{brand}', 모델 '{model}'
원본 상품명(참고): "{source_title}"

먼저 이 페이지가 검색한 상품의 실제 상세 페이지인지 판단(is_product_page).
- 페이지의 official_name 또는 URL이 검색 대상(원본 상품명 기준)과 **명백히 다른 모델**이면 false.
  예: 검색은 'Climacool 러닝화'인데 페이지가 'Gazelle Indoor' 또는 '야구 모자' → false.
  모델명이 한 단어라도 다르면(라인 변경: Climacool vs Gazelle, Air Force vs Dunk) 거부.
- 카테고리/검색결과/홈/다른 상품이면 false, 모든 값 null·빈 배열
- 일치하는 상품 페이지면 true, 명시된 정보만 추출 (추측 금지)

추출 항목:
- official_name: 공식 페이지에 표시된 상품명
- list_price: 정가 (KRW 정수, 할인가 아님)
- available_colors / available_sizes: 선택 가능한 옵션 리스트.
  단 available_colors에는 실제 색상명만(블랙/화이트 등). '1 Color','4 Colors','N개 색상'
  같은 **색상 개수 라벨/배지**는 색상이 아니므로 절대 넣지 마세요(이건 카테고리 목록 페이지 신호).
- materials: 본문/메타에 언급된 소재 키워드
- material_composition: 혼용률 표 (부위별 %). 페이지에 정확히 명시된 경우만
- washing_instructions: 세탁/관리 지침 항목 리스트
- origin: 원산지/제조국 ('모로코에서 제조' → '모로코')
- size_chart: 사이즈별 측정값 (가슴둘레/총장 등). 없으면 빈 배열
- description: 객관적 상품 설명 한 문장 (마케팅 카피 제외)
- release_date: 출시일 YYYY-MM-DD (없으면 null)
- sku_codes: 페이지에 나타난 SKU/스타일 코드
- features: '세부정보' accordion의 특징 bullet (핏/클로저/내부 구조 등).
  단, materials·material_composition·sku_codes·origin에 이미 매핑되는 항목은 중복 금지.
- rating: 리뷰 영역에 표시된 평균 별점 (5점 만점 기준 실수). 없으면 null
- review_count: 총 리뷰 개수. 없으면 null

[페이지 데이터]
{content}
"""


def extract_official_info(brand: str, model: str, content: str,
                          client: OpenAI,
                          source_title: str = "") -> Optional[dict]:
    """렌더링된 페이지 → OfficialInfo dict. 실패 시 None.
    source_title은 LLM 정규화 전 원본 상품명 — model이 오타·축약된 경우에도
    실제로 검색한 상품이 무엇인지 LLM에게 추가 단서를 준다."""
    # 빈/얇은 페이지(봇 차단·죽은 URL)면 LLM이 그럴듯한 가짜 스펙(정가·소재·원산지)을
    # 환각하므로 추출 자체를 스킵한다. 정상 상품 페이지 텍스트는 수천 자 이상.
    if not content or len(content.strip()) < 200:
        return None
    try:
        resp = client.chat.completions.parse(
            model=MODEL_FAST,
            temperature=0,
            messages=[{"role": "user",
                       "content": OFFICIAL_PROMPT.format(
                           brand=brand, model=model,
                           source_title=source_title or model,
                           content=content[:15000])}],
            response_format=OfficialInfo,
        )
        info = resp.choices[0].message.parsed.model_dump()
        # 카테고리 그리드의 'N Colors'/'N개 색상' 개수 배지는 색상명이 아니므로 제거
        info["available_colors"] = [
            c for c in (info.get("available_colors") or [])
            if not re.match(r'^\s*\d+\s*colors?\s*$', str(c), re.IGNORECASE)
            and not re.match(r'^\s*\d+\s*개?\s*(?:색상|컬러)\s*$', str(c))
        ]
        return info
    except Exception as e:
        print(f"    [공식 정보 추출 실패] {e}")
        return None


# 카탈로그/네비게이션 URL — drill-down 시 제외 (모두 절대/상대 둘 다 매칭)
CATALOG_URL_RE = re.compile(
    r'/(c|category|categories|cat|list|search|collection|collections|brand|brands'
    r'|cart|account|login|signup|customer|review|reviews|event|coupon)(?:/|\?|$|#)',
    re.IGNORECASE,
)


# SPA 카탈로그(ASICS 등)는 상품 detail 링크가 정적 anchor로 없고 코드만 스크립트에 존재.
# 도메인별 상품코드 패턴 → /p/ 상세 URL 직접 구성.
ASICS_PRODUCT_CODE_RE = re.compile(r'AKR_[0-9]{6,}-[0-9]{3}')


def _construct_product_url_from_codes(raw: str, brand: str) -> Optional[str]:
    """카탈로그 페이지 HTML에서 상품코드를 추출해 detail URL을 구성(anchor 없을 때 폴백).
    현재 ASICS(/p/AKR_...) 지원 — 페이지에 가장 많이 등장하는 코드를 대표로 선택."""
    domain = get_brand_domain(brand) or ""
    if "asics.co.kr" in domain:
        codes = ASICS_PRODUCT_CODE_RE.findall(raw)
        if codes:
            code = Counter(codes).most_common(1)[0][0]
            return f"https://www.asics.co.kr/p/{code}"
    return None


def _resolve_asics_product_url(search_query: str, api_key: str) -> Optional[str]:
    """ASICS 전용 — Brave로 ASICS 페이지 후보를 찾아 /c/ 컬렉션을 우선 렌더 → 상품코드(AKR_)
    추출 → /p/ 상세 URL 구성. ASICS는 SPA라 /p/ anchor가 정적 DOM에 없고, Brave가 존재하지
    않는 /p/ 코드(렌더 시 빈 39바이트)를 1순위로 주기도 하므로, 코드가 풍부한 /c/ 컬렉션
    페이지에서 실제 코드를 뽑는 것이 가장 안정적이다."""
    if not api_key:
        return None
    # 브랜드 토큰 제거 → 모델명 위주 검색 ('아식스 조그 100S' → '조그 100S')
    q = re.sub(r'아식스|asics', '', search_query, flags=re.IGNORECASE).strip() or search_query
    results = brave_search(f"{q} site:asics.co.kr", api_key, num=5)
    urls = [r.get("link", "") for r in results if r.get("link")]
    # 코드가 풍부한 /c/ 컬렉션을 먼저, goods/search(코드 0개)는 맨 뒤로
    urls.sort(key=lambda u: 0 if "/c/" in u else (2 if "/goods/search" in u else 1))
    for u in urls[:3]:
        rendered = render_pages([u])
        if rendered and rendered[0]:
            purl = _construct_product_url_from_codes(rendered[0], "아식스")
            if purl:
                return purl
    return None


def _drill_down_from_catalog(raw: str, brand: str, search_query: str,
                              client: OpenAI, base_url: str = "") -> Optional[str]:
    """카탈로그/검색결과 페이지 HTML에서 product detail URL 1건을 골라 반환.
    - 같은 브랜드 도메인 anchor만 후보 (상대경로는 base_url로 절대화)
    - PRODUCT_URL_PATTERNS 매칭 + 카탈로그 URL 제외 우선
    - 후보가 둘 이상이면 LLM picker가 search_query와 가장 부합하는 URL 선택"""
    domain = get_brand_domain(brand) or ""
    host = domain.split("/")[0] if domain else ""
    anchors = extract_anchors_from_html(raw, domain_filter=host, max_anchors=80,
                                        base_url=base_url)
    if not anchors:
        return None
    product_anchors = [
        a for a in anchors
        if PRODUCT_URL_PATTERNS.search(a["href"])
        and not CATALOG_URL_RE.search(a["href"])
    ]
    if not product_anchors:
        # SPA(ASICS 등): /p/ anchor가 없으면 페이지 내 상품코드로 detail URL 구성
        return _construct_product_url_from_codes(raw, brand)
    if len(product_anchors) == 1:
        return product_anchors[0]["href"]
    anchor_lines = "\n".join(
        f"- {a['href']} | {a['text']}" for a in product_anchors[:30])
    try:
        resp = client.chat.completions.parse(
            model=MODEL_FAST,
            temperature=0,
            messages=[{"role": "user", "content": URL_PICK_PROMPT.format(
                search_query=search_query, anchors=anchor_lines, text="")}],
            response_format=ProductURLPick,
        )
        return _sanitize_picked_url(resp.choices[0].message.parsed.url)
    except Exception as e:
        print(f"    [drill-down picker 실패] {e}")
        return None


def enrich_with_official_site(items: List[dict], client: OpenAI,
                              brave_key: str,
                              top_n: int = 3, keyword: str = "") -> List[dict]:
    """attrs.brand + attrs.model로 공식몰 검색 → 렌더링 → LLM 추출.
    (brand, model) 중복은 한 번만 처리. 상위 top_n 후보까지.
    keyword가 주어지면, 그 조사 키워드와 토큰을 공유하지 않는 모델(키워드스터핑으로
    유입된 '젤 카야노 14' 같은 무관 제품)은 타깃에서 제외한다."""
    # 조사 키워드의 핵심 토큰 (브랜드·단일문자 불용어 제외)
    _stop = {"아식스", "asics", "s", "t", "ss", "신발", "운동화", "러닝화"}
    kw_tokens = {w for w in re.findall(r'[0-9a-zA-Z가-힣]+', (keyword or "").lower())
                 if w not in _stop}

    targets: List[dict] = []
    seen = set()
    for it in items:
        attrs = it.get("attrs") or {}
        brand, model = attrs.get("brand"), attrs.get("model")
        if not brand or not model:
            continue
        # 조사 키워드와 무관한 모델은 제외 (정규화 model 토큰이 키워드와 겹쳐야 채택)
        if kw_tokens:
            m_tokens = set(re.findall(r'[0-9a-zA-Z가-힣]+', model.lower()))
            if not (kw_tokens & m_tokens):
                continue
        key = (brand.strip().lower(), model.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        targets.append({
            "brand": brand, "model": model,
            "sku": attrs.get("sku"),                # SKU 기반 정밀 검색용
            "source_title": it.get("title"),
            "source_lprice": it.get("lprice"),
        })
        if len(targets) >= top_n:
            break

    if not targets:
        return []

    # 1) 공식 URL 검색 — SKU 우선 → 브랜드 자체 검색 → Brave Search.
    #    SKU가 있으면 가장 정확. 검색어는 LLM 정규화 model이 아닌 원본 상품명 사용.
    print(f"       · 공식 URL 검색 중 ({len(targets)}건)...")
    for t in targets:
        # 검색어는 스터핑된 원본 제목 대신 '브랜드 + 정규화 모델'로 — 엉뚱한 모델 매칭 방지
        search_query = f"{t['brand']} {t['model']}".strip()
        t["official_url"] = find_official_url(
            t["brand"], search_query, brave_key, client,
            sku=t.get("sku"),
        )
        time.sleep(1.1)  # Brave free tier 1 qps에 안전한 간격

    # 2) 유효 URL만 일괄 렌더링 (브라우저 1회 재사용)
    valid_urls = [t["official_url"] for t in targets if t["official_url"]]
    if not valid_urls:
        for t in targets:
            t["official"] = None
        return targets
    print(f"       · 페이지 렌더링 중 ({len(valid_urls)}건)...")
    rendered = render_pages(valid_urls)
    url_to_html = dict(zip(valid_urls, rendered))

    # 3) LLM 추출 — 카탈로그 페이지 잡혔으면 product detail로 drill-down 1회
    for t in targets:
        url = t.get("official_url")
        if not url:
            t["official"] = None
            continue
        raw = url_to_html.get(url)
        if not raw:
            t["official"] = None
            continue
        text = html_to_text(raw)
        if not text:
            t["official"] = None
            continue
        info = extract_official_info(
            t["brand"], t["model"], text, client,
            source_title=t.get("source_title") or "",
        )
        # 카탈로그/검색결과 페이지 판정 — URL 패턴 + LLM 판정 둘 다 활용
        is_product_url = (
            bool(PRODUCT_URL_PATTERNS.search(url))
            and not CATALOG_URL_RE.search(url)
        )
        needs_drill = (
            not is_product_url
            or not info
            or not info.get("is_product_page")
        )
        if needs_drill:
            product_url = _drill_down_from_catalog(
                raw, t["brand"],
                t.get("source_title") or t["model"], client,
                base_url=url,
            )
            if product_url and product_url != url:
                print(f"       · drill-down: {url} → {product_url}")
                rendered2 = render_pages([product_url])
                if rendered2 and rendered2[0]:
                    text2 = html_to_text(rendered2[0])
                    if text2:
                        info2 = extract_official_info(
                            t["brand"], t["model"], text2, client,
                            source_title=t.get("source_title") or "",
                        )
                        if info2 and info2.get("is_product_page"):
                            t["official_url"] = product_url
                            info = info2
            else:
                print(f"       · drill-down 실패 (product URL 못 찾음): {url}")
        t["official"] = info

    return targets


# ──────────────────────────────────────────────────────────────────────────
# 2) LLM 구조화 추출 스키마 (Pydantic → 구조화 출력으로 검증)
# ──────────────────────────────────────────────────────────────────────────
class FAQ(BaseModel):
    question: str = Field(description="소비자가 실제로 자주 묻는 질문")
    short_answer: str = Field(description="리뷰들에서 도출한 한 문장 답변")


class Insights(BaseModel):
    common_phrases: List[str] = Field(description="실사용자가 자주 쓰는 표현/키워드")
    use_cases: List[str] = Field(description="추천 용도·착용 상황")
    pros: List[str] = Field(description="반복적으로 언급되는 장점")
    concerns: List[str] = Field(description="반복적으로 언급되는 불만/우려")
    sizing_tips: List[str] = Field(description="사이즈·발볼·핏 관련 팁")
    faqs: List[FAQ] = Field(description="자주 묻는 질문 3~6개")


MODEL = os.environ.get("INSIGHT_MODEL", "gpt-4o")  # 구조화 출력 지원 모델. 비용 절감 시 "gpt-4o-mini"

EXTRACT_PROMPT = """다음은 '{keyword}'에 대한 블로그/유튜브 글·댓글 모음입니다(협찬 글 제외).
원문을 복제하지 말고, 시장조사 관점에서 **반복되는 패턴만** 구조화해 추출하세요.
구매자가 실제로 쓰는 표현과 자주 묻는 질문에 집중합니다.

--- 수집 데이터 ---
{snippets}
--- 끝 ---

위 데이터에서 공통 표현, 추천 용도, 장점, 우려, 사이즈 팁, FAQ를 정리하세요.
근거가 약하면 무리해서 만들지 말고 비워두세요."""


def extract_insights(keyword: str, items: List[dict], client: OpenAI) -> Insights:
    def _fmt(it: dict) -> str:
        tag = "[유튜브]" if it.get("source") == "youtube" else "[블로그]"
        head = it["title"] or "(댓글)"
        return f"- {tag} {head} | {it['desc']}"

    snippets = "\n".join(_fmt(it) for it in items)[:12000]  # 과도한 입력 방지(토큰/비용)
    resp = client.chat.completions.parse(
        model=MODEL,
        temperature=0,
        messages=[{
            "role": "user",
            "content": EXTRACT_PROMPT.format(keyword=keyword, snippets=snippets),
        }],
        response_format=Insights,
    )
    return resp.choices[0].message.parsed


# ──────────────────────────────────────────────────────────────────────────
# 2-a) 출처·근거가 부착된 비정형 인사이트 (insights_unstructured.json)
#      각 인사이트가 "어떤 출처(블로그/유튜브)의 어떤 문장"에서 나왔는지 추적한다.
#      ── 한계: 네이버 블로그 검색 API는 본문이 아니라 약 200자 '요약'만 주므로,
#         블로그 근거 문장은 그 요약 범위 안에서만 인용된다(본문 전체 아님).
# ──────────────────────────────────────────────────────────────────────────
class Evidence(BaseModel):
    source_id: str = Field(
        description="근거로 인용한 출처의 ID. 반드시 입력에 제시된 [S숫자] 중 하나를 그대로 사용")
    quote: str = Field(
        description="해당 출처 원문에서 그대로 옮긴 근거 문장(요약·의역·창작 금지)")


class SourcedInsight(BaseModel):
    point: str = Field(description="추출한 인사이트(반복되는 표현/용도/장점/우려/사이즈팁)")
    evidence: List[Evidence] = Field(
        description="이 인사이트를 뒷받침하는 1개 이상의 원문 근거(가능하면 2개 이상)")


class SourcedFAQ(BaseModel):
    question: str = Field(description="소비자가 실제로 자주 묻는 질문")
    short_answer: str = Field(description="답변·후기에서 도출한 한 문장 답변")
    question_evidence: List[Evidence] = Field(
        description="이 질문이 실제로 나왔음을 보여주는 사용자 질문 문장(질문 형태 그대로 가능)")
    answer_evidence: List[Evidence] = Field(
        description="short_answer의 근거가 된 답변·후기 문장(질문이 아니라 단정적 서술이어야 함)")


class SourcedInsights(BaseModel):
    # 비정형 메인 결과는 taxonomy(context/aspect/verdict/flags)로 옮겼고, 기존 5개
    # 카테고리(common_phrases/use_cases/pros/concerns/sizing_tips)는 제거했다.
    # 이 스키마는 GEO 공개 산출물(faq.jsonld·insights.json)용 FAQ만 담당한다.
    faqs: List[SourcedFAQ] = Field(description="자주 묻는 질문 3~6개 (질문+답변 근거 분리)")


EXTRACT_SOURCED_PROMPT = """다음은 '{keyword}'에 대한 실제 사용자 글·댓글·구매후기 모음입니다.
각 항목은 [S번호]로 시작하고 종류가 표시됩니다: (블로그), (유튜브 댓글), (유튜브 답글(답변)), (다나와 구매후기).
이 데이터에서 소비자가 **자주 묻는 질문(FAQ)**과 그 답변을 추출하세요(GEO 공개 산출물용).

[FAQ는 '질문 + 그 답변'으로]
- question: 사용자가 실제로 자주 묻는 질문.
- question_evidence: 그 질문이 실제로 나왔음을 보여주는 사용자 질문 문장(질문 형태 그대로 OK).
- short_answer: 답변·후기에서 도출한 한 문장 답변.
- answer_evidence: short_answer의 근거가 된 **답변·후기(단정적 서술)** 문장.
  evidence.source_id는 [S번호] 그대로(없는 ID 금지), evidence.quote는 원문 그대로(요약·의역·창작 금지).
- **"~까요?", "~나요?", "궁금합니다" 같은 질문/문의 문장은 answer_evidence로 쓸 수 없습니다**(단정적 서술만).
- 답변 근거(answer_evidence)가 하나도 없으면 그 FAQ는 만들지 마세요.

--- 수집 데이터 ---
{snippets}
--- 끝 ---

답변 근거가 약하거나 단정적으로 인용할 문장이 없으면 무리해서 FAQ를 만들지 마세요."""


# ──────────────────────────────────────────────────────────────────────────
# 2-b) 필수 taxonomy — context(사용자/상황) · aspect(객관속성) · verdict(평가) · flags
#      깊은 계층(대분류>중분류>dim)을 그대로 중첩하면 OpenAI structured output의
#      '중첩 5단계' 한계에 닿으므로, 각 dim을 루트 직속 필드(List[SourcedInsight])로
#      '평탄화'해 중첩 3단계로 둔다. 계층(누가>연령 …)은 build 단계에서 다시 조립한다.
#      신뢰성을 위해 context 그룹과 aspect+verdict+flags 그룹을 별도 호출로 나눈다.
#      각 dim은 기존 SourcedInsight/Evidence를 재사용 → _resolve_evidence로 같은 근거검증.
# ──────────────────────────────────────────────────────────────────────────
class SourcedContext(BaseModel):
    # context.who — 이 상품을 누가 쓰는가 (8)
    who_age: List[SourcedInsight] = Field(description="사용자 연령대(예: 30대, 아기, 시니어)")
    who_gender: List[SourcedInsight] = Field(description="성별 관련 언급")
    who_occupation: List[SourcedInsight] = Field(description="직업·신분(직장인, 학생, 주부 등)")
    who_household: List[SourcedInsight] = Field(description="가구 구성(1인가구, 신혼, 아이있는집 등)")
    who_body_type: List[SourcedInsight] = Field(description="체형·신체 특성(발볼, 키, 민감성 등)")
    who_health: List[SourcedInsight] = Field(description="건강 상태·고민(다이어트, 위장, 알레르기 등)")
    who_taste_pref: List[SourcedInsight] = Field(description="맛·디자인 등 취향(매운맛 선호 등)")
    who_lifestyle: List[SourcedInsight] = Field(description="라이프스타일(운동, 캠핑, 자취 등)")
    # context.when — 언제·어떤 상황에서 쓰는가 (5)
    when_scene: List[SourcedInsight] = Field(description="사용 장면(야식, 등산, 출근 등)")
    when_season: List[SourcedInsight] = Field(description="시즌·계절")
    when_event: List[SourcedInsight] = Field(description="특별 이벤트(명절, 생일, 시험기간 등)")
    when_time_of_day: List[SourcedInsight] = Field(description="시간대(아침, 밤 등)")
    when_frequency: List[SourcedInsight] = Field(description="사용 빈도(매일, 가끔, 비상용 등)")
    # context.where — 어디서 쓰는가 (1)
    where_place: List[SourcedInsight] = Field(description="설치·사용 공간(집, 사무실, 차량 등)")
    # context.why — 왜 사는가 (3)
    why_positive_goal: List[SourcedInsight] = Field(description="구매로 이루려는 긍정적 목표")
    why_negative_concern: List[SourcedInsight] = Field(description="구매를 망설이게 하는 부정적 우려")
    why_workload: List[SourcedInsight] = Field(description="해결하려는 부담·수고(요리 귀찮음 등)")
    # context.gift — 누구에게 선물하는가 (1)
    gift_recipient: List[SourcedInsight] = Field(description="선물 받는 대상(부모님, 친구, 직장 등)")
    # context.how_compatibility — 무엇과 호환되는가 (3)
    compat_device: List[SourcedInsight] = Field(description="호환 기기(특정 기기·모델)")
    compat_os: List[SourcedInsight] = Field(description="호환 OS·플랫폼")
    compat_standard: List[SourcedInsight] = Field(description="호환 규격·표준(사이즈 규격, 전압 등)")


class SourcedAspectVerdict(BaseModel):
    # aspect — 상품 자체의 객관적 속성 (8)
    aspect_taste: List[SourcedInsight] = Field(description="맛(식품류). 해당 없으면 비움")
    aspect_texture: List[SourcedInsight] = Field(description="질감·식감·촉감")
    aspect_spec: List[SourcedInsight] = Field(description="스펙·성능·기능")
    aspect_size: List[SourcedInsight] = Field(description="사이즈·핏·용량")
    aspect_care: List[SourcedInsight] = Field(description="세척·보관·관리법")
    aspect_price_range: List[SourcedInsight] = Field(description="가격대·가성비")
    aspect_routine: List[SourcedInsight] = Field(description="사용 루틴·조리·사용법")
    aspect_sensory: List[SourcedInsight] = Field(description="오감 경험(향, 색, 소리 등)")
    # verdict.compare — 다른 상품·브랜드 비교 (3)
    compare_spec: List[SourcedInsight] = Field(description="다른 상품과 스펙·성능 비교")
    compare_brand: List[SourcedInsight] = Field(description="다른 브랜드와 비교")
    compare_alternative_when: List[SourcedInsight] = Field(description="어떤 경우 대안 상품이 나은지")
    # verdict.trust — 신뢰·인증 시그널 (4)
    trust_clinical: List[SourcedInsight] = Field(description="임상·효능·과학적 근거 언급")
    trust_authenticity: List[SourcedInsight] = Field(description="정품·진위 관련 신뢰")
    trust_origin: List[SourcedInsight] = Field(description="원산지·제조국·제조 관련 신뢰")
    trust_certification: List[SourcedInsight] = Field(description="인증·수상·검증 관련")
    # verdict.strengths / weaknesses — 리뷰 빈도 동반 강·약점 (cell 배열)
    strengths: List[SourcedInsight] = Field(
        description="반복적으로 언급되는 강점(각 항목의 cited_examples가 빈도 proxy)")
    weaknesses: List[SourcedInsight] = Field(
        description="반복적으로 언급되는 약점(각 항목의 cited_examples가 빈도 proxy)")
    # verdict.overall_recommendation — 한 줄 종합 추천(근거 인용 없는 종합 서술)
    overall_recommendation: str = Field(
        description="위 내용을 종합한 한 줄 추천 요약. 근거 부족하면 빈 문자열")
    # flags — 빠른 필터용 boolean (근거 분명할 때만 true)
    is_direct_import: bool = Field(description="직구/해외직배송 여부(근거 있을 때만 true)")
    is_gift_set: bool = Field(description="선물세트/기획세트 여부(근거 있을 때만 true)")
    is_premium: bool = Field(description="프리미엄/고급 라인 여부(근거 있을 때만 true)")
    is_eco_friendly: bool = Field(description="친환경/비건/무첨가 등 여부(근거 있을 때만 true)")


_EVIDENCE_RULES = """[근거(evidence) 규칙 — 모든 항목 공통]
- 인사이트는 질문이 아니라 '답변·후기·경험을 단정적으로 서술한 문장'에서만 뽑습니다.
- "~까요? ~나요? 궁금합니다" 같은 질문/문의 문장은 근거(evidence)로 쓰지 마세요.
- evidence.source_id: 근거가 된 항목의 [S번호]를 그대로 적습니다(없는 ID를 지어내지 마세요).
- evidence.quote: 그 출처 원문에서 근거 문장을 원문 그대로 옮깁니다(요약·의역·창작 금지).
- 어떤 분류(셀)에 들어맞는 단정적 근거가 원문에 없으면 그 셀은 빈 배열로 두세요.
  모든 셀을 억지로 채우지 마세요 — 빈 셀은 정상입니다(상품에 따라 해당 없음이 많습니다).
- point(인사이트 요약)와 overall_recommendation 등 생성 텍스트는 **반드시 한국어**로 작성합니다
  (영어로 쓰지 마세요). 단 evidence.quote는 원문 그대로 둡니다."""


EXTRACT_CONTEXT_PROMPT = """다음은 '{keyword}'에 대한 실제 사용자 글·댓글·구매후기 모음입니다.
각 항목은 [S번호]로 시작하고 종류가 표시됩니다: (블로그), (유튜브 댓글), (유튜브 답글(답변)), (다나와 구매후기).
'누가(who) / 언제(when) / 어디서(where) / 왜(why) 사고 쓰는가', '누구에게 선물하는가(gift)',
'무엇과 호환되는가(how_compatibility)'를 각 분류 필드에 맞춰 구조화해 추출하세요.

""" + _EVIDENCE_RULES + """

--- 수집 데이터 ---
{snippets}
--- 끝 ---
"""


EXTRACT_ASPECT_VERDICT_PROMPT = """다음은 '{keyword}'에 대한 실제 사용자 글·댓글·구매후기 모음입니다.
각 항목은 [S번호]로 시작하고 종류가 표시됩니다: (블로그), (유튜브 댓글), (유튜브 답글(답변)), (다나와 구매후기).
상품의 '객관적 속성(aspect)'과 '평가(verdict: 비교·신뢰·강점·약점·종합추천)', 빠른 필터 'flags'를 추출하세요.

- strengths/weaknesses: 반복적으로 언급되는 강·약점. 근거를 여러 개 달수록 빈도 신호가 강해집니다.
- overall_recommendation: 위 내용을 종합한 '한 줄' 추천 요약(이 한 줄만 근거 인용 없이 종합 서술 허용).
- flags(boolean): 근거가 분명할 때만 true, 불확실하면 false로 둡니다.

""" + _EVIDENCE_RULES + """

--- 수집 데이터 ---
{snippets}
--- 끝 ---
"""


def _item_content(it: dict) -> str:
    """LLM에게 보여준 항목 본문과 '바이트 동일'한 검증용 텍스트 (제목 + desc).
    프롬프트 라인 구성과 인용 검증이 같은 함수를 쓰도록 단일 출처로 둔다."""
    head = it.get("title") or "(댓글)"
    return f"{head} {it.get('desc', '')}".strip()


def _build_sourced_snippets(
        items: List[dict], char_budget: int = 28000) -> Tuple[str, Dict[str, dict], int]:
    """항목마다 [S번호] ID를 부여해 프롬프트 문자열과 ID→항목 매핑을 만든다.
    문자열은 '항목 단위'로만 잘라(부분 항목 금지) 모든 인용 ID가 실제 항목과 1:1 대응되게 한다.
    반환: (snippets, id_map, dropped_count)."""
    lines: List[str] = []
    id_map: Dict[str, dict] = {}
    used = 0
    for i, it in enumerate(items, 1):
        sid = f"S{i}"
        if it.get("source") == "youtube":
            tag = "유튜브 답글(답변)" if it.get("kind") == "reply" else "유튜브 댓글"
        elif it.get("source") == "danawa":
            tag = "다나와 구매후기"
        else:
            tag = "블로그"
        line = f"[{sid}] ({tag}) {_item_content(it)}"
        if lines and used + len(line) + 1 > char_budget:
            return "\n".join(lines), id_map, len(items) - len(lines)
        lines.append(line)
        id_map[sid] = it
        used += len(line) + 1
    return "\n".join(lines), id_map, 0


_WS_RE = re.compile(r"\s+")


def _norm_soft(s: str) -> str:
    """공백 축약 + 소문자 — 문자는 보존(엄격 부분일치용)."""
    return _WS_RE.sub(" ", s).strip().lower()


def _norm_hard(s: str) -> str:
    """공백·구두점·이모지 제거 + 소문자 — LLM 정규화 차이 흡수용."""
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def _verify_quote(quote: str, content: str) -> str:
    """인용이 원문에 실재하는지 3단계로 판정: 'verified' | 'partial' | 'unverified'.
    - verified : 공백/구두점만 다른 부분일치 (원문에 사실상 그대로 존재)
    - partial  : 핵심 토큰의 60% 이상이 원문에 존재 (LLM이 문장을 합치거나 다듬은 경우)
    - unverified: 매칭 실패 또는 환각 → 출력에서 제외한다."""
    if not quote or not content:
        return "unverified"
    if _norm_soft(quote) in _norm_soft(content):
        return "verified"
    if _norm_hard(quote) and _norm_hard(quote) in _norm_hard(content):
        return "verified"
    c_soft = _norm_soft(content)
    tokens = [t for t in _norm_soft(quote).split(" ") if len(t) >= 2]
    if tokens and sum(1 for t in tokens if t in c_soft) / len(tokens) >= 0.6:
        return "partial"
    return "unverified"


# 질문/문의 문장 식별 — 인사이트·답변의 '근거'로는 질문을 쓰지 않는다(답변 기반 추출).
# 보수적(고정밀): 한국어 의문 종결어미 + 물음표만 잡아 단정형 후기를 잘못 거르지 않게 한다.
_QUESTION_RE = re.compile(
    r"(\?|까요|까여|까용|을까|ㄹ까|ㄴ가요|은가요|인가요|나요|되나요|하나요|맞나요|"
    r"좋을지|나을지|될지|될까|할까|어떨까|어떤가요|어떠한가요|어때요|궁금|"
    r"뭐예요|뭔가요|무엇인가요|알려주세요|여쭤|문의)")


def _looks_like_question(text: str) -> bool:
    """질문/문의 문장인지 간단 판별 (의문 종결어미 또는 물음표 포함)."""
    return bool(_QUESTION_RE.search(text or ""))


def _evidence_kind(it: dict) -> str:
    """근거 출처의 종류 라벨: blog | yt_comment | yt_reply | danawa_review."""
    if it.get("source") == "youtube":
        return "yt_reply" if it.get("kind") == "reply" else "yt_comment"
    if it.get("source") == "danawa":
        return "danawa_review"
    return "blog"


_SENT_SPLIT_RE = re.compile(r"[\.!?\n。·•…]+|\s{2,}")

# AUDIT_RECOVERY=1 일 때 재접지/재배정 복구 이벤트를 누적 → recovery_audit.json 으로 덤프.
_RECOVERY_AUDIT: List[dict] = []


def _candidate_spans(content: str) -> List[str]:
    """원문을 재접지 후보 구간으로 쪼갠다: 개별 문장 + 인접 2문장 윈도우
    (LLM이 두 문장을 합쳐 인용한 경우 대비). 너무 짧은 조각은 버린다."""
    raw = [s.strip() for s in _SENT_SPLIT_RE.split(content) if s.strip()]
    spans = list(raw)
    for i in range(len(raw) - 1):
        spans.append(raw[i] + " " + raw[i + 1])
    return [s for s in spans if len(s) >= 6]


def _reground_quote(quote: str, content: str, thresh: float = 0.72) -> Optional[str]:
    """LLM이 의역/창작한 quote를 원문에 실재하는 구간으로 되돌린다.
    content에서 quote와 가장 유사한 실제 구간(문장/2문장 윈도)을 찾아 유사도가
    임계 이상이면 '원문 그대로의' 그 구간을 반환(없으면 None). 반환값은 항상
    content의 실제 부분문자열이므로, 교체 후 재검증하면 verified가 보장된다."""
    if not quote or not content:
        return None
    nq = _norm_soft(quote)
    best, best_r = None, 0.0
    for span in _candidate_spans(content):
        r = difflib.SequenceMatcher(None, nq, _norm_soft(span)).ratio()
        if r > best_r:
            best, best_r = span, r
    return best if best_r >= thresh else None


def _best_source_match(quote: str, id_map: Dict[str, dict]):
    """모든 출처에서 quote를 뒷받침하는 실제 근거를 탐색한다(출처 ID 환각/오배정 복구용).
    1순위: 어느 출처든 그대로 존재(verified/partial) → 그 출처로 재배정.
    2순위: 가장 유사한 원문 구간으로 퍼지 재접지. 반환 (sid, it, real_quote, match)|None."""
    for sid, it in id_map.items():
        st = _verify_quote(quote, _item_content(it))
        if st in ("verified", "partial"):
            return sid, it, quote, st
    best = None
    for sid, it in id_map.items():
        rg = _reground_quote(quote, _item_content(it))
        if rg:
            r = difflib.SequenceMatcher(None, _norm_soft(quote), _norm_soft(rg)).ratio()
            if best is None or r > best[0]:
                best = (r, sid, it, rg)
    if best:
        _, sid, it, rg = best
        return sid, it, rg, "regrounded"
    return None


def _lookup_source(sid: str, id_map: Dict[str, dict]):
    """source_id를 출처에 매핑하되 표기 차이를 흡수한다: LLM이 프롬프트의 [S번호]
    형식을 따라 '[S44]'처럼 대괄호/공백/대소문자를 붙여도 'S44' 키에 정확히 연결.
    반환 (정규화된 키, item) — 못 찾으면 (원본 sid, None)."""
    it = id_map.get(sid)
    if it is not None:
        return sid, it
    key = re.sub(r"[\[\]\s]", "", sid or "").upper()    # [S44] / s44 → S44
    for k, v in id_map.items():
        if k.upper() == key:
            return k, v
    return sid, None


def _resolve_evidence(ev: "Evidence", id_map: Dict[str, dict], stats: Counter,
                      allow_question: bool = False) -> Optional[dict]:
    """LLM이 준 evidence를 실제 출처 메타데이터로 해석하고 인용을 검증한다.
    환각이어도 곧장 버리지 않고 '재접지(re-grounding)'로 회복을 시도한다:
      ② 인용 환각(의역/창작) → 같은 출처 내 실제 문장으로 교체.
      ① 출처 ID 환각/오배정 → 전체 출처에서 실제 근거를 찾아 재배정.
    회복도 실패하면 유형별로 분리 계측 후 None(=버림). allow_question=False면
    최종 채택 quote가 질문/문의면 버린다(답변 근거는 단정적 서술이어야 하므로)."""
    sid, it = _lookup_source(ev.source_id, id_map)  # [S44]↔S44 등 표기차 흡수 후 매핑
    quote, match = ev.quote, None
    recovery = None                                 # None | "id" | "quote_same" | "quote_cross"
    orig_found = it is not None                     # 정규화 후에도 못 찾으면 진짜 ID 환각

    if it is not None:                              # 출처 ID는 유효 — 인용부터 검증
        status = _verify_quote(quote, _item_content(it))
        if status in ("verified", "partial"):
            match = status
        else:                                       # ② 인용 환각 → 같은 출처 내 재접지
            rg = _reground_quote(quote, _item_content(it))
            if rg:
                quote, match, recovery = rg, "regrounded", "quote_same"
                stats["recovered_quote"] += 1

    if match is None:                               # ① 출처 ID 환각 또는 같은-출처 재접지 실패
        hit = _best_source_match(ev.quote, id_map)  # → 전체 출처에서 실제 근거 탐색
        if hit:
            sid, it, quote, match = hit
            if not orig_found:
                stats["recovered_id"] += 1; recovery = "id"
            else:
                stats["recovered_quote"] += 1; recovery = "quote_cross"
        else:                                       # 최종 폐기 — 환각 유형 분리 계측
            stats["dropped_unverified"] += 1
            stats["dropped_id_halluc" if not orig_found
                  else "dropped_quote_halluc"] += 1
            return None

    if not allow_question and _looks_like_question(quote):
        stats["dropped_question"] += 1              # 질문/문의는 답변 근거로 부적격
        return None
    if recovery and os.environ.get("AUDIT_RECOVERY"):
        # 유일성 감사: 최종 quote를 담은 출처가 몇 개인가. 1=유일(명백 정답), ≥2=원출처 모호.
        n_src = sum(1 for _i in id_map.values()
                    if _verify_quote(quote, _item_content(_i)) in ("verified", "partial"))
        _RECOVERY_AUDIT.append({
            "type": recovery, "orig_id": ev.source_id, "new_id": sid, "match": match,
            "n_sources_with_quote": n_src, "id_changed": sid != ev.source_id,
            "orig_quote": ev.quote, "final_quote": quote,
            "assigned_source": (_item_content(it) or "")[:300],
        })
    stats[match] += 1
    return {
        "source_id": sid,
        "source": it.get("source"),                 # "naver"(블로그) | "youtube"
        "kind": _evidence_kind(it),                 # blog | yt_comment | yt_reply
        "is_ad": is_ad(it),                         # 광고/협찬 신호 여부 (제외 안 함, 표기만)
        "ad_signals": ad_signals(it),               # 매칭된 협찬 신호 단어
        "author": it.get("bloggername", ""),
        "date": it.get("postdate", ""),
        "url": it.get("link", ""),
        "title": it.get("title") or "",
        "rating": it.get("rating"),                 # 다나와 구매후기 별점(5점), 그 외 None
        "quote": quote,
        "match": match,                             # verified | partial | regrounded
    }


def build_sourced_block(si: SourcedInsights, ctx: SourcedContext,
                        av: SourcedAspectVerdict, id_map: Dict[str, dict],
                        kept: List[dict]) -> dict:
    """키워드별 출처 부착 인사이트 블록을 만든다.
    근거가 하나도 남지 않는 항목(전부 환각/미검증)은 제외하고 개수를 집계한다.
    기존 6 카테고리(insights)에 더해 필수 taxonomy(context/aspect/verdict/flags)를
    같은 근거검증으로 조립한다. taxonomy의 모든 dim 키는 빈 배열이어도 항상 존재한다
    ('필수 포함' = 스키마 레벨 존재; 상품에 따라 해당 없는 dim은 비어 있는 게 정상)."""
    stats: Counter = Counter()

    def resolve_points(points: List[SourcedInsight]) -> List[dict]:
        out = []
        for ins in points:
            ev = [r for r in (_resolve_evidence(e, id_map, stats) for e in ins.evidence) if r]
            if ev:
                out.append({"point": ins.point, "cited_examples": len(ev), "evidence": ev})
            else:
                stats["insights_dropped"] += 1
        return out

    def cells(obj, pairs: List[Tuple[str, str]]) -> dict:
        """평탄화 dim 필드들을 {dim명: 검증된 포인트 리스트} 로 조립(계층 복원)."""
        return {label: resolve_points(getattr(obj, attr)) for label, attr in pairs}

    # 기존 5개 카테고리(common_phrases/use_cases/pros/concerns/sizing_tips)는 제거했다.
    # 비정형 메인 결과는 taxonomy(아래)로 옮겼고, FAQ만 공개 산출물용으로 별도 추출한다.
    # FAQ는 '질문 + 그 답변'으로: 질문 근거(질문 허용)와 답변 근거(질문 금지)를 분리.
    # 답변 근거가 하나도 없으면(=실제 답변 없음) 그 FAQ는 만들지 않는다.
    # (stats는 아래 taxonomy 해석에서도 누적되므로 verification 조립 전에 먼저 처리한다.)
    faqs_out = []
    for f in si.faqs:
        a_ev = [r for r in (_resolve_evidence(e, id_map, stats, allow_question=False)
                            for e in f.answer_evidence) if r]
        if not a_ev:
            stats["faqs_dropped"] += 1
            continue
        q_ev = [r for r in (_resolve_evidence(e, id_map, stats, allow_question=True)
                            for e in f.question_evidence) if r]
        faqs_out.append({
            "question": f.question,
            "short_answer": f.short_answer,
            "cited_examples": len(a_ev),
            "answer_evidence": a_ev,        # short_answer의 근거(단정적 서술)
            "question_evidence": q_ev,      # 이 질문이 실제로 나온 출처
        })

    n_blog = sum(1 for it in kept if it.get("source") == "naver")
    n_yt = sum(1 for it in kept if it.get("source") == "youtube")
    n_dn = sum(1 for it in kept if it.get("source") == "danawa")
    n_ad = sum(1 for it in kept if is_ad(it))
    source_index = {sid: {
        "source": it.get("source"),
        "kind": _evidence_kind(it),                 # blog | yt_comment | yt_reply
        "is_ad": is_ad(it),                         # 광고/협찬 신호 여부(제외 안 함, 표기만)
        "ad_signals": ad_signals(it),               # 매칭된 협찬 신호 단어
        "author": it.get("bloggername", ""),
        "date": it.get("postdate", ""),
        "url": it.get("link", ""),
        "title": it.get("title") or "",
        "rating": it.get("rating"),                 # 다나와 구매후기 별점(5점), 그 외 None
        "text": _item_content(it)[:500],
    } for sid, it in id_map.items()}

    # 필수 taxonomy — 평탄화된 dim들을 (대분류 > 중분류 > dim) 계층으로 복원.
    # 모든 dim 키는 빈 배열이어도 항상 존재한다('필수 포함'의 의미는 스키마 레벨 존재).
    taxonomy = {
        "context": {
            "who": cells(ctx, [
                ("age", "who_age"), ("gender", "who_gender"),
                ("occupation", "who_occupation"), ("household", "who_household"),
                ("body_type", "who_body_type"), ("health", "who_health"),
                ("taste_pref", "who_taste_pref"), ("lifestyle", "who_lifestyle")]),
            "when": cells(ctx, [
                ("scene", "when_scene"), ("season", "when_season"),
                ("event", "when_event"), ("time_of_day", "when_time_of_day"),
                ("frequency", "when_frequency")]),
            "where": cells(ctx, [("place", "where_place")]),
            "why": cells(ctx, [
                ("positive_goal", "why_positive_goal"),
                ("negative_concern", "why_negative_concern"),
                ("workload", "why_workload")]),
            "gift": cells(ctx, [("recipient", "gift_recipient")]),
            "how_compatibility": cells(ctx, [
                ("device", "compat_device"), ("os", "compat_os"),
                ("standard", "compat_standard")]),
        },
        "aspect": cells(av, [
            ("taste", "aspect_taste"), ("texture", "aspect_texture"),
            ("spec", "aspect_spec"), ("size", "aspect_size"),
            ("care", "aspect_care"), ("price_range", "aspect_price_range"),
            ("routine", "aspect_routine"), ("sensory", "aspect_sensory")]),
        "verdict": {
            "compare": cells(av, [
                ("spec", "compare_spec"), ("brand", "compare_brand"),
                ("alternative_when", "compare_alternative_when")]),
            "trust": cells(av, [
                ("clinical", "trust_clinical"), ("authenticity", "trust_authenticity"),
                ("origin", "trust_origin"), ("certification", "trust_certification")]),
            # strengths/weaknesses: 각 항목의 cited_examples가 '빈도(frequency) proxy'
            "strengths": resolve_points(av.strengths),
            "weaknesses": resolve_points(av.weaknesses),
            "overall_recommendation": av.overall_recommendation or "",
        },
        "flags": {                                  # LLM 판단(별도 근거검증 없음)
            "is_direct_import": av.is_direct_import,
            "is_gift_set": av.is_gift_set,
            "is_premium": av.is_premium,
            "is_eco_friendly": av.is_eco_friendly,
        },
    }

    return {
        "analyzed_count": len(kept),
        "sources": {"naver": n_blog, "youtube": n_yt, "danawa": n_dn},
        "ad_flagged": n_ad,                         # 광고/협찬으로 표기된 출처 수(제외 안 함)
        "note": ("블로그 근거는 네이버 검색 API의 약 200자 요약에서 인용(본문 전체 아님). "
                 "다나와 근거는 쇼핑몰 통합 구매후기 본문 전체에서 인용(robots 허용 경로). "
                 "match=verified는 원문 부분일치, partial은 핵심 토큰 다수 일치. "
                 "cited_examples는 LLM이 제시한 근거 예시 수(전체 언급 빈도 아님). "
                 "taxonomy.verdict.strengths/weaknesses 등 각 셀 항목의 cited_examples를 "
                 "'빈도(frequency) proxy'로 사용한다(검증된 근거 예시 수이며 전체 언급 빈도와 다를 수 있음). "
                 "taxonomy의 모든 dim 키는 빈 배열이어도 항상 존재하며, 빈 셀은 해당 상품에 그 관점이 "
                 "없다는 뜻이다. flags는 LLM 판단으로 별도 근거검증을 거치지 않는다. "
                 "질문/문의 문장은 인사이트·답변 근거에서 제외(dropped_question). "
                 "광고/협찬 글도 분석에 포함하되 is_ad/ad_signals로 표기만 함."),
        "verification": {
            "verified": stats["verified"],
            "partial": stats["partial"],
            "regrounded": stats["regrounded"],          # 의역 환각을 원문 실제 구간으로 복구
            "recovered_quote": stats["recovered_quote"],  # 인용 재접지로 회복된 근거 수
            "recovered_id": stats["recovered_id"],      # 출처 ID 환각/오배정을 재배정해 회복
            "dropped_unverified": stats["dropped_unverified"],
            "dropped_quote_halluc": stats["dropped_quote_halluc"],  # 회복 실패한 인용 환각
            "dropped_id_halluc": stats["dropped_id_halluc"],        # 회복 실패한 출처 ID 환각
            "dropped_question": stats["dropped_question"],
            "insights_dropped": stats["insights_dropped"],
            "faqs_dropped": stats["faqs_dropped"],
        },
        "taxonomy": taxonomy,           # 비정형 메인 — context/aspect/verdict/flags
        "faqs": faqs_out,               # GEO 공개 산출물용(faq.jsonld·insights.json)
        "source_index": source_index,
    }


def _flatten_block(block: dict) -> dict:
    """검증·필터된 block(taxonomy + faqs)을 공개 insights.json용 '요약'으로 down-convert.
    비정형 메인 결과는 taxonomy지만, GEO 공개 산출물에는 사람/AI가 바로 인용할 요약만
    담는다: 종합추천 + 대표 강·약점 + 채워진 객관속성(aspect) + FAQ. 모두 근거검증을
    통과한 항목만 들어간다. ※ top-level 'faqs' 키는 build_faq_jsonld가 읽으므로 유지한다."""
    tax = block.get("taxonomy") or {}
    vd = tax.get("verdict") or {}
    asp = tax.get("aspect") or {}

    def pts(items) -> List[str]:
        return [x["point"] for x in (items or [])]

    return {
        "overall_recommendation": vd.get("overall_recommendation", ""),
        "strengths": pts(vd.get("strengths")),
        "weaknesses": pts(vd.get("weaknesses")),
        # 근거가 실제로 채워진 객관속성(맛/스펙/사이즈 등)만 요약에 노출
        "key_aspects": {dim: pts(v) for dim, v in asp.items() if v},
        "faqs": [{"question": f["question"], "short_answer": f["short_answer"]}
                 for f in block.get("faqs", [])],
    }


def _parse_sourced(keyword: str, snippets: str, schema, prompt: str, client: OpenAI):
    """주어진 스키마/프롬프트로 출처부착 추출 1회. snippets/id_map은 외부에서 공유한다."""
    resp = client.chat.completions.parse(
        model=MODEL,
        temperature=0,
        messages=[{
            "role": "user",
            "content": prompt.format(keyword=keyword, snippets=snippets),
        }],
        response_format=schema,
    )
    return resp.choices[0].message.parsed


def extract_sourced_insights(
        keyword: str, items: List[dict], client: OpenAI
) -> Tuple[SourcedInsights, SourcedContext, SourcedAspectVerdict, Dict[str, dict], int]:
    """출처 ID를 부여한 입력으로 (1) 기존 6 카테고리 + (2) 필수 taxonomy(context,
    aspect/verdict/flags)를 추출. 세 호출이 '같은 snippets/id_map'을 공유하므로
    근거검증(_resolve_evidence)이 일관된다. taxonomy를 context / aspect+verdict 두
    그룹으로 나눠 호출 신뢰성을 높이고 중첩 한계를 피한다.
    반환: (SourcedInsights, SourcedContext, SourcedAspectVerdict, id_map, dropped)."""
    snippets, id_map, dropped = _build_sourced_snippets(items)
    sourced = _parse_sourced(keyword, snippets, SourcedInsights,
                             EXTRACT_SOURCED_PROMPT, client)
    context = _parse_sourced(keyword, snippets, SourcedContext,
                             EXTRACT_CONTEXT_PROMPT, client)
    aspverd = _parse_sourced(keyword, snippets, SourcedAspectVerdict,
                             EXTRACT_ASPECT_VERDICT_PROMPT, client)
    return sourced, context, aspverd, id_map, dropped


# ──────────────────────────────────────────────────────────────────────────
# 2-b) 슬림 리포트 — shop.items 50건을 (brand, model) 그룹 단위로 축약.
#      원본 insights.json은 디버깅/재가공용으로 유지, 사람이 읽기 좋은
#      요약은 insights.slim.json 으로 따로 출력.
# ──────────────────────────────────────────────────────────────────────────
def _slim_catalog_specs(specs: List[dict]) -> List[dict]:
    """catalog spec의 SpecItem 리스트를 dict로 평탄화."""
    out = []
    for s in specs:
        spec = s.get("spec") or {}
        out.append({
            "title": s.get("title"),
            "lprice": s.get("lprice"),
            "rating": spec.get("rating"),
            "review_count": spec.get("review_count"),
            "specs": {sp["key"]: sp["value"]
                      for sp in spec.get("specs") or []},
            "description": spec.get("description_summary"),
        })
    return out


def _slim_official(official: List[dict]) -> List[dict]:
    """official_info에서 is_product_page=true 만 남기고 핵심 필드만."""
    out = []
    for o in official:
        info = o.get("official") or {}
        if not info.get("is_product_page"):
            continue
        # LLM이 is_product_page=true로 오판정해도 /c/·검색 URL이면 상품상세가 아님 → 제외
        if CATALOG_URL_RE.search(o.get("official_url") or ""):
            continue
        out.append({
            "brand": o.get("brand"),
            "model": o.get("model"),
            "source_title": o.get("source_title"),
            "official_url": o.get("official_url"),
            "official_name": info.get("official_name"),
            "list_price": info.get("list_price"),
            "colors": info.get("available_colors"),
            "sizes": info.get("available_sizes"),
            "materials": info.get("materials"),
            "origin": info.get("origin"),
            "sku_codes": info.get("sku_codes"),
        })
    return out


def _slim_shop(shop: dict) -> dict:
    """shop.items를 (brand, model) 기준으로 그룹화해 가격·몰·SKU 분포만 보존."""
    items = shop.get("items") or []
    groups: dict = {}
    for it in items:
        attrs = it.get("attrs") or {}
        brand = (attrs.get("brand") or it.get("brand") or "").strip()
        model = _clean_attr_str(attrs.get("model")) or ""
        # 표기 차이(공백·대소문자)를 흡수해 '조그 100S/조그100S/조그 100 S'를 한 그룹으로
        model_key = re.sub(r'\s+', '', model).lower()
        key = ((brand or "").lower(),
               model_key or it.get("title", "")[:30].lower())
        g = groups.setdefault(key, {
            "brand": brand or None,
            "model": model or None,
            "prices": [],
            "malls": Counter(),
            "skus": set(),
            "variants": set(),
            "colors": set(),
            "productIds": set(),
            "example_title": it.get("title"),
            "example_link": it.get("link"),
        })
        if it.get("lprice"):
            g["prices"].append(it["lprice"])
        if it.get("mallName"):
            g["malls"][it["mallName"]] += 1
        sk = attrs.get("sku_canon") or _canon_sku(attrs.get("sku"))
        if sk:
            g["skus"].add(sk)
        v = _clean_attr_str(attrs.get("variant"))
        if v:
            g["variants"].add(v)
        for c in _filter_colors(attrs.get("colors")):
            g["colors"].add(c)
        if it.get("productId"):
            g["productIds"].add(str(it["productId"]))

    models_out = []
    for g in groups.values():
        prices = sorted(g["prices"])
        models_out.append({
            "brand": g["brand"],
            "model": g["model"],
            "count": max(len(prices), len(g["productIds"])),
            "lprice": {
                "min": prices[0],
                "median": prices[len(prices) // 2],
                "max": prices[-1],
            } if prices else None,
            "variants": sorted(g["variants"]),
            "colors": sorted(g["colors"]),
            "skus": sorted(g["skus"]),
            "top_malls": g["malls"].most_common(3),
            "example_title": g["example_title"],
            "example_link": g["example_link"],
        })
    models_out.sort(key=lambda m: m["count"], reverse=True)

    return {
        "summary": shop.get("summary") or {},
        "models": models_out,
        "catalog_specs": _slim_catalog_specs(shop.get("catalog_specs") or []),
        "official_info": _slim_official(shop.get("official_info") or []),
        # L3 보강 — 다나와 정형 스펙(robots 허용 dsearch의 spec_list)
        "danawa_specs": [{"pcode": d.get("pcode"), "name": d.get("name"),
                          "spec": d.get("spec") or [], "features": d.get("features") or []}
                         for d in (shop.get("danawa_specs") or [])],
    }


def slimify_report(report: dict) -> dict:
    """리포트를 키워드별로 슬림화. insights는 그대로, shop만 축약."""
    return {
        kw: {
            "analyzed_count": kw_data.get("analyzed_count", 0),
            "sources": kw_data.get("sources", {}),
            "insights": kw_data.get("insights") or {},
            "shop": _slim_shop(kw_data.get("shop") or {}),
        }
        for kw, kw_data in report.items()
    }


# ──────────────────────────────────────────────────────────────────────────
# 2-c) 변형(수량/용량) 간 비교 — 고정/가변 속성 자동 분류 + 공통 인사이트
#      같은 제품의 변형(예: 신라면 120g 5/20/40/60개)을 base로 자동 그룹화하고,
#      속성값을 '제네릭하게' 비교해(속성명 하드코딩 없음) 고정/가변을 가린다.
#      개수는 spec이 아니라 키워드에서 파싱하고(다나와 spec은 개수와 무관하게 동일),
#      가변 수치(개당 단가 등)는 막대그래프로, 공통 인사이트는 LLM 클러스터링으로 뽑는다.
#      식품·비식품 일반화 — 단위 지식베이스(_UNIT_KB)로 모든 측정 차원을 자동 추출하고,
#      family에서 '값이 변하는 차원'이 곧 변형 축이 된다(개수·용량·크기·전력·배터리용량 등).
# ──────────────────────────────────────────────────────────────────────────


# ── 단위 지식베이스(차원→정규화) ────────────────────────────────────────────
# 식품·비식품을 횡단하는 '물리 단위'는 유한·안정적이다. 여기 등록된 단위만 측정으로
# 채택하므로 마케팅/모델 글루(3듀얼·2018테니스·001스포츠·9EX·30핀)는 KB에 없어 자동 탈락.
# 값: 단위(소문자/NFKC) → (차원키, 기준단위, 환산계수, 표시단위).
#   기준단위/계수 = 비교·정렬용 정규화(1kg=1000g, 1L=1000ml). 표시단위 = 사람이 보는 원래 단위(kg/L/인치).
_UNIT_KB = {
    # 질량
    "mg": ("mass", "g", 1e-3, "mg"), "g": ("mass", "g", 1.0, "g"), "kg": ("mass", "g", 1e3, "kg"),
    "lb": ("mass", "g", 453.592, "lb"), "lbs": ("mass", "g", 453.592, "lb"),
    "oz": ("mass", "g", 28.3495, "oz"),
    # 부피
    "ml": ("volume", "ml", 1.0, "ml"), "cc": ("volume", "ml", 1.0, "cc"),
    "l": ("volume", "ml", 1e3, "L"), "fl": ("volume", "ml", 29.5735, "fl"),
    # 길이
    "mm": ("length", "mm", 1.0, "mm"), "cm": ("length", "mm", 10.0, "cm"),
    "m": ("length", "mm", 1e3, "m"), "in": ("length", "mm", 25.4, "in"),
    "인치": ("length", "mm", 25.4, "인치"),
    # 전기·디지털(비식품 비교축)
    "w": ("power", "W", 1.0, "W"), "kw": ("power", "W", 1e3, "kW"), "a": ("current", "A", 1.0, "A"),
    "v": ("voltage", "V", 1.0, "V"), "mah": ("capacity", "mAh", 1.0, "mAh"),
    "wh": ("energy", "Wh", 1.0, "Wh"), "hz": ("refresh", "Hz", 1.0, "Hz"),
    "gb": ("storage", "GB", 1.0, "GB"), "tb": ("storage", "GB", 1024.0, "TB"),
    "mb": ("storage", "GB", 1 / 1024.0, "MB"), "ms": ("response", "ms", 1.0, "ms"),
    # 개수(한글 셀 수 단위) — 차원키를 단위별로 분리(개≠매≠정)해 서로 섞이지 않게
    "개": ("count", "개", 1.0, "개"), "입": ("count", "개", 1.0, "입"), "개입": ("count", "개", 1.0, "개입"),
    "매": ("count_매", "매", 1.0, "매"), "장": ("count_장", "장", 1.0, "장"),
    "정": ("count_정", "정", 1.0, "정"), "캡슐": ("count_캡슐", "캡슐", 1.0, "캡슐"),
    "봉": ("count_봉", "봉", 1.0, "봉"), "봉지": ("count_봉", "봉", 1.0, "봉지"),
    "캔": ("count_캔", "캔", 1.0, "캔"), "팩": ("count_팩", "팩", 1.0, "팩"),
    "병": ("count_병", "병", 1.0, "병"), "포": ("count_포", "포", 1.0, "포"),
    "롤": ("count_롤", "롤", 1.0, "롤"), "겹": ("count_겹", "겹", 1.0, "겹"),
    "족": ("count_족", "족", 1.0, "족"), "켤레": ("count_족", "족", 1.0, "켤레"),
    "줄": ("count_줄", "줄", 1.0, "줄"), "구": ("count_구", "구", 1.0, "구"),
    "미": ("count_미", "미", 1.0, "미"), "마리": ("count_마리", "마리", 1.0, "마리"),
    "박스": ("count_박스", "박스", 1.0, "박스"), "세트": ("count_세트", "세트", 1.0, "세트"),
    "단계": ("count_단계", "단계", 1.0, "단계"),
}
_UNIT_SYNONYM = {"floz": "fl", "inch": "in", "리터": "l", "그램": "g", "킬로그램": "kg"}
# 모호 단위(한 글자/흔한 알파벳) — 단독으론 오탐 위험. family에서 2개+ 변형이 같은 차원을
# 가질 때만 변형 축으로 채택한다(예: 'm'이 한 변형에만 있으면 축으로 쓰지 않음).
_UNIT_AMBIG = {"g", "w", "a", "m", "in", "v"}
_DIM_LABEL = {
    "mass": "중량", "volume": "용량", "length": "크기", "power": "소비전력", "current": "전류",
    "voltage": "전압", "capacity": "배터리용량", "energy": "에너지", "refresh": "주사율",
    "storage": "저장용량", "response": "응답속도", "count": "개수", "count_매": "매수",
    "count_장": "장수", "count_정": "정수", "count_캡슐": "캡슐수", "count_봉": "봉수",
    "count_캔": "캔수", "count_팩": "팩수", "count_병": "병수", "count_포": "포수",
    "count_롤": "롤수", "count_겹": "겹수", "count_족": "족수", "count_줄": "줄수",
    "count_구": "구수", "count_미": "미수", "count_마리": "마리수", "count_박스": "박스수",
    "count_세트": "세트수", "count_단계": "단계",
}
_RES_RE = re.compile(r"\d[\d,]*\s*[xX]\s*\d[\d,]*")        # 해상도 1920x1080 — 측정 아님, 제거
# 포장 곱셈기호: '210gX36개'·'130G*6번들'처럼 (숫자+단위) 뒤 x/*/× 가 개수 숫자에 붙으면
# 경계 조건 때문에 둘 다 매칭 실패한다 → 공백으로 분리. 숫자로 시작하는 단위 뒤에서만
# 동작하므로 모델명('아이폰X 256')은 건드리지 않는다.
_PACK_X_RE = re.compile(r"(\d[\d.,]*[A-Za-z가-힣]{1,3})\s*[xX*×]\s*(?=\d)")
# 일반 '숫자+단위' 포착: 앞뒤 경계로 모델번호(아이폰15·갤럭시S24) 오포착을 막는다.
_PAIR_RE = re.compile(
    r"(?<![A-Za-z0-9.])(\d[\d,]*(?:\.\d+)?)\s*(인치|[A-Za-z가-힣]{1,3})(?![A-Za-z0-9])")


def _unit_lookup(suffix: str):
    """접미 토큰 → (차원키, 표준표기, 환산계수). 최장접두 매칭으로 '개입'>'개'를 우선.
    KB·동의어에 없으면 (None, None) — 마케팅 글루(듀얼/스포츠)는 여기서 걸러진다."""
    s = _UNIT_SYNONYM.get(suffix.lower(), suffix.lower())
    for end in range(len(s), 0, -1):
        if s[:end] in _UNIT_KB:
            return s[:end], _UNIT_KB[s[:end]]
    return None, None


def extract_measures(name: str) -> Tuple[dict, list, str]:
    """상품명/키워드에서 '모든' (수치,단위) 쌍을 차원별로 추출 — 식품·비식품 무관.
    KB에 등록된 단위만 채택(노이즈 자동 탈락), 기준단위로 정규화(base_value: 1L→1000, 1kg→1000g).
    반환: ({dim: {raw,value,base_value,unit,ambig}}, [매칭 span], 정규화된 작업문자열)."""
    work = _RES_RE.sub("  ", unicodedata.normalize("NFKC", name))
    work = _PACK_X_RE.sub(r"\1 ", work)   # 210gX36개 → 210g 36개
    measures: dict = {}
    spans: list = []
    for m in _PAIR_RE.finditer(work):
        key, ent = _unit_lookup(m.group(2))
        if ent is None:
            continue
        dim, base_unit, factor, disp = ent
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if dim not in measures:                    # 차원당 첫 등장만(중복 방지)
            num = m.group(1).replace(",", "")
            measures[dim] = {
                "raw": num + disp, "value": val, "base_value": val * factor,
                "unit": disp, "base_unit": base_unit, "dim": dim,
                "ambig": key in _UNIT_AMBIG,
            }
        spans.append(m.span())
    return measures, spans, work


def _is_measure_tag(s: str) -> bool:
    """문자열 '전체'가 수치+단위 태그인지(다나와 features의 1.05kg/315g/40개 제거용)."""
    work = _RES_RE.sub("", unicodedata.normalize("NFKC", s.strip()))
    m = _PAIR_RE.fullmatch(work)
    if not m:
        return False
    _, ent = _unit_lookup(m.group(2))
    return ent is not None


def _strip_measures(text: str) -> str:
    """KB로 인식되는 모든 '수치+단위' 토큰을 제거 — 관련성 게이트(_danawa_relevant)가
    측정 표기를 빼고 'sub-type 수식어'만 남기도록. 게이트와 추출기가 단위 어휘를 공유하게 한다
    (그래야 27인치/60캡슐/3켤레/4단계 같은 한글 단위 상품이 게이트에서 잘못 탈락하지 않음)."""
    def _repl(m):
        _, ent = _unit_lookup(m.group(2))
        return " " if ent is not None else m.group(0)
    return _PAIR_RE.sub(_repl, unicodedata.normalize("NFKC", text))


def _parse_variant(keyword: str) -> dict:
    """키워드 → {base, measures, volume, count, count_unit}. base는 모든 측정을 떼어낸 제품 식별자.
    measures가 1차 산출(차원 무관 일반화). volume/count는 하위호환용 파생값(부피>질량, 개수형 첫개)."""
    measures, spans, work = extract_measures(keyword)
    base = work
    for a, b in sorted(spans, reverse=True):
        base = base[:a] + " " + base[b:]
    base = re.sub(r'\s+', ' ', base).strip() or keyword
    vol = measures.get("volume") or measures.get("mass")
    cnt = next((measures[d] for d in measures if d.startswith("count")), None)
    return {
        "base": base, "measures": measures,
        "volume": vol["raw"] if vol else None,
        "count": int(cnt["value"]) if cnt else None,
        "count_unit": cnt["unit"] if cnt else None,
    }


def _variant_attrs(v: dict, shop: dict) -> dict:
    """한 변형의 정형 속성을 모은다 — 세 종류.
    (1) 측정 차원(키워드 파싱): 추출된 모든 차원을 라벨로(중량/용량/개수/소비전력/배터리용량…).
        차원·단위를 하드코딩하지 않으므로 식품(kg·개)이든 비식품(W·mAh·인치)이든 자동.
    (2) 가격·단가(shop): 가격 중앙값 + 개당 단가(개수형) 또는 100단위당 가격(부피/질량형).
    (3) 제품 정체성: 변형의 다나와 상품들(≤5개) 중 'majority(과반)'인 features만(카테고리형).
        향·sub-type 노이즈는 소수라 탈락(도브=바디워시·보습 / 비비고=만두·고기만두).
        ※ 다나와 수치 영양정보는 같은 라인서도 패키지마다 갈려 비교에서 제외(danawa_specs엔 유지).
        ※ 측정 태그(1.05kg/40개)는 (1)과 중복이라 features에서 뺀다(_is_measure_tag)."""
    attrs: dict = {}
    measures = v.get("measures") or {}
    for dim, d in measures.items():
        attrs[_DIM_LABEL.get(dim, dim)] = d["raw"]
    price = ((shop.get("summary") or {}).get("price") or {}).get("median")
    if price:
        attrs["가격(중앙값)"] = price
        cnt = next((measures[d] for d in measures if d.startswith("count")), None)
        vol = measures.get("volume") or measures.get("mass")
        if cnt and cnt["value"]:
            attrs["개당 단가"] = round(price / cnt["value"])
        elif vol and vol["base_value"]:
            label = f'100{vol["base_unit"]}당 가격'     # 100g당 / 100ml당 (기준단위로 정규화)
            attrs[label] = round(price / vol["base_value"] * 100)
    # 제품 정체성 — 변형의 다나와 상품들 중 '과반(majority)'인 features만(노이즈/측정태그 제거)
    ds = shop.get("danawa_specs") or []
    n = len(ds)
    if n:
        feat_cnt: Counter = Counter()
        for d in ds:
            for f in d.get("features") or []:
                fs = f.strip()
                if _is_measure_tag(fs):
                    continue                       # 1.05kg/315g/40개 → 측정 차원과 중복
                feat_cnt[fs] += 1
        thresh = (n + 1) // 2                       # 과반(절반 이상)
        for feat, c in feat_cnt.items():
            if c >= thresh:
                attrs[feat] = "있음"
    return attrs


def _classify_attrs(per_kw_attrs: Dict[str, dict]) -> Tuple[dict, dict]:
    """변형별 속성 dict들을 모아 고정(모두 같음)/가변(다름)으로 분류.
    속성명을 하드코딩하지 않고 값만 비교한다(어떤 식품이 와도 자동).
    반환: (fixed{key:value}, variable{key:{kw:value}})."""
    keys: set = set()
    for a in per_kw_attrs.values():
        keys |= set(a.keys())
    n_variants = len(per_kw_attrs)
    fixed, variable = {}, {}
    for k in sorted(keys):
        vals = {kw: a.get(k) for kw, a in per_kw_attrs.items()}
        present = [val for val in vals.values() if val is not None]
        distinct = {str(val) for val in present}
        # 고정 = '모든' 변형에 '같은 값'으로 존재해야 함. 값이 갈리거나(맛·가격) 일부
        # 변형에만 있으면(도브 400ml만 유아동용=베이비 라인) 구분 속성이므로 가변으로.
        if len(distinct) >= 2 or len(present) < n_variants:
            variable[k] = vals                 # 값이 다르거나 일부에만 있음 → 가변(구분 속성)
        elif present:
            fixed[k] = present[0]              # 모든 변형에 동일하게 존재 → 고정
    return fixed, variable


def _graph_series(members: List[dict], variable: dict) -> List[dict]:
    """변형 축을 '측정 차원' 수준에서 자동 판별 — family에서 값이 변하는 차원이 곧 변형 축.
    차원/단위를 하드코딩하지 않으므로 개수(신라면)·용량(비비고)·크기(모니터 인치)·전력(충전기 W)
    무엇이 와도 자동. 가변 수치 속성(가격/단가/단위가격)을 그 축 순서로 막대그래프.
    반환: [{attr, unit, axis, bars:[{label,value}]}]."""
    dim_vals: Dict[str, dict] = {}             # dim -> {keyword: measure-dict}
    for m in members:
        for dim, d in (m.get("measures") or {}).items():
            dim_vals.setdefault(dim, {})[m["keyword"]] = d
    varying = []                               # 값이 2종 이상으로 '변하는' 차원만 후보
    for dim, vals in dim_vals.items():
        if len({round(d["base_value"], 4) for d in vals.values()}) < 2:
            continue
        if any(d["ambig"] for d in vals.values()) and len(vals) < 2:
            continue                           # 모호 단위는 2변형+ 일치해야 축 채택
        varying.append(dim)
    if not varying:
        return []
    # 변형 축: 개수형이 변하면 우선, 아니면 변하는 물리 차원 첫개
    axis_dim = next((d for d in varying if d.startswith("count")), varying[0])
    vals = dim_vals[axis_dim]
    ordered = sorted((m for m in members if m["keyword"] in vals),
                     key=lambda m: vals[m["keyword"]]["base_value"])
    axis_label = _DIM_LABEL.get(axis_dim, axis_dim)
    series = []
    for attr, av in variable.items():          # 가격/단가/단위가격(수치 outcome)만 그래프
        if not ("가격" in attr or "단가" in attr):
            continue
        bars = []
        for m in ordered:
            val = av.get(m["keyword"])
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                bars.append({"label": vals[m["keyword"]]["raw"], "value": val})
        if len(bars) >= 2:
            series.append({"attr": attr, "unit": "원", "axis": axis_label, "bars": bars})
    return series


class SharedInsight(BaseModel):
    category: str = Field(description="속성 분류(예: 맛, 질감, 연령, 취향, 장점, 우려)")
    point: str = Field(
        description="이 공통 인사이트의 대표 문장. 아래 주어진 문장 중에서만 고르고 새로 만들거나 의역하지 마세요")
    member_points: List[str] = Field(
        description="이 공통 인사이트와 '의미가 같은' 원본 문장 전부 — 표현이 달라도(예: '집에서 간편 조리' / "
                    "'집에서 간편히 즐김') 같은 뜻이면 모두 포함. 반드시 아래 주어진 문장 그대로(의역·창작 금지). "
                    "대표 문장(point)도 여기 포함하세요.")
    variants: List[str] = Field(description="이 인사이트가 나타난 변형 키워드들(2개 이상)")


class SharedInsights(BaseModel):
    shared: List[SharedInsight]


SHARED_PROMPT = """다음은 같은 제품의 여러 '변형(수량·용량 차이)'에서 각각 추출된 인사이트 목록입니다.
변형과 무관하게 **공통으로** 나타나는 인사이트만 묶어 정리하세요(맛·연령·취향처럼 변형이 달라도 같은 것).

규칙:
- 아래에 **주어진 인사이트 문장 안에서만** 고르세요. 새로 만들거나 의역·창작하지 마세요.
- **의미가 같으면 표현이 달라도 하나의 공통 인사이트로 묶으세요**(예: '집에서 간편하게 조리'와
  '집에서 간편히 즐길 수 있는'은 같은 뜻 → 하나로). 묶은 원본 문장은 member_points에 **전부 그대로** 넣으세요.
- **2개 이상의 변형**에 공통으로 나타나는 것만 포함하세요(한 변형에만 있으면 제외).
- category는 맛/질감/연령/취향/장점/우려 등 속성 분류로 적으세요.
- variants에는 그 인사이트가 나타난 변형 키워드를 그대로 적으세요.

--- 변형별 인사이트 ---
{snippets}
--- 끝 ---
"""


def _collect_taxonomy_points(tax: dict) -> List[Tuple[str, str]]:
    """taxonomy(계층)에서 (분류라벨, point) 목록을 평탄화 — 공통 인사이트 입력용."""
    out: List[Tuple[str, str]] = []
    for grp, dims in (tax.get("context") or {}).items():
        if isinstance(dims, dict):
            for dim, pts in dims.items():
                for p in pts or []:
                    out.append((dim, p["point"]))
    for dim, pts in (tax.get("aspect") or {}).items():
        for p in pts or []:
            out.append((dim, p["point"]))
    vd = tax.get("verdict") or {}
    for key in ("strengths", "weaknesses"):
        for p in vd.get(key) or []:
            out.append((key, p["point"]))
    return out


def _shared_insights(members: List[dict], unstructured: dict,
                     client: OpenAI) -> List[dict]:
    """변형 family의 '검증된' taxonomy point를 LLM으로 클러스터/dedup → 공통 인사이트.
    검증 통과 point만 입력하므로(근거추적 유지) 환각 레이어를 덧대지 않는다.
    LLM은 새로 만들지 않고 주어진 point 중 공통인 것만 묶는다."""
    lines = []
    for m in members:
        kw = m["keyword"]
        pts = _collect_taxonomy_points((unstructured.get(kw) or {}).get("taxonomy") or {})
        if not pts:
            continue
        lines.append(f"[{kw}]")
        for cat, pt in pts:
            lines.append(f"  - ({cat}) {pt}")
    blocks = "\n".join(lines)
    if not blocks.strip():
        return []
    try:
        si = _parse_sourced("", blocks, SharedInsights, SHARED_PROMPT, client)
    except Exception:
        return []
    # 2개 이상 변형을 지지하는 것만(LLM이 규칙을 어겨도 방어). member_points엔 대표 point도 포함 보장.
    return [{"category": s.category, "point": s.point,
             "member_points": sorted(set([s.point, *(s.member_points or [])])),
             "variants": s.variants}
            for s in si.shared if len(s.variants) >= 2]


# ── 변형 트리(스펙 필터 경로) + ProductGroup JSON-LD — GEO 데이터화 ──────────
# UN/CEFACT 공통코드(표시단위 기준) — JSON-LD QuantitativeValue.unitCode용(rich result).
_UNIT_CODE = {
    "kg": "KGM", "g": "GRM", "mg": "MGM", "lb": "LBR", "oz": "ONZ",
    "ml": "MLT", "cc": "CMQ", "L": "LTR", "l": "LTR",
    "mm": "MMT", "cm": "CMT", "m": "MTR", "in": "INH", "인치": "INH",
    "W": "WTT", "kW": "KWT", "A": "AMP", "V": "VLT", "Hz": "HTZ",
    "GB": "E34", "TB": "E35",
}


def _variant_sig(measures: dict) -> frozenset:
    """변형의 '스펙 시그니처' = (차원, 기준값) 집합. 포함관계(⊂)로 트리 부모-자식을 잇는다.
    예: '1.4kg'={(mass,1400)} ⊂ '1.4kg 10개'={(mass,1400),(count,10)} → 후자가 자식."""
    return frozenset((dim, round(d["base_value"], 4))
                     for dim, d in (measures or {}).items())


def _node_spec_labels(measures: dict) -> dict:
    """measures → {차원라벨: 표시값} (예: {'중량':'1.4kg','개수':'10개'})."""
    return {_DIM_LABEL.get(dim, dim): d["raw"] for dim, d in (measures or {}).items()}


def _agg_rating(kw: str, report: dict, unstructured: dict) -> Optional[dict]:
    """ProductGroup variant의 aggregateRating — 다나와 집계 우선, 없으면 수집 후기 평점 집계.
    (1) shop.danawa_meta(vssearch starPoint/reviewCount = 다나와 전체집계) — 가장 신뢰.
    (2) 폴백: source_index의 다나와 후기 rating 평균(우리가 수집한 샘플, 3건 이상일 때만)."""
    meta = ((report.get(kw) or {}).get("shop") or {}).get("danawa_meta") or {}
    sp, rc = meta.get("star_point"), meta.get("review_count")
    try:
        sp = float(sp) if sp not in (None, "", "0") else None
    except (ValueError, TypeError):
        sp = None
    if sp and rc:
        return {"@type": "AggregateRating", "ratingValue": round(sp, 1),
                "reviewCount": int(rc), "bestRating": 5, "worstRating": 1}
    si = (unstructured.get(kw) or {}).get("source_index") or {}
    ratings = [s["rating"] for s in si.values()
               if isinstance(s, dict) and isinstance(s.get("rating"), (int, float))]
    if len(ratings) >= 3:
        return {"@type": "AggregateRating",
                "ratingValue": round(sum(ratings) / len(ratings), 1),
                "reviewCount": len(ratings), "bestRating": 5, "worstRating": 1}
    return None


def _build_variant_tree(members: List[dict], report: dict,
                        unstructured: dict, shared: List[dict]) -> dict:
    """변형들을 '스펙 필터 경로' 트리로 조립 — 스펙을 하나씩 더 거는 포함관계가 부모→자식.
    · 루트 고정(fixed) = 모든 변형 공통 인사이트(shared) — 상속되어 자식에선 반복하지 않음.
    · 노드 차별(distinctive) = 그 스펙으로 조건을 걸어야 비로소 나타나는 후기
      (= 자기 taxonomy point − 조상경로·shared의 여집합). LLM 추가호출 없이 집합차로 계산.
    · collapse: 후기 차별점이 없는 leaf는 접는다(빈약한 노드는 doorway/스팸 신호 → GEO 역효과).
    base 키워드(스펙 0개)가 없으면 가상 루트로 형제 변형들을 묶는다."""
    base = members[0]["base"]
    # 공통 인사이트의 '모든 표현'(대표 + 의미동일 member)을 제외 집합으로 — near-paraphrase가
    # 자식 distinctive로 새는 것을 막는다(_shared_insights LLM 클러스터링과 짝).
    shared_pts = set()
    for s in shared:
        shared_pts.add(s["point"])
        shared_pts.update(s.get("member_points") or [])
    nodes = []
    for m in members:
        kw = m["keyword"]
        measures = m.get("measures") or {}
        tax = (unstructured.get(kw) or {}).get("taxonomy") or {}
        spec = _node_spec_labels(measures)
        nodes.append({
            "keyword": kw,
            "label": "·".join(spec.values()) or base,
            "spec": spec,
            "sig": _variant_sig(measures),
            "points": _collect_taxonomy_points(tax),
            "review_count": (report.get(kw) or {}).get("analyzed_count", 0),
            "attributes": _variant_attrs(m, (report.get(kw) or {}).get("shop") or {}),
            "children": [],
        })
    # 가상 루트(빈 sig=base 키워드) 없으면 생성해 형제들을 묶는다
    root = next((n for n in nodes if not n["sig"]), None)
    if root is None:
        root = {"keyword": None, "label": base, "spec": {}, "sig": frozenset(),
                "points": [], "review_count": 0, "attributes": {}, "children": []}
    # 부모 결정 — sig의 '최대 진부분집합'인 노드(가장 가까운 상위 스펙). 없으면 루트.
    for n in nodes:
        if n is root:
            continue
        cands = [o for o in nodes if o is not n and o["sig"] < n["sig"]]
        parent = max(cands, key=lambda o: len(o["sig"])) if cands else root
        parent["children"].append(n)

    # 패스 1 — 차별 후기 채우기(조상 경로 누적 point 제외). depth≥2에서 부모 point도 상속 제외.
    def _fill(node, ancestor_pts):
        node["distinctive"] = [{"category": c, "point": p} for c, p in node["points"]
                               if p not in ancestor_pts]
        nxt = ancestor_pts | {p for _, p in node["points"]}
        for c in node["children"]:
            _fill(c, nxt)

    # 패스 2 — 형제 공통 승격(bottom-up): ≥2 형제에 나타나는 distinctive는 '차별'이 아니라
    # 그 부모 레벨의 공통이므로 부모로 끌어올린다(_shared_insights가 못 묶은 의미중복 방어).
    def _promote(node, is_root):
        for c in node["children"]:
            _promote(c, False)
        kids = node["children"]
        if not kids:
            return
        cnt = Counter(d["point"] for c in kids for d in c["distinctive"])
        common = {p for p, n in cnt.items() if n >= 2}
        if not common:
            return
        objs, taken = [], set()
        for c in kids:
            for d in c["distinctive"]:
                if d["point"] in common and d["point"] not in taken:
                    objs.append(d)
                    taken.add(d["point"])
        bucket = node["fixed"] if is_root else node["distinctive"]
        exist = {x["point"] for x in bucket}
        bucket.extend(o for o in objs if o["point"] not in exist)
        for c in kids:
            c["distinctive"] = [d for d in c["distinctive"] if d["point"] not in common]

    # 패스 3 — collapse: 차별 후기 없는 leaf 제거(빈약한 노드 = doorway/스팸 신호 → GEO 역효과).
    def _prune(node):
        node["children"] = [c for c in node["children"] if _prune(c)]
        node.pop("sig", None)
        node.pop("points", None)
        return bool(node["children"]) or bool(node["distinctive"]) or node.get("keyword") is None

    root_pts = {p for _, p in root["points"]}
    root["fixed"] = list(shared)          # 루트 = 모든 변형 공통(상속)
    root["distinctive"] = []
    for c in root["children"]:
        _fill(c, shared_pts | root_pts)
    _promote(root, True)                  # 형제 공통은 루트 fixed로 승격
    root.pop("sig", None)
    root.pop("points", None)
    root["children"] = [c for c in root["children"] if _prune(c)]
    return root


def _variant_group_jsonld(base: str, members: List[dict],
                          report: dict, unstructured: dict) -> dict:
    """변형 family → schema.org ProductGroup(+hasVariant Product[]). GEO/AI검색이 바로 읽음.
    각 variant: 측정값(additionalProperty + QuantitativeValue), 가격(offers), 평점(aggregateRating).
    variesBy = 변형 간 달라지는 차원 라벨 — 어떤 카테고리든 측정 KB에서 자동 도출."""
    # variesBy는 '변형 간 실제로 달라지는' 차원만(불변 차원 제외 — schema.org 규약).
    # 예: 도브는 전부 1L → 용량은 variesBy 제외, 개수만. (용량은 additionalProperty엔 유지)
    dim_vals: Dict[str, set] = {}
    for m in members:
        for dim, d in (m.get("measures") or {}).items():
            dim_vals.setdefault(dim, set()).add(round(d["base_value"], 4))
    varying = {dim for dim, vals in dim_vals.items() if len(vals) >= 2}

    variants, varies = [], set()
    for m in members:
        kw = m["keyword"]
        measures = m.get("measures") or {}
        shop = (report.get(kw) or {}).get("shop") or {}
        prod = {"@type": "Product", "name": kw}
        props = []
        for dim, d in measures.items():
            label = _DIM_LABEL.get(dim, dim)
            if dim in varying:
                varies.add(label)
            qv = {"@type": "QuantitativeValue", "value": d["value"], "unitText": d["unit"]}
            code = _UNIT_CODE.get(d["unit"])
            if code:
                qv["unitCode"] = code
            props.append({"@type": "PropertyValue", "name": label,
                          "value": d["raw"], "valueReference": qv})
        if props:
            prod["additionalProperty"] = props
        price = ((shop.get("summary") or {}).get("price") or {}).get("median")
        if price:
            prod["offers"] = {"@type": "Offer", "price": price, "priceCurrency": "KRW"}
        ar = _agg_rating(kw, report, unstructured)
        if ar:
            prod["aggregateRating"] = ar
        variants.append(prod)
    out = {
        "@context": "https://schema.org/",
        "@type": "ProductGroup",
        "name": base,
        "hasVariant": variants,
    }
    if varies:
        out["variesBy"] = sorted(varies)
    return out


def _josa(word: str, pair: Tuple[str, str]) -> str:
    """한글 받침에 맞는 조사 선택. pair=(받침있을때, 받침없을때) 예: ('은','는')/('이','가')/('을','를')."""
    if not word:
        return pair[1]
    last = word[-1]
    if "가" <= last <= "힣":
        return pair[0] if (ord(last) - 0xAC00) % 28 else pair[1]
    return pair[1]


def _geo_summary(base: str, members: List[dict], report: dict, shared: List[dict]) -> str:
    """정의 우선(GEO) 요약 1문장 — 제품명 + 실구매 평점/리뷰수 + 공통 강점 상위 3.
    AI 검색이 페이지 첫 문장을 답변 스니펫 후보로 추출하므로 핵심 사실을 앞에 모은다(근거 기반·템플릿)."""
    best_sp, best_rc = None, 0
    for m in members:
        meta = ((report.get(m["keyword"]) or {}).get("shop") or {}).get("danawa_meta") or {}
        try:
            rc = int(meta.get("review_count")) if meta.get("review_count") else 0
        except (ValueError, TypeError):
            rc = 0
        if rc > best_rc:
            best_rc = rc
            try:
                best_sp = float(meta.get("star_point")) if meta.get("star_point") not in (None, "", "0") else None
            except (ValueError, TypeError):
                best_sp = None
    pri = ("strengths", "price_range", "장점", "강점")
    strong = [s["point"] for s in shared if s["category"] in pri]
    strong += [s["point"] for s in shared if s["category"] not in pri]
    strong = strong[:3]
    nun = _josa(base, ("은", "는"))
    rating = f"실구매 {best_rc:,}명 평가 ★{best_sp} " if (best_sp and best_rc) else ""
    feat = ""
    if strong:
        ga = _josa(strong[-1], ("이", "가"))
        feat = f"{', '.join(strong)}{ga} 강점입니다"
    if rating and feat:
        return f"{base}{nun} {rating}제품으로, {feat}."
    if rating:
        return f"{base}{nun} {rating}제품입니다."
    if feat:
        return f"{base}{nun} {feat}."
    return ""


def build_comparison(report: dict, unstructured: dict, client: OpenAI) -> List[dict]:
    """변형 키워드를 base로 자동 그룹화해 family별 비교를 만든다.
    family = 같은 base의 변형 2개 이상. 단일 키워드만 있으면 비교 대상이 아니다.
    각 family에 '스펙 필터 경로 트리'(고정 상속 + 스펙별 차별 후기), ProductGroup JSON-LD,
    정의 우선 요약을 함께 만든다 — GEO 데이터화용(질의 구체성 매칭 + 중복 제거 + rich result)."""
    families: Dict[str, List[dict]] = {}
    for kw in report:
        v = _parse_variant(kw)
        families.setdefault(v["base"], []).append({"keyword": kw, **v})
    out = []
    for base, members in families.items():
        if len(members) < 2:
            continue
        per_kw = {m["keyword"]: _variant_attrs(m, report[m["keyword"]].get("shop") or {})
                  for m in members}
        fixed, variable = _classify_attrs(per_kw)
        shared = _shared_insights(members, unstructured, client)
        summary = _geo_summary(base, members, report, shared)
        pg = _variant_group_jsonld(base, members, report, unstructured)
        if summary:
            pg["description"] = summary           # ProductGroup 설명 = 정의 우선 요약
        out.append({
            "base": base,
            "variants": [m["keyword"] for m in members],
            "summary": summary,                   # 정의 우선(GEO) 요약 1문장
            "fixed_attributes": fixed,            # 변형과 무관하게 동일(용량·영양정보 등)
            "variable_attributes": variable,      # 변형마다 다름(개수·가격 등)
            "graph": _graph_series(members, variable),
            "shared_insights": shared,
            "tree": _build_variant_tree(members, report, unstructured, shared),
            "product_group": pg,
        })
    return out


# ──────────────────────────────────────────────────────────────────────────
# 3) FAQ → schema.org FAQPage JSON-LD 변환 (GEO/AI 검색이 바로 읽는 형태)
# ──────────────────────────────────────────────────────────────────────────
def build_faq_jsonld(report: dict, product_name: str = "") -> dict:
    """키워드별 FAQ를 합쳐 하나의 FAQPage JSON-LD로 만든다 (질문 기준 중복 제거)."""
    seen = set()
    main_entity = []
    for kw_data in report.values():
        for f in kw_data["insights"].get("faqs", []):
            q = (f.get("question") or "").strip()
            a = (f.get("short_answer") or "").strip()
            key = q.lower()
            if not q or not a or key in seen:
                continue
            seen.add(key)
            main_entity.append({
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            })

    jsonld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": main_entity,
    }
    if product_name:
        jsonld["about"] = {"@type": "Product", "name": product_name}
    return jsonld


def to_html_snippet(jsonld: dict) -> str:
    """페이지 <head> 또는 <body>에 그대로 붙일 수 있는 JSON-LD 스크립트 태그."""
    return (
        '<script type="application/ld+json">\n'
        + json.dumps(jsonld, ensure_ascii=False, indent=2)
        + "\n</script>\n"
    )


def build_pdp_snippet(comparison: List[dict]) -> str:
    """자사 상품페이지(PDP)에 바로 붙이는 GEO 임베드 조각 — 네이버·구글 AI 검색 대상.
    구성(GEO 2026 권고 반영): ① 정의 우선 요약(첫 문장=AI 답변 스니펫 후보)
    ② 고정(공통) 특징 + 변형별 추가 후기를 추출 가능한 패시지로 ③ ProductGroup JSON-LD(rich result).
    근거 기반 표기('실구매 후기 기반')로 인용 신뢰 신호를 준다."""
    esc = html.escape

    def _node(n: dict) -> str:
        dist = n.get("distinctive") or []
        kids = "".join(_node(c) for c in n.get("children") or [])
        if not dist and not kids:
            return ""
        lis = "".join(f"<li>{esc(d['point'])}</li>" for d in dist)
        body = f"<ul>{lis}</ul>" if lis else ""
        return f"<h4>{esc(n.get('label', ''))}</h4>{body}{kids}"

    blocks = []
    for fam in comparison:
        tree = fam.get("tree") or {}
        summary = fam.get("summary") or ""
        lead = f'<p class="geo-lead"><strong>{esc(summary)}</strong></p>' if summary else ""
        fixed = tree.get("fixed") or []
        fx = ""
        if fixed:
            lis = "".join(f"<li>{esc(s['point'])}</li>" for s in fixed)
            fx = (f"<h3>{esc(fam.get('base', ''))} 공통 특징 "
                  f"<small>(실구매 후기 기반)</small></h3><ul>{lis}</ul>")
        variants_html = "".join(_node(c) for c in tree.get("children") or [])
        var_section = (f"<h3>용량·구성별 추가 특징</h3>{variants_html}"
                       if variants_html else "")
        jsonld = to_html_snippet(fam.get("product_group") or {})
        blocks.append(
            f'<section class="geo-pdp" itemscope>\n{lead}\n{fx}\n{var_section}\n{jsonld}</section>')
    return "<!-- GEO 임베드: 자사 상품페이지 <body>에 삽입 -->\n" + "\n".join(blocks) + "\n"


# ──────────────────────────────────────────────────────────────────────────
# 4) 파이프라인
# ──────────────────────────────────────────────────────────────────────────
def main() -> None:
    keywords = sys.argv[1:]
    if not keywords:
        print('사용법: python naver_review_geo.py "키워드1" "키워드2" ...')
        sys.exit(1)

    nid = os.environ.get("NAVER_CLIENT_ID")
    nsecret = os.environ.get("NAVER_CLIENT_SECRET")
    if not (nid and nsecret):
        print("환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 가 필요합니다.")
        print("→ https://developers.naver.com 에서 애플리케이션 등록 후 발급 (무료)")
        sys.exit(1)

    llm = OpenAI()  # OPENAI_API_KEY 환경변수 사용
    yt_key = os.environ.get("YOUTUBE_API_KEY")  # 선택 — 있으면 유튜브 댓글도 수집
    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY")  # 선택 — L4 공식몰 추출용
    if not brave_key:
        print("[안내] BRAVE_SEARCH_API_KEY 미설정 → L4(공식몰 정보) 생략")
    # INSIGHTS_ONLY=1 → 정형(쇼핑 L1~L4) 수집을 건너뛰고 비정형 인사이트만 추출
    insights_only = bool(os.environ.get("INSIGHTS_ONLY"))
    if insights_only:
        print("[모드] INSIGHTS_ONLY — 정형(쇼핑 L1~L4) 수집을 건너뜁니다. 비정형 인사이트만 추출.")
    report = {}
    unstructured_report: dict = {}   # 비정형 인사이트 + 출처/근거 (insights_unstructured.json)

    for kw in keywords:
        print(f"\n=== '{kw}' 수집 중 ===")

        # 1) 정형 — 네이버 쇼핑: L1(리스트) + L2(제목 정규화) + L3(catalog 스펙) + L4(공식몰)
        shop_items: List[dict] = []
        shop_summary: dict = {}
        catalog_specs: List[dict] = []
        official_info: List[dict] = []
        if not insights_only:
            try:
                shop_items = search_shop(kw, nid, nsecret, display=50)

                # L2 먼저 — 제목 정규화(attrs)를 끝낸 뒤 요약해야 모델별 가격/그룹이 정확
                if shop_items:
                    shop_items = normalize_titles(shop_items, llm)
                    with_brand = sum(1 for it in shop_items
                                     if (it.get("attrs") or {}).get("brand"))
                    print(f"  [L2] 제목 정규화 완료 · 브랜드 인식 {with_brand}/{len(shop_items)}건")

                shop_summary = summarize_shop(shop_items)
                med = shop_summary["price"].get("median")
                med_str = f"{med:,}원" if med else "N/A"
                dom = shop_summary.get("dominant_model")
                print(f"  [L1] 쇼핑 상품 {shop_summary['count']}건 · 가격 중앙값 {med_str}"
                      + (f" (모델 '{dom}' 기준)" if dom else ""))
                if shop_summary["top_malls"]:
                    top3 = ", ".join(f"{m}({c})" for m, c in shop_summary["top_malls"][:3])
                    print(f"       상위 몰: {top3}")

                # L3 — 가격비교 카탈로그(/catalog/ 링크) 상위 3건의 스펙 추출
                # 주의: 네이버 쇼핑 카탈로그는 requests·headless 모두 봇 차단(418/캡차)이라
                #       현재 스펙 확보가 사실상 불가 — 소재/원산지는 L4(공식몰)에서 보강한다.
                n_catalog = sum(1 for it in shop_items if is_catalog_item(it))
                if n_catalog:
                    target = min(n_catalog, 3)
                    print(f"  [L3] 가격비교 카탈로그 {target}건 스펙 추출 중...")
                    catalog_specs = enrich_with_specs(shop_items, llm, top_n=3)
                    ok = sum(1 for c in catalog_specs if c.get("spec"))
                    rated = sum(1 for c in catalog_specs
                                if c.get("spec") and c["spec"].get("rating"))
                    print(f"       스펙 추출 {ok}/{len(catalog_specs)}건 · 평점 확보 {rated}건")
                else:
                    print("  [L3] 가격비교 카탈로그(/catalog/) 항목이 없어 스펙 추출 생략")

                # L4 — attrs.brand+model로 공식몰 검색 → 상위 3건 정보 추출
                if brave_key and shop_items:
                    print("  [L4] 공식몰 정보 추출 중...")
                    official_info = enrich_with_official_site(
                        shop_items, llm, brave_key, top_n=3, keyword=kw,
                    )
                    ok = sum(1 for r in official_info
                             if r.get("official") and r["official"].get("is_product_page"))
                    print(f"       공식 페이지 매칭 {ok}/{len(official_info)}건")
            except requests.HTTPError as e:
                print(f"  [쇼핑 API 오류] {e}")

        # 2) 비정형 — 블로그/유튜브 텍스트
        items: List[dict] = []
        try:
            items += search_blog(kw, nid, nsecret, display=50)
        except requests.HTTPError as e:
            print(f"  [네이버 API 오류] {e}")

        if yt_key:
            try:
                yt_items = collect_youtube(kw, yt_key, n_videos=3, n_comments=50)
                items += yt_items
                n_rep = sum(1 for it in yt_items if it.get("kind") == "reply")
                print(f"  유튜브 댓글·답글 {len(yt_items)}건 수집 (답글 {n_rep}건)")
            except requests.HTTPError as e:
                print(f"  [유튜브 API 오류] {e}")

        # 다나와 검색 1회로 정형 스펙(L3 보강)+비정형 구매후기를 함께 확보(robots 허용 경로만).
        # spec_list는 네이버 카탈로그 봇차단으로 막힌 정형 스펙을 보강한다. DANAWA_OFF=1로 끔.
        danawa_prods: List[dict] = []
        danawa_meta: dict = {}
        if not os.environ.get("DANAWA_OFF"):
            try:
                danawa_prods = search_danawa(kw, top_n=5)          # 정형: pcode·상품명·스펙
                # vssearch JSON API — 대표 상품의 가격/평점/리뷰수 메타(다나와 전체집계).
                # ProductGroup JSON-LD의 offers/aggregateRating(GEO rich result)에 쓰인다.
                api_prods = search_danawa_api(kw, top_n=5)
                if api_prods:
                    danawa_meta = {k: api_prods[0].get(k) for k in
                                   ("pcode", "name", "min_price", "star_point", "review_count")}
                dn_items = []
                for prod in danawa_prods:
                    dn_items.extend(
                        fetch_danawa_reviews(prod["pcode"], prod["name"], max_pages=2))
                items += dn_items
                n_spec = sum(1 for d in danawa_prods if d.get("spec"))
                print(f"  다나와 구매후기 {len(dn_items)}건 + 정형 스펙 {n_spec}/{len(danawa_prods)}건 "
                      f"수집 (쇼핑몰 통합 상품평·dsearch 정형 — 한줄평/Q&A/커뮤니티는 robots 차단이라 제외)")
            except requests.RequestException as e:
                print(f"  [다나와 수집 오류] {e}")

        # 쇼핑 데이터만이라도 있으면 report에 보존 (LLM 분석은 텍스트 있을 때만)
        kept: List[dict] = []
        n_ad = 0
        n_blog = n_yt = n_dn = 0
        insights_dict: Optional[dict] = None

        if items:
            # 광고/협찬은 '제외'하지 않고 표기만 — 각 항목에 is_ad/ad_signals를 부착해 분석에 포함.
            for it in items:
                it["is_ad"] = is_ad(it)
                it["ad_signals"] = ad_signals(it)
            kept = items
            n_ad = sum(1 for it in kept if it["is_ad"])
            n_blog = sum(1 for it in kept if it.get("source") == "naver")
            n_yt = sum(1 for it in kept if it.get("source") == "youtube")
            n_dn = sum(1 for it in kept if it.get("source") == "danawa")
            print(f"  분석 대상 {len(kept)}건 (블로그 {n_blog}/유튜브 {n_yt}/다나와 {n_dn}) · "
                  f"광고/협찬 표기 {n_ad}건 (제외 안 함)")
            if kept:
                sourced, context, aspverd, id_map, n_drop = \
                    extract_sourced_insights(kw, kept, llm)
                block = build_sourced_block(sourced, context, aspverd, id_map, kept)
                unstructured_report[kw] = block
                # 공개 insights.json/faq.jsonld는 taxonomy 요약 + FAQ(근거검증 통과분만)
                insights_dict = _flatten_block(block)
                if n_drop:
                    print(f"  [근거추출] 입력 길이 제한으로 {n_drop}건은 인용 대상에서 제외")
                v = block["verification"]
                print(f"  [근거추출] 인용 검증 verified {v['verified']}/partial {v['partial']}"
                      f"/regrounded {v['regrounded']}"
                      f" · 회복(인용 {v['recovered_quote']}/ID {v['recovered_id']})"
                      f" · 미검증 제외 {v['dropped_unverified']}"
                      f"(인용환각 {v['dropped_quote_halluc']}/ID환각 {v['dropped_id_halluc']})"
                      f" · 질문 제외 {v['dropped_question']}"
                      f" · 근거 없어 제외된 인사이트 {v['insights_dropped']}")
            else:
                print("  분석할 텍스트가 없어 LLM 추출은 건너뜁니다.")
        else:
            print("  수집된 텍스트가 없어 LLM 추출은 건너뜁니다.")

        if not (kept or shop_items):
            continue

        report[kw] = {
            "analyzed_count": len(kept),
            "ad_flagged": n_ad,
            "sources": {"naver": n_blog, "youtube": n_yt, "danawa": n_dn},
            "insights": insights_dict or {},
            "shop": {
                "summary": shop_summary,
                "items": shop_items,
                "catalog_specs": catalog_specs,
                "official_info": official_info,
                "danawa_specs": danawa_prods,   # L3 보강 — 다나와 정형 스펙(robots 허용 dsearch)
                "danawa_meta": danawa_meta,     # vssearch 메타(가격/평점/리뷰수) — JSON-LD용
            },
        }

        # 콘솔 요약 (insights는 텍스트가 있었을 때만 존재)
        if insights_dict:
            if insights_dict.get("overall_recommendation"):
                print(f"  · 종합 추천: {insights_dict['overall_recommendation']}")
            if insights_dict.get("strengths"):
                print(f"  · 강점: {', '.join(insights_dict['strengths'][:3])}")
            if insights_dict.get("weaknesses"):
                print(f"  · 약점: {', '.join(insights_dict['weaknesses'][:3])}")
            if insights_dict.get("faqs"):
                print("  · FAQ:")
                for f in insights_dict["faqs"][:3]:
                    print(f"      Q. {f['question']}\n         A. {f['short_answer']}")

        time.sleep(0.3)  # API 매너 (rate limit 여유)

    # 재접지/재배정 복구 감사 — AUDIT_RECOVERY=1 일 때만.
    if os.environ.get("AUDIT_RECOVERY") and _RECOVERY_AUDIT:
        with open("recovery_audit.json", "w", encoding="utf-8") as fp:
            json.dump(_RECOVERY_AUDIT, fp, ensure_ascii=False, indent=2)
        uniq = sum(1 for r in _RECOVERY_AUDIT if r["n_sources_with_quote"] == 1)
        amb = len(_RECOVERY_AUDIT) - uniq
        print(f"\n[복구감사] {len(_RECOVERY_AUDIT)}건 → recovery_audit.json"
              f" · 유일(명백정답) {uniq} · 모호(원출처 불확실) {amb}")

    # 출력은 항상 슬림본 — shop.items 50건을 (brand, model) 그룹으로 압축.
    # 원본(raw) 보존이 필요하면 DUMP_RAW=1 환경변수로 .raw.json 같이 저장.
    slim = slimify_report(report)
    with open("insights.json", "w", encoding="utf-8") as fp:
        json.dump(slim, fp, ensure_ascii=False, indent=2)
    print(f"\n완료 → insights.json ({len(slim)}개 키워드)")

    # 변형(수량/용량) 간 비교 — base 자동 그룹화 → 고정/가변 속성 + 공통 인사이트 + 그래프
    comparison = build_comparison(report, unstructured_report, llm)
    if comparison:
        # 최신성(GEO recency 신호) — 생성일을 dateModified로 주입. 후기 재수집 주기와 연동.
        today = time.strftime("%Y-%m-%d")
        for c in comparison:
            if c.get("product_group"):
                c["product_group"]["dateModified"] = today

        with open("comparison.json", "w", encoding="utf-8") as fp:
            json.dump(comparison, fp, ensure_ascii=False, indent=2)
        n_var = sum(len(c["variants"]) for c in comparison)
        print(f"        + comparison.json (변형 비교 {len(comparison)}개 그룹 · 변형 {n_var}개)")

        # ProductGroup JSON-LD — 변형 family를 schema.org 구조로(GEO/AI검색 rich result)
        pgs = [c["product_group"] for c in comparison if c.get("product_group")]
        if pgs:
            payload = pgs[0] if len(pgs) == 1 else {
                "@context": "https://schema.org/", "@graph": pgs}
            with open("product_group.jsonld", "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            print(f"        + product_group.jsonld (ProductGroup {len(pgs)}개 그룹)")

        # 자사 상품페이지(PDP) 임베드 — 정의우선 요약 + 고정/차별 패시지 + JSON-LD
        pdp = build_pdp_snippet(comparison)
        if pdp.strip():
            with open("pdp_snippet.html", "w", encoding="utf-8") as fp:
                fp.write(pdp)
            print("        + pdp_snippet.html (자사 PDP 임베드용 — 네이버·구글 GEO)")

    # 비정형(블로그·유튜브) 인사이트는 '어디서 어떤 문장에서' 나왔는지 출처/근거를
    # 부착해 별도 파일로 저장 — 정형 데이터와 섞지 않는다.
    if unstructured_report:
        with open("insights_unstructured.json", "w", encoding="utf-8") as fp:
            json.dump(unstructured_report, fp, ensure_ascii=False, indent=2)
        print(f"        + insights_unstructured.json "
              f"(비정형 인사이트 + 출처/근거 {len(unstructured_report)}개 키워드)")
        # 근거·인사이트 + 변형 비교를 사람이 읽기 좋은 HTML 리포트로(LLM 재호출 없음)
        try:
            import make_report
            make_report.write_report(unstructured_report, comparison)
            print("        + insights_report.html (근거·인사이트 + 변형 비교 HTML)")
        except Exception as e:
            print(f"        [HTML 리포트 생성 건너뜀] {e}")

    if os.environ.get("DUMP_RAW"):
        with open("insights.raw.json", "w", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=2)
        print("        + insights.raw.json (원본)")

    # FAQ → FAQPage JSON-LD 변환
    product_name = os.environ.get("PRODUCT_NAME", "")  # 예: "나이키 에어포스 1 블랙"
    jsonld = build_faq_jsonld(report, product_name)
    n_faq = len(jsonld["mainEntity"])
    with open("faq.jsonld", "w", encoding="utf-8") as fp:
        json.dump(jsonld, fp, ensure_ascii=False, indent=2)
    with open("faq_snippet.html", "w", encoding="utf-8") as fp:
        fp.write(to_html_snippet(jsonld))
    print(f"완료 → faq.jsonld / faq_snippet.html (FAQ {n_faq}개, 중복 제거됨)")


if __name__ == "__main__":
    main()
