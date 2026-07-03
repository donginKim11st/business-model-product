"""godomall(고도몰) 계열 가구 브랜드 공통 크롤러.

extract_furniture_jakomo / flora / dongsuh 에서 import.
목록: /goods/goods_list.php?cateCd=XXX&page=N
상세: /goods/goods_view.php?goodsNo=N

SSL 인증서 오류가 있는 사이트가 있어 자체 fetch(unverified context) 사용.
그 외 유틸(HEADER, write_csv, parse_dimensions, parse_bed_size)은
extract_furniture_base 를 그대로 사용한다.
"""
import csv
import html as _html
import json
import os
import re
import signal
import socket
import ssl
import time
import urllib.parse
import urllib.request

# 절전 복귀 후 SSL read 가 urlopen timeout 을 비껴 무한 대기하는 사례(2026-07-04 야간 행 2회)
# — 소켓 전역 타임아웃으로 하드 가드.
socket.setdefaulttimeout(25)

from extract_furniture_base import (  # noqa: F401
    HEADER, OUT, UA, write_csv, parse_dimensions, parse_bed_size,
)

SLEEP = 0.2          # 요청 간 대기
# 몰별 감속 — dongsuh: ~300요청마다 타르핏(IP 스로틀) 실측(2026-07-04) → 1.2s 로 순항
SLEEP_OVERRIDE = {"dongsuh": 1.2}
RETRIES = 2          # 재시도 횟수
PAGE_CAP = 100       # 카테고리당 페이지 상한

_CTX = ssl._create_unverified_context()


def fetch(url, retries=RETRIES):
    """SSL 검증 없이 HTML fetch (godomall 사이트 인증서 이슈 대응)."""
    enc = urllib.parse.quote(url, safe=":/?=&%#+,@")
    req = urllib.request.Request(enc, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
                raw = r.read()
            return raw.decode(r.headers.get_content_charset("utf-8"), errors="replace")
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise last


def _strip_tags(s):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ── 목록 ─────────────────────────────────────────────────────────────────────

_GOODS_RE = re.compile(r"goods_view\.php\?(?:[^\"'>]*&(?:amp;)?)?goodsNo=(\d+)")


def list_goods_page(base_url, cate_cd, page):
    """카테고리 목록 한 페이지의 goodsNo 목록(순서보존, 중복제거)."""
    url = f"{base_url}/goods/goods_list.php?cateCd={cate_cd}&page={page}"
    html = fetch(url)
    seen, out = set(), []
    for m in _GOODS_RE.finditer(html):
        g = m.group(1)
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out


def crawl_categories(base_url, categories, limit=0):
    """categories: [(cateCd, 카테고리명)] → [(goodsNo, 카테고리명)].

    page=1부터 goodsNo가 안 나오거나 이전 페이지와 동일하면 중단. 전체 중복 제거.
    """
    seen = set()
    out = []
    for cate_cd, cate_name in categories:
        prev = None
        for page in range(1, PAGE_CAP + 1):
            try:
                goods = list_goods_page(base_url, cate_cd, page)
            except Exception as e:
                print(f"  [list] cateCd={cate_cd} page={page} 실패: {e}")
                break
            if not goods or goods == prev:
                break
            prev = goods
            new = 0
            for g in goods:
                if g not in seen:
                    seen.add(g)
                    out.append((g, cate_name))
                    new += 1
            print(f"  [list] cateCd={cate_cd}({cate_name}) page={page}: "
                  f"{len(goods)}개 (신규 {new}, 누적 {len(out)})")
            if limit and len(out) >= limit:
                return out[:limit]
            time.sleep(SLEEP)
        time.sleep(SLEEP)
    return out


# ── 상세 파싱 ─────────────────────────────────────────────────────────────────

def parse_title(html, suffix=None):
    m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    t = m.group(1) if m and m.group(1).strip() else ""
    if not t:
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        t = m.group(1) if m else ""
    t = _strip_tags(t)
    if suffix:
        t = re.sub(r"\s*\|\s*" + re.escape(suffix) + r"\s*$", "", t)
    # 앞의 [..%] 프로모 태그 제거
    t = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", t).strip()
    return t


_PRICE_RES = [
    re.compile(r'name="set_goods_price"\s+value="(\d+)"'),
    re.compile(r'set_goods_price"?[^0-9]{0,40}?value="(\d+)"'),
    re.compile(r'"goodsPrice"\s*[:=]\s*"?(\d{3,10})'),
]


def parse_price(html):
    for rx in _PRICE_RES:
        m = rx.search(html)
        if m:
            return m.group(1)
    return ""


_SELECT_RE = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.S)
_OPTION_RE = re.compile(r"<option[^>]*value=\"([^\"]*)\"[^>]*>([^<]*)", re.S)
_SIZE_HINT = re.compile(
    r"사이즈|크기|size|\d\s*인|\d+\s*(?:cm|mm)|[A-Z]{2,3}\d{5,}"
    r"|프레임|매트리스|매트|세트|^\d{2}\."
    r"|^(?:S|SS|D|Q|K|KK|싱글|슈퍼싱글|더블|퀸|킹)\b")


