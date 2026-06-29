#!/usr/bin/env python3
"""gpt-4o-mini 비전으로 상세 통이미지에서 고시(소재·제조국·제조년월) 추출 테스트.
키는 환경변수 OPENAI_API_KEY. stdlib urllib만 사용(SDK 불필요)."""
import base64
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
KEY = os.environ["OPENAI_API_KEY"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
try:
    from curl_cffi import requests as _cc
    _CC = True
except ImportError:
    _CC = False
from PIL import Image
import ocr_gosi as og  # detail_images, http, download, _base, THUMB 재사용

PROMPT = ("이 의류/잡화 상품 상세페이지 이미지에서 한국 '상품정보제공고시' 정보를 찾아 JSON으로만 답하라. "
          "키: material(소재/혼용률, 예 '겉감 나일론100% 안감 폴리에스터100%'), "
          "origin(제조국, 예 '중국'/'베트남'/'대한민국'), mfg_date(제조연월, 예 '2025년 11월'). "
          "이미지에 없는 항목은 빈 문자열. 마케팅 문구 말고 고시 표/정보의 값만. 코드블록 없이 순수 JSON.")


def b64_image(path, max_w=1100, max_h=2600):
    """긴 통이미지는 폭 축소 + 세로 조각(여러 장)으로 — 비전 토큰/가독성 균형."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w > max_w:
        im = im.resize((max_w, int(h * max_w / w)))
        w, h = im.size
    parts = []
    if h <= max_h:
        crops = [im]
    else:
        crops = [im.crop((0, k, w, min(k + max_h, h))) for k in range(0, h, max_h)][:6]
    for c in crops:
        p = path + ".part.jpg"
        c.save(p, quality=80)
        parts.append(base64.b64encode(open(p, "rb").read()).decode())
        os.remove(p)
    return parts


def gpt_ocr(b64_list):
    content = [{"type": "text", "text": PROMPT}]
    for b in b64_list[:6]:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "high"}})
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 300, "temperature": 0,
    }).encode()
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {KEY}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        out = json.load(r)
    txt = out["choices"][0]["message"]["content"]
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    try:
        return json.loads(txt), out.get("usage", {})
    except Exception:
        return {"_raw": txt}, out.get("usage", {})


def test_brand(slug, n=2):
    rows = list(csv.DictReader(open(os.path.join(OUT, f"extract_brand_{slug}.csv"), encoding="utf-8-sig")))
    print(f"\n===== {slug} (gpt-4o-mini) =====")
    for r in rows[:n]:
        url = r["url"]
        sc = r["style_code"]
        try:
            html = og.http(url)
            imgs = [u for u in og.detail_images(html, og._base(url)) if not og.THUMB.search(u)]
            if not imgs:
                print(f"  {sc}: 상세이미지 없음")
                continue
            # 후보 여러 장 다운로드 → 가장 '큰(높은)' 이미지 = 고시 통이미지일 확률↑
            best, best_area, best_path = None, 0, None
            for k, u in enumerate(imgs[:8]):
                tmp = os.path.join(OUT, f"_oa_{slug}_{k}.bin")
                try:
                    og.download(u, tmp, url)
                    w, h = Image.open(tmp).size
                    if h > 1200 and w * h > best_area:  # 통이미지(세로 김) 우선
                        best_area, best, best_path = w * h, u, tmp
                    else:
                        os.remove(tmp)
                except Exception:
                    pass
            if not best_path:
                print(f"  {sc}: 통이미지 후보 없음(이미지 {len(imgs)}장)")
                continue
            parts = b64_image(best_path)
            os.remove(best_path)
            res, usage = gpt_ocr(parts)
            cost = (usage.get("prompt_tokens", 0) * 0.15 + usage.get("completion_tokens", 0) * 0.6) / 1e6
            print(f"  {sc}: {json.dumps(res, ensure_ascii=False)}  [${cost:.4f}, {len(parts)}조각]")
        except Exception as e:
            print(f"  {sc}: 실패 {str(e)[:60]}")


if __name__ == "__main__":
    for s in sys.argv[1:] or ["westwood", "jansport"]:
        test_brand(s, 2)
