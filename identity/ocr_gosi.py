#!/usr/bin/env python3
"""고시 이미지 → Tesseract(로컬, 무료) OCR → 소재·제조국·제조년월 파싱.
이미지고시 9개 브랜드 전수 enrichment. 재개 가능(이미 처리한 style_code 스킵).

전처리: 통이미지가 매우 길면(>4500px) 하단부 위주 + 폭 1400 리사이즈(가독성·속도).
OCR: tesseract lang=kor+eng, psm 6.
파싱: 라벨(소재/제품소재/원단, 제조국/원산지, 제조연월/제조년월) 다음 값 정규식.

사용:
  python3 ocr_gosi.py <slug> [N]      # 한 브랜드 N개(미지정=전수)
  python3 ocr_gosi.py all             # 9개 전수
출력: outputs/gosi_<slug>.csv (style_code,origin,material,mfg_date,source_image)
"""
import csv
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
TESS = None  # 런타임 탐색
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

try:
    from curl_cffi import requests as _ccffi
    _CC = True
except ImportError:
    _CC = False
from PIL import Image

SLUGS = ["mizuno", "montbell", "outdoorproducts", "westwood", "proworldcup",
         "crocs", "jansport", "lecaf", "kolping"]

# 상세설명 이미지 후보 경로(브랜드 공통: 상품 고유 통이미지; 공용 배너/아이콘 제외)
DETAIL_IMG = re.compile(r'(?:ec-data-src|data-src|src)=["\']([^"\']+\.(?:jpg|jpeg|png))["\']', re.I)
KEEP = re.compile(r'/web/product|/web/upload/(?!.*(?:icon|logo|banner|btn))|linkfile|/data/|/userimg|/goods|/item|/editor', re.I)
SKIP = re.compile(r'icon|logo|banner|btn_|/skin/|common|footer|header|bottom-|top\.jpg|brandstory|modelinfo', re.I)
# 썸네일/소형 변형(-S, _s, -m, /small/, /thumb/) 은 고시가 없으니 후순위/제외
THUMB = re.compile(r'[-_](?:s|m|small|thumb|tiny|list)\.(?:jpg|jpeg|png)$|/(?:small|thumb|tn|list)/', re.I)


def tess_bin():
    global TESS
    if TESS:
        return TESS
    for p in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "tesseract"):
        try:
            subprocess.run([p, "--version"], capture_output=True, timeout=5)
            TESS = p
            return p
        except Exception:
            continue
    raise RuntimeError("tesseract 없음")


def http(url):
    url = urllib.parse.quote(url, safe=":/?=&%#+,")
    if _CC:
        return _ccffi.get(url, impersonate="chrome", timeout=30).text
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def download(url, path, referer):
    url = urllib.parse.quote(url, safe=":/?=&%#+,")
    # Referer 헤더는 ASCII여야 함(urllib은 latin-1) — 한글 슬러그 PDP면 origin만 사용
    ref = referer
    try:
        ref.encode("latin-1")
    except (UnicodeEncodeError, AttributeError):
        p = urllib.parse.urlparse(referer)
        ref = f"{p.scheme}://{p.netloc}/"
    if _CC:
        data = _ccffi.get(url, impersonate="chrome", timeout=40,
                          headers={"Referer": ref}).content
    else:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ref})
        data = urllib.request.urlopen(req, timeout=40).read()
    open(path, "wb").write(data)
    return len(data)


def detail_images(html, base):
    out, seen = [], set()
    # #prdDetail 영역 우선
    i = html.find("prdDetail")
    seg = html[i:i + 60000] if i > 0 else html
    for src in DETAIL_IMG.findall(seg):
        u = src.strip()
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = base + u
        if not u.startswith("http") or u in seen:
            continue
        if SKIP.search(u) or not KEEP.search(u):
            continue
        seen.add(u)
        out.append(u)
    # 썸네일은 맨 뒤로(고시는 풀사이즈 통이미지에 있음)
    out.sort(key=lambda u: 1 if THUMB.search(u) else 0)
    return out


def prep(path):
    """통이미지를 폭 1500으로 맞춘 뒤 세로 ~2400px 조각으로 전부 분할(고시 위치 무관 커버)."""
    try:
        im = Image.open(path).convert("L")
    except Exception:
        return [path]
    w, h = im.size
    # 폭 정규화(너무 좁으면 확대해 글자 키움 = OCR 가독성↑)
    target_w = 1500
    if w != target_w:
        im = im.resize((target_w, max(1, int(h * target_w / w))))
        w, h = im.size
    crops = []
    if h <= 2600:
        p = path + ".p0.png"
        im.save(p)
        return [p]
    step = 2200
    for idx, k in enumerate(range(0, h, step)):
        c = im.crop((0, k, w, min(k + step + 250, h)))  # 250 겹침으로 경계 글자 보존
        p = path + f".p{idx}.png"
        c.save(p)
        crops.append(p)
        if idx >= 11:  # 안전 상한(초장신 이미지)
            break
    return crops


