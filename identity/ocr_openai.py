#!/usr/bin/env python3
"""gpt-4o-mini 비전 OCR로 고시(소재·제조국·제조년월) 전수 추출 (재개 가능).
- 이미지형 브랜드(mizuno/montbell/outdoorproducts/westwood/proworldcup): 가장 큰 상세 통이미지
  → 폭축소+세로조각 → gpt-4o-mini 비전 → JSON.
- crocs: 고시가 이미지 아닌 렌더 텍스트(아코디언 <li>겉감/안감/수입자:...</li>) → HTML 파싱.
키: 환경변수 OPENAI_API_KEY. 출력: outputs/gosi_<slug>.csv (style_code,origin,material,mfg_date,source_image)
사용: python3 ocr_openai.py <slug> [N]
"""
import base64
import csv
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
KEY = os.environ["OPENAI_API_KEY"]
from PIL import Image
import ocr_gosi as og  # detail_images, http, download, _base, THUMB

PROMPT = ("이 의류/잡화 상품 상세페이지 이미지에서 한국 '상품정보제공고시'를 찾아 JSON으로만 답하라. "
          "키: material(소재/혼용률, 예 '겉감 나일론100% 안감 폴리에스터100%'), "
          "origin(제조국, 예 '중국'/'베트남'/'대한민국'), mfg_date(제조연월, 예 '2025년 11월' 또는 '2025.08'). "
          "이미지에 없는 항목은 빈 문자열. 마케팅 문구 말고 고시 표/스펙의 값만. 코드블록 없이 순수 JSON.")


def b64_parts(path, max_w=1100, max_h=2600, max_parts=6):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w > max_w:
        im = im.resize((max_w, int(h * max_w / w)))
        w, h = im.size
    crops = [im] if h <= max_h else \
        [im.crop((0, k, w, min(k + max_h, h))) for k in range(0, h, max_h)][:max_parts]
    out = []
    for c in crops:
        p = path + ".part.jpg"
        c.save(p, quality=80)
        out.append(base64.b64encode(open(p, "rb").read()).decode())
        os.remove(p)
    return out


