#!/usr/bin/env python3
"""title_geo canonical 모델명 배치 추출(LLM) — 증분·재개·캐시. 도메인 파라미터화.

유니크 (brand, name) 단위로 gpt-4o-mini 에 canonical 모델명을 물어 store JSON 에 누적.
스포츠(기본): catalog_decomposed.csv(brand_norm/product_name/product_type) → _catalog_canonical.json
가구:        catalogs_furniture.csv --brand-col brand --name-col product_name --type-col l2 \
             --store outputs/_catalog_canonical_furniture.json --stage-key furniture_geo

  OPENAI_API_KEY=.. python3 catalog_geo.py [--in CSV] [--batch 200] [--workers N]
                     [--store JSON] [--brand-col C] [--name-col C] [--type-col C]
                     [--stage-key K] [--redo-collisions]
"""
import os
import re
import csv
import sys
import json
import argparse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
URL = "https://api.openai.com/v1/chat/completions"
STORE = os.path.join(HERE, "outputs", "_catalog_canonical.json")
IN_DEFAULT = os.path.join(HERE, "outputs", "catalog_decomposed.csv")
_WS = re.compile(r"\s+")


def _key(brand, pn):
    return "%s|%s" % (brand, pn)


def store_load(path=STORE):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def store_save(d, path=STORE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def pending(in_path, store, brand_col="brand_norm", name_col="product_name",
            type_col="product_type"):
    """유니크 (brand, name) 중 store 미보유분 → (brand, name, type) 리스트."""
    seen, out = set(), []
    if not os.path.exists(in_path):
        return out
    for r in csv.DictReader(open(in_path, encoding="utf-8-sig")):
        pn = (r.get(name_col) or "").strip()
        if not pn:
            continue
        k = _key(r.get(brand_col, ""), pn)
        if k in seen:
            continue
        seen.add(k)
        if k not in store:
            out.append((r.get(brand_col, ""), pn, r.get(type_col, "")))
    return out


def _prompt(brand, pn, ptype):
    return (
        "상품명에서 'canonical 모델명'만 남기세요.\n"
        "제거: 브랜드·성별·색상·사이즈·소재·마케팅 수식어(벨크로·경량·그래픽·스트레치 등).\n"
        "반드시 유지(다른 상품과 구분되는 정체성): 콜라보/파트너명(X 언더커버, 잔망루피, 미키, "
        "스타워즈 등), 에디션/버전(프리미엄, 레트로, '07, 2.0, OG), 제품 라인·핏(로우/미드/하이, "
        "루즈핏/베이직핏, 슬립인스). 서로 다른 콜라보·에디션이 같은 이름이 되면 안 됩니다.\n"
        "브랜드: %s\n유형: %s\n상품명: %s\n"
        '오직 JSON만: {"canonical": "..."}' % (brand, ptype, pn)
    )


def _call(prompt, api_key):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 60,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("authorization", "Bearer %s" % api_key)
    with urllib.request.urlopen(req, timeout=40) as resp:
        payload = json.loads(resp.read())
    return payload["choices"][0]["message"]["content"].strip()


def _one(api_key, brand, pn, ptype):
    try:
        txt = _call(_prompt(brand, pn, ptype), api_key)
        can = _WS.sub(" ", json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
                      .get("canonical", "")).strip()
    except Exception:
        can = ""
    return _key(brand, pn), (can or pn)   # 실패/빈값 → 원문 폴백(드레인 보장)


def run_batch(in_path=IN_DEFAULT, batch=200, api_key=None, workers=4,
              store_path=STORE, brand_col="brand_norm", name_col="product_name",
              type_col="product_type", stage_key="catalog_geo"):
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    store = store_load(store_path)
    pend = pending(in_path, store, brand_col, name_col, type_col)
    total = len(store) + len(pend)

    def prog(rem):
        return {stage_key: {"total": total, "done": total - rem, "remaining": rem}}

    if not pend:
        return {"stage": stage_key, "processed": 0, "progress": prog(0)}
    if not api_key:
        return {"stage": stage_key, "error": "no OPENAI_API_KEY",
                "processed": 0, "progress": prog(len(pend))}
    todo = pend[:batch] if batch else pend   # batch=0 → 전량
    done = 0
    if workers and workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_one, api_key, b, p, t) for b, p, t in todo]
            for fut in as_completed(futs):
                k, v = fut.result()
                store[k] = v
                done += 1
                if done % 500 == 0:
                    store_save(store, store_path)   # 주기적 저장(장시간 드레인 안전)
    else:
        for b, p, t in todo:
            k, v = _one(api_key, b, p, t)
            store[k] = v
            done += 1
    store_save(store, store_path)
    return {"stage": stage_key, "processed": done, "model": MODEL,
            "progress": prog(len(pend) - done)}


def collision_keys(store):
    """같은 brand 에서 서로 다른 name 이 같은 canonical 로 병합된 키들(재처리 대상)."""
    groups = {}
    for k, v in store.items():
        brand = k.split("|", 1)[0]
        groups.setdefault((brand, v), []).append(k)
    out = []
    for (_b, _v), ks in groups.items():
        if len(ks) > 1:
            out.extend(ks)
    return out


def redo_collisions(in_path=IN_DEFAULT, workers=8, api_key=None, **kw):
    """충돌 키를 store 에서 지우고(새 프롬프트로) 재계산."""
    store_path = kw.get("store_path", STORE)
    store = store_load(store_path)
    ks = collision_keys(store)
    for k in ks:
        del store[k]
    store_save(store, store_path)
    print(json.dumps({"stage": kw.get("stage_key", "catalog_geo") + "_redo",
                      "invalidated": len(ks)}, ensure_ascii=False))
    return run_batch(in_path, batch=0, api_key=api_key, workers=workers, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=IN_DEFAULT)
    ap.add_argument("--batch", type=int, default=200, help="0=전량")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("CATALOG_GEO_WORKERS", "4")))
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--brand-col", default="brand_norm")
    ap.add_argument("--name-col", default="product_name")
    ap.add_argument("--type-col", default="product_type")
    ap.add_argument("--stage-key", default="catalog_geo")
    ap.add_argument("--redo-collisions", action="store_true",
                    help="canonical 충돌(다른 상품→같은 이름) 키를 새 프롬프트로 재계산")
    args = ap.parse_args()
    kw = dict(store_path=args.store, brand_col=args.brand_col, name_col=args.name_col,
              type_col=args.type_col, stage_key=args.stage_key)
    if args.redo_collisions:
        print(json.dumps(redo_collisions(args.in_path, args.workers, **kw), ensure_ascii=False))
    else:
        print(json.dumps(run_batch(args.in_path, args.batch, workers=args.workers, **kw),
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
