#!/usr/bin/env python3
"""각 브랜드 공식몰 PDP에서 '뽑을 수 있는 모든 속성'을 샘플 1개로 인벤토리화.
공통 14컬럼이 아니라, JSON-LD 전체 키 + 고시/스펙 전 항목 + 옵션(컬러/사이즈) + 메타를
싹 긁어 outputs/attrs_<slug>.json 저장. attrs_viewer.html(독립형)로 본다.

  python3 harvest_attrs.py            # 30개 전수
  python3 harvest_attrs.py fila puma  # 특정 브랜드
"""
import csv
import html as H
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
import ocr_gosi as og  # http() (curl_cffi/urllib)

SLUGS = ["fila", "puma", "crocs", "underarmour", "lecaf", "jansport", "arena",
         "proworldcup", "kolping", "northface", "natgeo", "starsports", "skechers",
         "prospecs", "worldcup", "vans", "blackyak", "montbell", "millet", "nepa",
         "columbia", "redface", "mizuno", "westwood", "eider", "outdoorproducts",
         "nike", "nb", "adidas", "k2"]
DISPLAY = {"fila": "휠라", "puma": "푸마", "crocs": "크록스", "underarmour": "언더아머",
           "lecaf": "르까프", "jansport": "잔스포츠", "arena": "아레나", "proworldcup": "프로월드컵",
           "kolping": "콜핑", "northface": "노스페이스", "natgeo": "내셔널지오그래픽",
           "starsports": "스타스포츠", "skechers": "스케쳐스", "prospecs": "프로스펙스",
           "worldcup": "월드컵", "vans": "반스", "blackyak": "블랙야크", "montbell": "몽벨",
           "millet": "밀레", "nepa": "네파", "columbia": "컬럼비아", "redface": "레드페이스",
           "mizuno": "미즈노", "westwood": "웨스트우드", "eider": "아이더",
           "outdoorproducts": "아웃도어프로덕츠", "nike": "나이키", "nb": "뉴발란스",
           "adidas": "아디다스", "k2": "케이투"}


def flatten(o, prefix=""):
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            if k.startswith("@") and k != "@type":
                continue
            out.update(flatten(v, f"{prefix}{k}."))
    elif isinstance(o, list):
        if o and not isinstance(o[0], (dict, list)):
            out[prefix[:-1]] = ", ".join(str(x) for x in o[:10])
        else:
            for i, v in enumerate(o[:2]):
                out.update(flatten(v, f"{prefix}{i}."))
    else:
        if o not in (None, "", []):
            out[prefix[:-1]] = str(o)[:120]
    return out


def harvest(html):
    res = {"jsonld": {}, "gosi": {}, "meta": {}, "options": {}}
    # 1) JSON-LD 전체 객체 평탄화
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(m.group(1).strip())
        except Exception:
            continue
        for n in (j if isinstance(j, list) else [j]):
            if isinstance(n, dict):
                res["jsonld"].update(flatten(n))
    # 1b) Next.js __NEXT_DATA__ 의 상품 핵심(있으면)
    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        try:
            j = json.loads(nd.group(1))
            # selectedProduct / products 핵심만
            def find_prod(o, d=0):
                if d > 6 or not isinstance(o, dict):
                    return None
                if o.get("styleColor") or o.get("productCode") or o.get("styleCode"):
                    return o
                for v in o.values():
                    r = find_prod(v, d + 1) if isinstance(v, dict) else None
                    if r:
                        return r
                return None
            p = find_prod(j.get("props", {}))
            if p:
                for k in ("genders", "manufacturingCountriesOfOrigin", "sportTags",
                          "netQuantity", "colorDescription", "productType"):
                    if p.get(k):
                        res["jsonld"]["next." + k] = str(p[k])[:120]
        except Exception:
            pass
    # 2) 고시/스펙 라벨:값 (th/td, dt/dd, strong). 네비/메뉴 노이즈 제외.
    # 고시 라벨 화이트리스트(부분일치) — 실제 상품정보제공고시/스펙 항목만
    GOSI_OK = re.compile(r"소재|제조|원산|색상|사이즈|치수|중량|용량|품질|보증|"
                         r"성분|혼용|재질|규격|모델|상품명|color|size|material|origin|brand|"
                         r"세탁|취급|관리|KC|인증|배송|반품|교환|A/S|책임|업체|수입|판매원|"
                         r"무게|용도|출시|시즌")
    for lab, val in re.findall(r'<(?:th|dt|strong)[^>]*>\s*([가-힣A-Za-z /()]{2,16})\s*</(?:th|dt|strong)>\s*'
                               r'<(?:td|dd|div|span)[^>]*>(.*?)</', html, re.S):
        v = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", H.unescape(val))).strip()
        lab = lab.strip()
        # 노이즈: 값이 #으로 시작(해시태그/메뉴), 빈값, 너무 김, 라벨이 고시항목 아님
        if not v or v.startswith("#") or len(v) >= 100:
            continue
        if not GOSI_OK.search(lab):
            continue
        if lab not in res["gosi"]:
            res["gosi"][lab] = v
    # 3) 메타(og/product)
    for p, c in re.findall(r'<meta[^>]+(?:property|name)="((?:og|product):[^"]+)"[^>]+content="([^"]*)"', html):
        if c.strip():
            res["meta"][p] = c[:120]
    # 4) 옵션(컬러/사이즈) — select option / 컬러 스와치
    opts = re.findall(r'<option[^>]*value="[^"]+"[^>]*>([^<]{1,40})</option>', html)
    opts = [re.sub(r"\s+", " ", H.unescape(o)).strip() for o in opts]
    opts = [o for o in opts if o and not re.match(r"(선택|choose|^-+$)", o, re.I)][:30]
    if opts:
        res["options"]["select_options"] = opts
    return res


def main():
    slugs = sys.argv[1:] or SLUGS
    for slug in slugs:
        f = next((p for p in (f"extract_brand_{slug}.csv", f"extract_{slug}.csv")
                  if os.path.exists(os.path.join(OUT, p))), None)
        if not f:
            print(f"  {slug}: CSV 없음")
            continue
        rows = list(csv.DictReader(open(os.path.join(OUT, f), encoding="utf-8-sig")))
        if not rows:
            continue
        sample = rows[0]
        try:
            html = og.http(sample["url"])
            res = harvest(html)
        except Exception as e:
            print(f"  {slug}: 실패 {str(e)[:40]}")
            res = {"jsonld": {}, "gosi": {}, "meta": {}, "options": {}, "error": str(e)[:60]}
        res["_brand"] = DISPLAY.get(slug, slug)
        res["_slug"] = slug
        res["_sample"] = {"style_code": sample.get("style_code"), "name": sample.get("name"),
                          "url": sample.get("url")}
        n = len(res["jsonld"]) + len(res["gosi"]) + len(res["meta"]) + len(res["options"])
        json.dump(res, open(os.path.join(OUT, f"attrs_{slug}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"  {DISPLAY.get(slug, slug):14} 속성 {n}개 "
              f"(JSON-LD {len(res['jsonld'])}·고시 {len(res['gosi'])}·메타 {len(res['meta'])}·옵션 {len(res['options'])})")


if __name__ == "__main__":
    main()