def gpt(b64s):
    content = [{"type": "text", "text": PROMPT}]
    for b in b64s[:6]:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "high"}})
    body = json.dumps({"model": "gpt-4o-mini", "temperature": 0, "max_tokens": 300,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.load(r)
    txt = re.sub(r"^```(?:json)?|```$", "", out["choices"][0]["message"]["content"].strip(), flags=re.M).strip()
    try:
        return json.loads(txt)
    except Exception:
        return {}


_IMG = re.compile(r'(?:ec-data-src|data-src|src)=["\']([^"\']+\.(?:jpg|jpeg|png))["\']', re.I)
_SPEC = re.compile(r'-D\d?\.|_D\d?\.|-sp\.|detail\d*\.|_spec|/spec|상세|info_|_\d+\.jpg$', re.I)
_SPECHOST = re.compile(r'esmplus|diskn|kgspirit|kittyshoes|linkfile', re.I)
_BADIMG = re.compile(r'icon|quick|kakao|banner|intro|/skin/|editor|logo|btn_|membership|/small/|thumb|tiny', re.I)


def biggest_image(url, slug="", style_code=""):
    """PDP에서 고시 '스펙 통이미지'를 스코어링해 선택 → 다운로드(임시경로 반환).
    점수: 스타일코드 포함(+100) · -D/-sp/_N/detail 패턴(+60) · 스펙CDN(+40) · 세로길이(+area).
    호스트/위치가 브랜드마다 달라도(미즈노 -D, 프로월드컵 esmplus, 아웃도어 diskn _5) 일괄 처리."""
    html = og.http(url)
    sc6 = (style_code or "")[:6].lower()
    cands = []
    for u in dict.fromkeys(_IMG.findall(html)):
        if _BADIMG.search(u):
            continue
        full = u if u.startswith("http") else (("https:" + u) if u.startswith("//") else og._base(url) + u)
        score = 0
        lu = u.lower()
        if sc6 and sc6 in lu:
            score += 100
        if _SPEC.search(u):
            score += 60
        if _SPECHOST.search(u):
            score += 40
        cands.append((score, full))
    # 점수 높은 후보 우선, 동점이면 뒤쪽(상세 끝) 우선
    cands.sort(key=lambda x: -x[0])
    tried = 0
    best_any, best_any_area, best_any_path = None, 0, None
    for score, full in cands:
        if tried >= 12:
            break
        tmp = os.path.join(OUT, f"_oa_{abs(hash(full)) % 9999}.bin")
        try:
            og.download(full, tmp, url)
            w, h = Image.open(tmp).size
            # 스펙 후보(점수≥60)는 크기만 되면 즉시 채택
            if score >= 60 and w >= 500 and h >= 600:
                return full, tmp
            # 폴백용: 가장 큰 통이미지 보관
            if h > 1200 and w * h > best_any_area:
                if best_any_path and os.path.exists(best_any_path):
                    os.remove(best_any_path)
                best_any_area, best_any, best_any_path = w * h, full, tmp
            else:
                os.remove(tmp)
            tried += 1
        except Exception:
            pass
    return best_any, best_any_path


# crocs: 렌더 텍스트 <li>겉감/안감/소재 : ...</li> 파싱
_CROCS_MAT = re.compile(r'(겉감|안감|소재|갑피)[^<:：]{0,6}[:：]\s*([^<\n]{2,60})')
_CROCS_ORIGIN = re.compile(r'(?:제조국|원산지)\s*[:：]\s*([가-힣A-Za-z]{2,12})')


def crocs_text(url):
    html = og.http(url)
    mats = [f"{a} {b.strip()}" for a, b in _CROCS_MAT.findall(html)][:4]
    o = _CROCS_ORIGIN.search(html)
    return {"material": " / ".join(dict.fromkeys(mats)), "origin": o.group(1) if o else "", "mfg_date": ""}


def run(slug, limit=None):
    src = os.path.join(OUT, f"extract_brand_{slug}.csv")
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    if limit:
        rows = rows[:limit]
    outp = os.path.join(OUT, f"gosi_{slug}.csv")
    done = {}
    if os.path.exists(outp):
        for r in csv.DictReader(open(outp, encoding="utf-8-sig")):
            done[r["style_code"]] = r
    results = dict(done)
    n_new = 0
    for i, r in enumerate(rows):
        sc, url = r.get("style_code", ""), r.get("url", "")
        if not url:
            continue
        if sc in done and (done[sc].get("material") or done[sc].get("origin")):
            continue
        try:
            if slug == "crocs":
                res = crocs_text(url)
                src_img = "(rendered text)"
            else:
                img, path = biggest_image(url, slug, sc)
                if not path:
                    results[sc] = {"style_code": sc, "origin": "", "material": "", "mfg_date": "", "source_image": ""}
                    continue
                res = gpt(b64_parts(path))
                os.remove(path)
                src_img = img
            results[sc] = {"style_code": sc, "origin": res.get("origin", ""),
                           "material": res.get("material", ""), "mfg_date": res.get("mfg_date", ""),
                           "source_image": src_img}
            if res.get("material") or res.get("origin"):
                n_new += 1
        except Exception as e:
            print(f"  {sc} 실패: {str(e)[:60]}", file=sys.stderr)
        if (i + 1) % 15 == 0:
            _save(outp, results)
            print(f"  …{i+1}/{len(rows)} (신규 {n_new})", file=sys.stderr)
    _save(outp, results)
    filled = sum(1 for v in results.values() if v.get("material") or v.get("origin"))
    print(f"{slug}: {len(results)}행 중 고시채움 {filled} → {outp}")


def _save(path, results):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["style_code", "origin", "material", "mfg_date", "source_image"])
        w.writeheader()
        w.writerows(results.values())


if __name__ == "__main__":
    slug = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    run(slug, limit)
