#!/usr/bin/env python3
"""
네이버 쇼핑검색 정식 API로 실데이터 수집 (의존성 없음, stdlib urllib).
business-model/naver_review_geo.py 의 search_shop 패턴을 그대로 따름.

키는 환경변수에서 읽음 (소스에 하드코딩하지 않음):
    export NAVER_CLIENT_ID=...        # https://developers.naver.com
    export NAVER_CLIENT_SECRET=...
    python3 naver_pull.py 싸이닉            # 기본 키워드
    python3 naver_pull.py 싸이닉 "싸이닉 토너" "싸이닉 선에센스"

출력: outputs/naver_<키워드>.json (원본 정형 아이템) + 콘솔 요약
      (몰별·productType별 집계, 가격비교 통합노드 표시)
"""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENDPOINT = "https://openapi.naver.com/v1/search/shop.json"
TAG_RE = re.compile(r"<[^>]+>")
PTYPE = {1: "일반", 2: "중고", 3: "대여", 4: "단종", 5: "판매중지", 6: "가격비교(통합)"}


def _int(v):
    try:
        return int(v) if v not in (None, "", "0") else None
    except (ValueError, TypeError):
        return None


def search_shop(keyword, cid, csec, display=100, sort="sim"):
    params = urllib.parse.urlencode({"query": keyword, "display": min(display, 100), "sort": sort})
    req = urllib.request.Request(ENDPOINT + "?" + params)
    req.add_header("X-Naver-Client-Id", cid)
    req.add_header("X-Naver-Client-Secret", csec)
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    out = []
    for it in data.get("items", []):
        out.append({
            "title": html.unescape(TAG_RE.sub("", it.get("title", ""))),
            "lprice": _int(it.get("lprice")), "hprice": _int(it.get("hprice")),
            "mallName": it.get("mallName", ""), "productId": it.get("productId", ""),
            "productType": _int(it.get("productType")),
            "brand": it.get("brand", ""), "maker": it.get("maker", ""),
            "category": " > ".join(c for c in (it.get("category1"), it.get("category2"),
                                               it.get("category3"), it.get("category4")) if c),
            "link": it.get("link", ""),
        })
    return out


def summarize(items, keyword):
    from collections import Counter
    malls = Counter(i["mallName"] for i in items if i["mallName"])
    ptypes = Counter(PTYPE.get(i["productType"], "기타") for i in items)
    prices = sorted(i["lprice"] for i in items if i["lprice"])
    print(f"\n[{keyword}]  아이템 {len(items)}")
    if prices:
        print(f"  최저가 분포: {prices[0]:,} ~ {prices[-1]:,}원  (중앙값 {prices[len(prices)//2]:,})")
    print(f"  productType: " + ", ".join(f"{k} {v}" for k, v in ptypes.most_common()))
    print(f"  상위 몰: " + ", ".join(f"{m}({c})" for m, c in malls.most_common(8)))
    print("  샘플:")
    for i in items[:8]:
        pc = "🔗통합" if i["productType"] == 6 else i["mallName"]
        print(f"    - {(i['title'][:40]):42} {str(i['lprice'] and format(i['lprice'],',')+'원'):>10}  [{pc}] {i['brand']}")


def main():
    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        print("✗ 인증키 없음. 먼저 환경변수를 설정하세요:")
        print("    export NAVER_CLIENT_ID=...")
        print("    export NAVER_CLIENT_SECRET=...")
        print("  (business-model 와 동일한 키 재사용 가능)")
        sys.exit(1)

    keywords = sys.argv[1:] or ["싸이닉"]
    out_dir = os.path.join(HERE, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    allitems = {}
    for kw in keywords:
        try:
            items = search_shop(kw, cid, csec)
        except urllib.error.HTTPError as e:
            print(f"✗ '{kw}' API 오류 {e.code}: {e.read()[:200]}")
            continue
        allitems[kw] = items
        summarize(items, kw)
        safe = re.sub(r"\s+", "_", kw)
        with open(os.path.join(out_dir, f"naver_{safe}.json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"\n저장: outputs/naver_*.json ({sum(len(v) for v in allitems.values())} 아이템)")


if __name__ == "__main__":
    main()