def _clean_opt(s):
    s = _strip_tags(s)
    s = re.sub(r"\s*\([\d,]+\)\s*$", "", s)                 # 뒤쪽 (재고/번호)
    s = re.sub(r"\s*\([+\-]?[\d,]+원?\)\s*$", "", s)         # (+10,000원)
    s = re.sub(r"\s*:\s*[+\-]?[\d,]+원?\s*$", "", s)         # : +10000원
    return s.strip()


def parse_colors(html):
    """optionNo_* / optionSnoInput <select>에서 색상 옵션 추출.

    색상 select 우선순위: label에 색상/컬러 포함 > optionNo_0 > optionSnoInput.
    사이즈성 select(라벨/옵션에 사이즈 힌트만 있는 것)는 후순위.
    """
    candidates = []  # (priority, [colors])
    for m in _SELECT_RE.finditer(html):
        attrs, body = m.group(1), m.group(2)
        name_m = re.search(r'name="([^"]*)"', attrs)
        sel_name = name_m.group(1) if name_m else ""
        if not (sel_name.startswith("optionNo_") or sel_name == "optionSnoInput"):
            continue
        label_m = re.search(r"option_select\(this,\s*'\d+',\s*'([^']*)'", m.group(0))
        label = label_m.group(1) if label_m else ""
        if re.search(r"배송|설치|출고|지정일", label):
            continue
        opts = []
        for val, txt in _OPTION_RE.findall(body):
            # value 속성은 sno^가격 등 내부데이터인 경우가 있어 표시 텍스트 우선
            o = _clean_opt(txt if txt.strip() else val)
            if not o or o in opts:
                continue
            if re.search(r"옵션|선택|품절|가격|배송|설치|출고|지정일|수도권|도서산간|^=+$", o):
                continue
            opts.append(o)
        if not opts:
            continue
        is_color_label = bool(re.search(r"색상|컬러|color", label, re.I))
        # 옵션 전부가 사이즈/구성(N인, 모델코드, cm 등)이면 색상 라벨이라도 제외
        # → 고시 테이블 색상으로 폴백 (사이트가 구성 select를 '색상'으로 라벨링하는 경우 있음)
        if all(_SIZE_HINT.search(o) for o in opts):
            continue
        if is_color_label:
            prio = 0
        elif sel_name == "optionNo_0":
            prio = 1
        elif sel_name.startswith("optionNo_"):
            prio = 2
        else:  # optionSnoInput
            prio = 3
        candidates.append((prio, opts))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    return "|".join(candidates[0][1])


