#!/usr/bin/env python3
"""카탈로그명 추출 LLM 게이트(옵션·잔여 한정). 기본 OFF.

규칙이 저신뢰(needs_llm=1)로 남긴 행/그룹만 gpt-4o-mini 로 보정. 재개 캐시 + 비용상한.
네트워크는 _call_openai 에만; 캐시 히트 시 호출 없음(테스트는 캐시 선주입으로 우회).
"""
import os
import re
import sys
import json
import hashlib
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalog_decompose as cd
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
URL = "https://api.openai.com/v1/chat/completions"
CACHE_PATH = os.path.join(HERE, "outputs", "_catalog_llm_cache.json")
_WS = re.compile(r"\s+")


def _cache_load():
    if os.path.exists(CACHE_PATH):
        try:
            return json.load(open(CACHE_PATH, encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def _cache_save(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)


def _key1(row):
    return "d1:" + hashlib.md5((row.get("name", "")).encode("utf-8")).hexdigest()


def _call_openai(prompt, api_key):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 200,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("authorization", "Bearer %s" % api_key)
    with urllib.request.urlopen(req, timeout=40) as resp:
        payload = json.loads(resp.read())
    return payload["choices"][0]["message"]["content"].strip()


def _decompose_prompt(row):
    return (
        "다음 스포츠/아웃도어 상품의 원본명에서 '핵심 상품명(product_name)'과 "
        "'상품유형(product_type)', '성별(gender: M/W/U/K/빈칸)'을 뽑으세요.\n"
        "규칙: 브랜드명·성별·색상·유형·한/영 중복은 상품명에서 제외. 핵심 모델명만 남김.\n"
        "브랜드: %s\n원본명: %s\n"
        '오직 JSON만: {"product_name": "...", "product_type": "...", "gender": "M|W|U|K|"}'
        % (row.get("brand_norm", ""), row.get("name", ""))
    )


def apply_stage1(rows, limit=0, api_key=None, cache=None):
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY")
    own_cache = cache is None
    if own_cache:
        cache = _cache_load()
    cands = [r for r in rows if r.get("needs_llm") == "1"]
    if not cands:
        return 0
    budget = limit if limit else len(cands)
    n_done = 0
    for r in cands:
        if n_done >= budget:
            break
        k = _key1(r)
        if k not in cache:
            if not api_key:
                continue
            try:
                txt = _call_openai(_decompose_prompt(r), api_key)
                cache[k] = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            except Exception as e:  # noqa: BLE001
                print("  [LLM] 호출 실패(규칙 유지): %s" % e)
                continue
        parsed = cache[k]
        pn = _WS.sub(" ", (parsed.get("product_name") or "")).strip()
        if pn:
            r["product_name"] = pn
            r["product_type"] = parsed.get("product_type") or r.get("product_type", "")
            g = parsed.get("gender")
            if g in ("M", "W", "U", "K"):
                r["gender_code"] = g
                r["gender"] = cd.lex.GENDER_LABEL.get(g, r.get("gender", ""))
            attrs = cd.name_attrs(r.get("gender", ""), r.get("product_type", ""),
                                  cd.primary_color(r.get("color", "")), True)
            r["catalog_name"] = cd.compose_catalog_name(r.get("brand_norm", ""), pn, attrs)
            r["needs_llm"] = "0"
            n_done += 1
    left = len(cands) - n_done
    if left > 0:
        print("  [LLM] %d행 미보정(비용상한/캐시부재) — --llm-limit 상향 시 처리" % left)
    if own_cache:
        _cache_save(cache)
    return n_done


def apply_stage2(cats, drows, limit=0, api_key=None, cache=None):
    # v1: Stage1 보정으로 대표명 품질이 확보되므로 Stage2 게이트는 no-op 자리표시.
    # (그룹 과병합/과분할 의심 케이스가 확인되면 후속 확장.)
    return 0
