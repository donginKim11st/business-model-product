#!/usr/bin/env python3
"""옵션 드롭다운 재수집 — raw_options 누락 상품의 PDP를 다시 긁어 select 전체 수집.

  python3 refetch_options.py dongsuh [N]
  python3 refetch_options.py all          # 누락 있는 전 몰

출력: outputs/options_furniture_<slug>.csv (model_no, options)
  → map_geo_furniture.py 가 raw_options 오버레이로 로드 (추출 CSV보다 우선).
재개 가능: 기존 출력의 model_no 스킵.
"""
import csv
import json
import os
import re
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/125.0 Safari/537.36"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# UI성 옵션 제외 (상품 옵션 아님) — SHIPPING TO: cafe24 해외배송 국가 셀렉터,
# ★: 리뷰 별점 select, 한글 1자: 설문 선지 조각 (S/M/L 등 영문 1자 사이즈는 유지)
_UI_RE = re.compile(
    r"^[-=\s]*$|선택|옵션|필수|배송|택배|LANGUAGE|한국어|ENGLISH|일본어|중국어"
    r"|^\s*\d+\s*$|로그인|회원|SHIPPING\s*TO|^[★☆\s]+$|^[가-힣]$", re.IGNORECASE)


def fetch(url):
    """charset 헤더 → utf-8 → euc-kr/cp949 순 엄격 디코드 (vittz 등 EUC-KR 몰 대응)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
        raw = r.read()
        charset = r.headers.get_content_charset()
    for enc in [charset, "utf-8", "euc-kr", "cp949"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def extract_all_options(body):
    """모든 <select>의 option 텍스트 수집 (UI성 제외, 순서 보존 dedupe)."""
    out, seen = [], set()
    for sel in re.findall(r"<select[^>]*>(.*?)</select>", body, re.DOTALL):
        for o in re.findall(r"<option[^>]*>(.*?)</option>", sel, re.DOTALL):
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", o)).strip()
            t = re.sub(r"^=+\s*|\s*=+$", "", t).strip()
            if not t or len(t) > 60 or _UI_RE.search(t) or "�" in t:
                continue  # U+FFFD = 디코드 실패 mojibake — CSV 오염 차단
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


_UI_SELECT_RE = re.compile(r"delivery|qty|quantity|review|board|country|sns", re.I)


def _strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def extract_option_groups(body):
    """select 군 구조 보존 수집 → [{"label": 군이름, "values": [...]}].

    라벨: 직전 300자 내 th/label/dt 텍스트 → 없으면 placeholder("사이즈 선택")에서 유도.
    UI성 select(배송/수량/리뷰)는 name 속성으로 제외. 평탄화("|" join) 대신 군별 유지 —
    다중 군(색상×사이즈)의 정확한 교차 조합 전개가 목적."""
    groups = []
    n_empty = 0   # 값이 placeholder뿐인 select — 캐스케이드(1차 선택 후 로딩) 신호
    for m in re.finditer(r"<select([^>]*)>(.*?)</select>", body, re.DOTALL | re.I):
        attrs, inner = m.group(1), m.group(2)
        if _UI_SELECT_RE.search(attrs):
            continue
        vals, seen = [], set()
        for o in re.findall(r"<option[^>]*>(.*?)</option>", inner, re.DOTALL):
            t = re.sub(r"^=+\s*|\s*=+$", "", _strip_tags(o)).strip()
            if not t or len(t) > 60 or _UI_RE.search(t) or "�" in t or t in seen:
                continue
            seen.add(t)
            vals.append(t)
        if not vals:
            if re.search(r"<option", inner, re.I):
                n_empty += 1   # option 은 있는데 전부 placeholder → 종속 select 의심
            continue
        pre = body[max(0, m.start() - 300):m.start()]
        lab = re.findall(r"<(?:th|label|dt)[^>]*>(.*?)</(?:th|label|dt)>", pre, re.DOTALL)
        label = _strip_tags(lab[-1]) if lab else ""
        if not label:
            ph = re.search(r"<option[^>]*>([^<]*선택[^<]*)</option>", inner)
            if ph:
                label = re.sub(r"\[?필수\]?|을|를|선택|해\s*주세요|하세요|[*:()\[\]-]", " ",
                               _strip_tags(ph.group(1))).strip()
        groups.append({"label": label[:20], "values": vals})
    # 중복 군 제거 (모바일+PC 마크업이 같은 select 를 두 번 렌더)
    uniq, seen_g = [], set()
    for g in groups:
        k = (g["label"], tuple(g["values"]))
        if k in seen_g:
            continue
        seen_g.add(k)
        uniq.append(g)
    if n_empty:
        uniq.append({"label": "_cascade", "values": [str(n_empty)]})  # 종속 select 수(탐지 마커)
    return uniq


def run_groups(slug, limit=0):
    """옵션군 구조 수집 — 대상: raw_options 가 있는(=드롭다운 보유) 상품. 재개 가능."""
    targets = []
    for l in open(os.path.join(OUT, "furniture_geo_mapped.jsonl"), encoding="utf-8"):
        r = json.loads(l)
        if r["source"]["mall"] != slug:
            continue
        # 전 상품 대상 — raw 형식(|/콤마/없음)으로 선별하면 구성 select 를 놓친다
        # (실측: raw='메이플,그레이,화이트'인 PDP에 10개 구성옵션 select 존재. 2026-07-03)
        mn = r["attributes"].get("model_no", "")
        if mn:
            targets.append((mn, r["source"]["url"]))
    out_path = os.path.join(OUT, f"options_groups_furniture_{slug}.csv")
    done = set()
    if os.path.exists(out_path):
        done = {r["model_no"] for r in csv.DictReader(open(out_path, encoding="utf-8-sig"))}
    todo = [(m, u) for m, u in targets if m not in done]
    if limit:
        todo = todo[:limit]
    print(f"[{slug}] 옵션군 수집 대상 {len(todo)}건 (완료 {len(done)} 스킵)")
    if not todo:
        return
    mode = "a" if done else "w"
    fout = open(out_path, mode, encoding="utf-8-sig", newline="")
    w = csv.DictWriter(fout, fieldnames=["model_no", "option_groups"])
    if mode == "w":
        w.writeheader()

    def one(m, u):
        try:
            return m, json.dumps(extract_option_groups(fetch(u)), ensure_ascii=False)
        except Exception:
            return m, None

    n_ok = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, m, u) for m, u in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            m, gj = fu.result()
            if gj is None:
                continue
            w.writerow({"model_no": m, "option_groups": gj})
            fout.flush()
            n_ok += 1
            if i % 100 == 0:
                print(f"  … {i}/{len(todo)}")
    fout.close()
    print(f"[{slug}] 옵션군 완료 — {n_ok}건 → {out_path}")


def run(slug, limit=0):
    # 대상: raw_options 빈 상품
    targets = []
    for l in open(os.path.join(OUT, "furniture_geo_mapped.jsonl"), encoding="utf-8"):
        r = json.loads(l)
        if r["source"]["mall"] != slug:
            continue
        if not (r.get("raw_options") or "").strip():
            targets.append((r["attributes"].get("model_no", ""), r["source"]["url"]))
    out_path = os.path.join(OUT, f"options_furniture_{slug}.csv")
    done = set()
    if os.path.exists(out_path):
        done = {r["model_no"] for r in csv.DictReader(open(out_path, encoding="utf-8-sig"))}
    todo = [(m, u) for m, u in targets if m not in done]
    if limit:
        todo = todo[:limit]
    print(f"[{slug}] 옵션 재수집 대상 {len(todo)}건 (완료 {len(done)} 스킵)")
    if not todo:
        return

    mode = "a" if done else "w"
    fout = open(out_path, mode, encoding="utf-8-sig", newline="")
    w = csv.DictWriter(fout, fieldnames=["model_no", "options"])
    if mode == "w":
        w.writeheader()

    def one(m, u):
        try:
            opts = extract_all_options(fetch(u))
            return m, "|".join(opts)
        except Exception:
            return m, None

    n_ok = n_opt = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, m, u) for m, u in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            m, opts = fu.result()
            if opts is None:
                continue
            w.writerow({"model_no": m, "options": opts})
            fout.flush()
            n_ok += 1
            if opts:
                n_opt += 1
            if i % 100 == 0:
                print(f"  … {i}/{len(todo)} (옵션발견 {n_opt})")
    fout.close()
    print(f"[{slug}] 완료 — {n_ok}건 수집, 옵션 존재 {n_opt}건 → {out_path}")


def _ps_next(base, gno, chosen):
    """godomall goods_ps.php option_select — 선택값 체인 → 다음 레벨 옵션값 리스트."""
    params = [("mode", "option_select"), ("optionKey", str(len(chosen) - 1)),
              ("goodsNo", gno), ("mileageFl", "y")] + [("optionVal[]", v) for v in chosen]
    req = urllib.request.Request(
        base + "/goods/goods_ps.php",
        data=urllib.parse.urlencode(params).encode(),
        headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                 "Referer": base + f"/goods/goods_view.php?goodsNo={gno}"})
    with urllib.request.urlopen(req, timeout=12, context=CTX) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return [v for v in (d.get("nextOption") or []) if v]


_CASCADE_BASE = {"dongsuh": "https://www.dongsuhfurniture.co.kr"}


def run_cascade(slug):
    """_cascade 마커 PDP 의 종속(2·3차) 옵션을 goods_ps.php 로 수집해 군 JSON 에 병합."""
    base = _CASCADE_BASE.get(slug)
    if not base:
        print(f"[{slug}] cascade 어댑터 없음 — 스킵")
        return
    path = os.path.join(OUT, f"options_groups_furniture_{slug}.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    todo = []
    for r in rows:
        gs = json.loads(r["option_groups"] or "[]")
        if any(g["label"] == "_cascade" for g in gs) and \
                not any(g["label"].startswith("종속") for g in gs):
            todo.append(r)
    print(f"[{slug}] cascade 대상 {len(todo)}건")

    def one(r):
        gs = json.loads(r["option_groups"] or "[]")
        depth = int(next(g["values"][0] for g in gs if g["label"] == "_cascade"))
        depth = max(1, depth // 2)   # PC+모바일 중복 → 실질 종속 레벨 수
        lvl0 = next((g["values"] for g in gs if g["label"] not in ("_cascade",)
                     and not _ADDON_HINT.search(g["label"])), [])[:10]
        try:
            l1 = list(dict.fromkeys(v for v0 in lvl0 for v in _ps_next(base, r["model_no"], [v0])))
            if l1:
                gs.append({"label": "종속1", "values": l1[:30]})
            if depth >= 2 and lvl0 and l1:
                l2 = list(dict.fromkeys(
                    v for v0 in lvl0[:5] for v in _ps_next(base, r["model_no"], [v0, l1[0]])))
                if l2:
                    gs.append({"label": "종속2", "values": l2[:30]})
        except Exception:
            return r, None
        return r, json.dumps(gs, ensure_ascii=False)

    n = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r, gj in ex.map(lambda x: one(x), todo):
            if gj:
                r["option_groups"] = gj
                n += 1
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model_no", "option_groups"])
        w.writeheader()
        w.writerows(rows)
    print(f"[{slug}] cascade 병합 {n}건 → {path}")


_ADDON_HINT = re.compile(r"추가|커버|사은|선반|방수|배송")

_ALL = ["dongsuh", "vittz", "dotoro", "jakomo", "prielle", "bflamp",
        "wooree", "flora", "mothershome"]

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    groups_mode = "--groups" in sys.argv
    slug = args[0]
    limit = int(args[1]) if len(args) > 1 else 0
    if "--cascade" in sys.argv:
        fn = lambda s, *_: run_cascade(s)
    else:
        fn = run_groups if groups_mode else run
    if slug == "all":
        for s in _ALL:
            fn(s)
    else:
        fn(slug, limit)