_TH_TD_RE = re.compile(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", re.S)


def parse_gosi(html):
    """상품필수 정보(고시) 테이블 → dict(원문 th → td 텍스트)."""
    out = {}
    for th, td in _TH_TD_RE.findall(html):
        k, v = _strip_tags(th), _strip_tags(td)
        if k and v and k not in out and len(k) < 60:
            out[k] = v
    return out


def _gosi_pick(gosi, *keywords):
    for k, v in gosi.items():
        for kw in keywords:
            if kw in k:
                return v
    return ""


def fetch_detail(base_url, goods_no, title_suffix=None):
    """상세 페이지 파싱 → 부분 row dict."""
    url = f"{base_url}/goods/goods_view.php?goodsNo={goods_no}"
    html = fetch(url)
    if "구매가 불가한 상품" in html or "판매중지" in html[:2000]:
        return None  # 구매불가/삭제 상품
    name = parse_title(html, suffix=title_suffix)
    if not name or name == "결과":
        return None
    gosi = parse_gosi(html)

    color = parse_colors(html)
    if not color:
        color = _gosi_pick(gosi, "색상")

    size_txt = _gosi_pick(gosi, "크기", "치수", "사이즈")
    w, d, h = parse_dimensions(size_txt)
    if not (w or d or h):
        w, d, h = parse_dimensions(name)

    return {
        "model_no": goods_no,
        "name": name,
        "color": color,
        "price": parse_price(html),
        "material": _gosi_pick(gosi, "주요 소재", "주요소재", "구성재료", "소재", "재질"),
        "origin": _gosi_pick(gosi, "제조국", "원산지"),
        "safety_cert": _gosi_pick(gosi, "KC", "인증"),
        "width_cm": w, "depth_cm": d, "height_cm": h,
        "bed_size": parse_bed_size(name),
        "url": url,
    }


# ── 브랜드 실행 공통 ──────────────────────────────────────────────────────────

class _WatchdogTimeout(BaseException):
    """fetch 내부 재시도 루프의 `except Exception`에 삼켜지지 않도록 BaseException 상속.
    (TimeoutError 기반 1차 워치독이 재시도 except에 흡수돼 무력화된 실측 — 2026-07-04)"""


def _item_watchdog(signum, frame):
    raise _WatchdogTimeout("PDP 워치독 — 핸드셰이크/파싱 무한 대기 차단")


def _load_journal(path):
    """진행 저널 파싱 → (완료 goodsNo, 독약 goodsNo). 'T g'=시도, 'O g'=처리완료.
    T 2회+ 인데 O 없음 = 시도 중 2회 죽음(타르핏 행 등) → 독약으로 영구 스킵."""
    done, tries = set(), {}
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            tag, _, g = ln.strip().partition(" ")
            if tag == "O":
                done.add(g)
            elif tag == "T":
                tries[g] = tries.get(g, 0) + 1
    return done, {g for g, c in tries.items() if c >= 2 and g not in done}


def run_brand(slug, brand_ko, base_url, categories, limit=0, title_suffix=None):
    """목록→상세→CSV. 재개형(2026-07-04): 진행 저널·목록 캐시(24h)·증분 .part —
    타르핏 행(dongsuh #731: 핸드셰이크를 천천히 끄는 안티봇, 소켓 타임아웃·SIGALRM 모두
    C레벨에서 무력화 실측)은 프로세스 내부에서 못 끊으므로, 외부 감독자가 kill/재시작해도
    수집분이 보존되고 2회 시도 실패 상품은 독약 스킵되도록 한다."""
    jr_path = os.path.join(OUT, f"_journal_{slug}.txt")
    items_cache = os.path.join(OUT, f"_items_{slug}.json")
    part_path = os.path.join(OUT, f"extract_furniture_{slug}.csv.part")

    done, poison = _load_journal(jr_path)

    items = None
    if os.path.exists(items_cache) and time.time() - os.path.getmtime(items_cache) < 86400:
        try:
            items = [tuple(x) for x in json.load(open(items_cache, encoding="utf-8"))]
            print(f"[{slug}] 목록 캐시 재사용: {len(items)}개")
        except ValueError:
            items = None
    if items is None:
        print(f"[{slug}] 목록 수집 시작 (limit={limit or '전체'})")
        items = crawl_categories(base_url, categories, limit=limit)
        json.dump(items, open(items_cache, "w", encoding="utf-8"), ensure_ascii=False)
    if poison:
        print(f"[{slug}] 독약 상품 {len(poison)}개 영구 스킵: {sorted(poison)[:5]}…")
    print(f"[{slug}] 고유 상품 {len(items)}개 → 상세 수집 (기수집 {len(done)} 스킵)")

    if os.path.exists(part_path):
        pf = open(part_path, "a", encoding="utf-8", newline="")
        pw = csv.DictWriter(pf, fieldnames=HEADER, extrasaction="ignore")
    else:
        pf = open(part_path, "w", encoding="utf-8-sig", newline="")
        pw = csv.DictWriter(pf, fieldnames=HEADER, extrasaction="ignore")
        pw.writeheader()
    jf = open(jr_path, "a", encoding="utf-8")

    sleep_s = SLEEP_OVERRIDE.get(slug, SLEEP)
    signal.signal(signal.SIGALRM, _item_watchdog)
    for i, (goods_no, cate_name) in enumerate(items, 1):
        g = str(goods_no)
        if g in done or g in poison:
            continue
        jf.write(f"T {g}\n")
        jf.flush()
        try:
            signal.setitimer(signal.ITIMER_REAL, 90, 45)
            d = fetch_detail(base_url, goods_no, title_suffix=title_suffix)
        except _WatchdogTimeout as e:
            print(f"  [detail] goodsNo={goods_no} 워치독 스킵: {e}")
            jf.write(f"O {g}\n")
            jf.flush()
            continue
        except Exception as e:
            print(f"  [detail] goodsNo={goods_no} 실패: {e}")
            jf.write(f"O {g}\n")
            jf.flush()
            continue
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0, 0)
        if d is None:
            print(f"  [detail] goodsNo={goods_no} 구매불가/무효 → 제외")
            jf.write(f"O {g}\n")
            jf.flush()
            time.sleep(sleep_s)
            continue
        row = {k: "" for k in HEADER}
        row.update(d)
        row.update({
            "source": slug,
            "brand": brand_ko,
            "currency": "KRW",
            "category": cate_name,
            "assembly": "",
            "installation_service": "",
        })
        pw.writerow(row)
        pf.flush()
        jf.write(f"O {g}\n")
        jf.flush()
        if i % 10 == 0 or i == len(items):
            print(f"  [detail] {i}/{len(items)}")
        time.sleep(sleep_s)
    pf.close()
    jf.close()

    # 완주 — .part 확정(재시작 경계의 중복 goodsNo dedupe), 저널·캐시 정리
    rows, seen = [], set()
    for r in csv.DictReader(open(part_path, encoding="utf-8-sig")):
        k = r.get("model_no") or r.get("url")
        if k in seen:
            continue
        seen.add(k)
        rows.append(r)
    write_csv(rows, slug)
    for p in (jr_path, items_cache, part_path):
        try:
            os.remove(p)
        except OSError:
            pass
    return rows
