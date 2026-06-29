#!/usr/bin/env python3
"""미즈노 cafe24 PDP 상세 통이미지에서 고시(소재/제조국/제조년월) 추출용
다운로드 + 하단 크롭. 비전 OCR은 별도(Read 도구)."""
import os, re, sys, urllib.request, urllib.parse
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# (index, style_code, url)
PRODUCTS = [
    (1, "J1GD268671", "https://kor.mizuno.com/product/detail.html?product_no=11953"),
    (2, "J1GD268672", "https://kor.mizuno.com/product/detail.html?product_no=11954"),
    (3, "V1GA254083", "https://kor.mizuno.com/product/detail.html?product_no=11963"),
    (4, "V1GA254039", "https://kor.mizuno.com/product/detail.html?product_no=11247"),
    (5, "V1GA254059", "https://kor.mizuno.com/product/detail.html?product_no=11246"),
    (6, "V1GC254085", "https://kor.mizuno.com/product/detail.html?product_no=11964"),
    (7, "D1GA245127", "https://kor.mizuno.com/product/detail.html?product_no=12035"),
    (8, "D1GA245128", "https://kor.mizuno.com/product/detail.html?product_no=12036"),
]

BASE = "https://kor.mizuno.com"


def get(url, timeout=30):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(u, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read()


def detail_imgs(html, style_code):
    """prdDetail .cont 영역의 상품 고유 통이미지 url들(문서순)."""
    i = html.rfind('id="prdDetail"')
    seg = html[i:i + 60000] if i > 0 else html
    # .cont 첫 블록만
    cands = re.findall(
        r'(?:ec-data-src|data-src|data-original|src)=["\']([^"\']+\.(?:jpg|jpeg|png))["\']',
        seg, re.I)
    out = []
    for s in cands:
        low = s.lower()
        if any(k in low for k in ("sizechart", "copyright", "echosting",
                                  "cafe24img", "/icon", "/upload/", "option_button",
                                  "/medium/", "/small/", "/big/")):
            continue
        if "/web/product/" in low or style_code.lower() in low:
            if s.startswith("//"):
                s = "https:" + s
            elif s.startswith("/"):
                s = BASE + s
            if s not in out:
                out.append(s)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    for idx, sc, url in PRODUCTS:
        print(f"\n=== [{idx}] {sc} {url}")
        try:
            html = get(url).decode("utf-8", "replace")
        except Exception as e:
            print("  HTML fail", e); continue
        imgs = detail_imgs(html, sc)
        print("  detail imgs:", imgs)
        if not imgs:
            continue
        # 모든 후보 다운로드 + 크기측정, 가장 긴 것 선택
        downloaded = []
        for j, iu in enumerate(imgs):
            raw_path = os.path.join(OUT, f"_raw_mizuno_{idx}_{j}.jpg")
            try:
                req = urllib.request.Request(
                    urllib.parse.quote(iu, safe=":/?=&%#+,"),
                    headers={"User-Agent": UA, "Referer": url})
                data = urllib.request.urlopen(req, timeout=40).read()
                with open(raw_path, "wb") as f:
                    f.write(data)
                im = Image.open(raw_path)
                w, h = im.size
                downloaded.append((raw_path, w, h, iu))
                print(f"    dl {iu.split('/')[-1]} {w}x{h}")
            except Exception as e:
                print("    img fail", iu, e)
        if not downloaded:
            continue
        # 고시는 통상 마지막(문서순) 또는 가장 긴 이미지 하단. 둘 다 크롭.
        # 선택: 가장 긴 이미지
        downloaded.sort(key=lambda t: t[2], reverse=True)
        raw_path, w, h, iu = downloaded[0]
        im = Image.open(raw_path)
        # 하단 1500px 크롭(네이티브 폭 유지, 가독성)
        top = max(0, h - 1500)
        crop = im.crop((0, top, w, h))
        crop_path = os.path.join(OUT, f"_tmp_mizuno_{idx}.jpg")
        crop.save(crop_path, quality=92)
        print(f"    CROP bottom -> {crop_path} {crop.size} (from {iu.split('/')[-1]})")


if __name__ == "__main__":
    main()