def ocr(path):
    t = tess_bin()
    r = subprocess.run([t, path, "stdout", "-l", "kor+eng", "--psm", "6"],
                       capture_output=True, timeout=60)
    return r.stdout.decode("utf-8", "ignore")


_ORIGIN = re.compile(r'(?:제조국|원산지)\s*[:：]?\s*([가-힣A-Za-z]{2,12})')
_MAT = re.compile(r'(?:제품\s*)?소재\s*[:：]?\s*([^\n]{2,60})')
_MFG = re.compile(r'제조\s*[년연]\s*월\s*[:：]?\s*([0-9]{4}[.\- 년/]*[0-9]{0,2}[월]?)')
_CO = re.compile(r'(중국|베트남|대한민국|한국|국내|인도네시아|인도|캄보디아|방글라데시|미얀마|일본|이탈리아|타이|태국)')


def _clean(s):
    # Tesseract 흔한 오인 보정: 100% 가 10096/10090/1009% 등으로 읽힘
    s = re.sub(r'(\d{2,3})\s*0?9[06]\b', r'\1%', s)        # 10096→100%, 10090→100%
    s = re.sub(r'(\d{2,3})\s*[%96]{1,3}(?=\s|$|,|/|·)', lambda m: re.sub(r'[96]+$', '%', m.group(0)) if m.group(0).endswith(('96','9','6')) else m.group(0), s)
    s = s.replace("10096", "100%").replace("10090", "100%")
    return re.sub(r'\s+', " ", s).strip(" :·-,")


def parse(text):
    origin = ""
    m = _ORIGIN.search(text)
    if m:
        origin = m.group(1).strip()
    if not origin:
        m = _CO.search(text)
        origin = m.group(1) if m else ""
    mat = ""
    m = _MAT.search(text)
    if m:
        cand = _clean(m.group(1))
        # 소재처럼 보이는지(섬유명 또는 % 포함) 검증 — 마케팅 단어 오인 방지
        if re.search(r'폴리|면|나일론|울|아크릴|레이온|스판|cotton|polyester|nylon|가죽|메쉬|%|혼용', cand, re.I):
            mat = cand
    mfg = ""
    m = _MFG.search(text)
    if m:
        mfg = m.group(1).strip()
    return origin, mat, mfg


def process_brand(slug, limit=None):
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
    for r in rows:
        sc = r.get("style_code", "")
        url = r.get("url", "")
        if not url:
            continue
        if sc in done and (done[sc].get("material") or done[sc].get("origin")):
            continue
        try:
            html = http(url)
            imgs = detail_images(html, _base(url))
            # 풀사이즈(비썸네일) 통이미지 우선 4장
            full = [u for u in imgs if not THUMB.search(u)] or imgs
            cand = full[:4]
            origin = mat = mfg = ""
            used = ""
            for img in cand:
                tmp = os.path.join(OUT, f"_ocr_{slug}.bin")
                try:
                    download(img, tmp, url)
                    for pp in prep(tmp):
                        txt = ocr(pp)
                        o, m, d = parse(txt)
                        origin = origin or o
                        mat = mat or m
                        mfg = mfg or d
                        try:
                            os.remove(pp)
                        except OSError:
                            pass
                    os.remove(tmp)
                except Exception:
                    pass
                if mat or origin:
                    used = img
                    break
            results[sc] = {"style_code": sc, "origin": origin, "material": mat,
                           "mfg_date": mfg, "source_image": used}
            if mat or origin:
                n_new += 1
        except Exception as e:
            print(f"  {sc} 실패: {str(e)[:50]}", file=sys.stderr)
        if (len(results)) % 20 == 0:
            _save(outp, results)
            print(f"  …{len(results)}/{len(rows)} (신규채움 {n_new})", file=sys.stderr)
    _save(outp, results)
    filled = sum(1 for v in results.values() if v.get("material") or v.get("origin"))
    print(f"{slug}: {len(results)}행 중 고시채움 {filled} → {outp}")
    return filled, len(results)


def _base(url):
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _save(path, results):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["style_code", "origin", "material", "mfg_date", "source_image"])
        w.writeheader()
        w.writerows(results.values())


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    if sys.argv[1] == "all":
        tot = 0
        for s in SLUGS:
            try:
                f, _ = process_brand(s)
                tot += f
            except Exception as e:
                print(f"{s} 브랜드 실패: {e}", file=sys.stderr)
        print(f"전체 고시채움 {tot}")
    else:
        slug = sys.argv[1]
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
        process_brand(slug, limit)


if __name__ == "__main__":
    main()
