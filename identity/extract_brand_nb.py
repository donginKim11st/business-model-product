#!/usr/bin/env python3
"""
뉴발란스(nbkorea.com) 공식몰 전수(全數) 추출 → outputs/extract_brand_nb.csv

전략:
  · 신발(남/여/키즈, cIdx 1280/1320/1353)은 이미 전수 완료 → outputs/extract_nb.json 재사용(674).
  · 의류(1281/1321/1354)·용품(1282/1322/1355) 6개 상위 카테고리를 productList.action 으로
    빈 페이지까지 전 페이지 list 크롤 → 유니크 style_code 1회씩 PDP 상세 병합.
  · 상세는 official_extract.NBAdapter.detail() 재사용(컬러·사이즈·제조국·소재·제조년월 = 상품정보제공고시).
  · 중간 저장: 카테고리별 list 는 _nb_newcats_styles.json, 상세는 _nb_full_progress.jsonl 에 append(재개 가능).
  · style_code 기준 dedup. >5000 이면 5000 에서 컷(notes 기록).

출력 헤더(정확히): source,brand,style_code,name,color,price,currency,category,gender,sizes,origin,material,mfg_date,url  (utf-8-sig)
"""
import csv
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
sys.path.insert(0, HERE)
from official_extract import NBAdapter, http_get  # noqa: E402

OUT_HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
              "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]
CAP = 5000
GRP = "250110"
NEW_CATS = {  # key -> (cIdx, category_label, gender)
    "men_apparel": ("1281", "의류", "MEN"),
    "women_apparel": ("1321", "의류", "WOMEN"),
    "kids_apparel": ("1354", "의류", "KIDS"),
    "men_acc": ("1282", "용품", "MEN"),
    "women_acc": ("1322", "용품", "WOMEN"),
    "kids_acc": ("1355", "용품", "KIDS"),
}
SEED = os.path.join(OUT, "_nb_newcats_styles.json")   # style -> [col, key, cidx, name, price]
PROG = os.path.join(OUT, "_nb_full_progress.jsonl")   # detail rows (공통 19컬럼 스키마)
SHOES = os.path.join(OUT, "extract_nb.json")          # 신발 전수(674) 재사용
FINAL = os.path.join(OUT, "extract_brand_nb.csv")

_SEL = re.compile(r'<a[^>]*id="selDetail"[^>]*>')


def _att(tag, n):
    m = re.search(n + r'="([^"]*)"', tag)
    return m.group(1) if m else ""


def list_all():
    """6개 신규 카테고리 전 페이지 list 크롤 → 유니크 style 시드(_nb_newcats_styles.json)."""
    styles = {}
    if os.path.exists(SEED):
        styles = json.load(open(SEED, encoding="utf-8"))
        print(f"[list] 기존 시드 재사용 {len(styles)}", file=sys.stderr)
        return styles
    pages = {}
    for key, (cidx, _cat, _g) in NEW_CATS.items():
        seen = set()
        page = 0
        for page in range(1, 400):
            url = (f"https://www.nbkorea.com/product/productList.action"
                   f"?cateGrpCode={GRP}&cIdx={cidx}&pageNo={page}")
            html = http_get(url)
            tags = _SEL.findall(html)
            if not tags:
                break
            fresh = 0
            for t in tags:
                sc, cc = _att(t, "data-style"), _att(t, "data-color")
                if not sc or (sc + cc) in seen:
                    continue
                seen.add(sc + cc)
                fresh += 1
                if sc not in styles:
                    styles[sc] = [cc, key, cidx, _att(t, "data-display-name"),
                                  _att(t, "data-price").replace(",", "")]
            if fresh == 0:
                break
            time.sleep(0.05)
        pages[key] = page
        print(f"[list] {key:14} pages={page} cum_unique_styles={len(styles)}", file=sys.stderr)
    json.dump(styles, open(SEED, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(pages, open(os.path.join(OUT, "_nb_pages.json"), "w"), ensure_ascii=False)
    print(f"[list] 신규 카테고리 유니크 style {len(styles)} → {SEED}", file=sys.stderr)
    return styles


def detail_all(styles):
    """유니크 style 별 PDP 상세 1회. 진행분은 _nb_full_progress.jsonl 에 append(재개)."""
    ad = NBAdapter()
    done = set()
    if os.path.exists(PROG):
        for ln in open(PROG, encoding="utf-8"):
            try:
                done.add(json.loads(ln)["style_code"])
            except Exception:  # noqa: BLE001
                pass
    print(f"[detail] 이미 완료 {len(done)} / 대상 {len(styles)}", file=sys.stderr)
    f = open(PROG, "a", encoding="utf-8")
    items = list(styles.items())
    for i, (sc, (col, key, cidx, disp, price)) in enumerate(items):
        if sc in done:
            continue
        cat, gen = NEW_CATS[key][1], NEW_CATS[key][2]
        try:
            r = ad.detail(sc, col, price, disp, gen, category=cat)
        except Exception as e:  # noqa: BLE001
            print(f"  [detail] {sc} 실패: {e}", file=sys.stderr)
            continue
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()
        if (i + 1) % 50 == 0:
            print(f"  …{i + 1}/{len(items)}", file=sys.stderr)
        time.sleep(0.08)
    f.close()


def _to_out(r):
    """공통 19컬럼 레코드 → 출력 14컬럼(mfg_date 는 attributes JSON 에서)."""
    attrs = r.get("attributes") or "{}"
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except Exception:  # noqa: BLE001
            attrs = {}
    return {
        "source": "nb", "brand": r.get("brand", "newbalance"),
        "style_code": r.get("style_code", ""), "name": r.get("name", ""),
        "color": r.get("color", ""), "price": r.get("price", ""),
        "currency": r.get("currency", "KRW"), "category": r.get("category", ""),
        "gender": r.get("gender", ""), "sizes": r.get("sizes", ""),
        "origin": r.get("origin", ""), "material": r.get("material", ""),
        "mfg_date": attrs.get("mfg_date", ""), "url": r.get("url", ""),
    }


def assemble():
    """신발(extract_nb.json) + 신규(_nb_full_progress.jsonl) 병합 → dedup(style_code) → CSV."""
    rows, seen = [], set()
    # 신발 전수 먼저
    shoes = json.load(open(SHOES, encoding="utf-8")) if os.path.exists(SHOES) else []
    for r in shoes:
        o = _to_out(r)
        if o["style_code"] and o["style_code"] not in seen:
            seen.add(o["style_code"])
            rows.append(o)
    n_shoes = len(rows)
    # 신규 카테고리
    if os.path.exists(PROG):
        for ln in open(PROG, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            o = _to_out(r)
            if o["style_code"] and o["style_code"] not in seen:
                seen.add(o["style_code"])
                rows.append(o)
    capped = False
    if len(rows) > CAP:
        rows = rows[:CAP]
        capped = True
    with open(FINAL, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_HEADER)
        w.writeheader()
        w.writerows(rows)
    print(f"[assemble] 총 {len(rows)} (신발 {n_shoes} + 신규 {len(rows) - n_shoes}) "
          f"capped={capped} → {FINAL}", file=sys.stderr)
    return len(rows), n_shoes, capped


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("list", "all"):
        st = list_all()
    if cmd in ("detail", "all"):
        st = json.load(open(SEED, encoding="utf-8"))
        detail_all(st)
    if cmd in ("assemble", "all"):
        assemble()
