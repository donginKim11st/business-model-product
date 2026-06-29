#!/usr/bin/env python3
# Server-side product sample extractor for 월드컵 official mall
# (worldcupshoes.co.kr, GodoMall5, hook=dom). Multi-brand 신발 도매몰.
import urllib.request, urllib.parse, re, csv, time, html as ihtml, sys

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
BASE = "https://www.worldcupshoes.co.kr"
OUT_CSV = "/Users/a1101417/Work/business-model/identity/outputs/extract_brand_worldcup.csv"

# (cateCd, gender-from-category) -- gender categories so we get diversity + a backup gender label
CATS = [("012001", "남성"), ("012002", "여성"), ("012003", "아동")]
TARGET = 120
MAX_PAGES = 5     # per category

HEADER = ["source", "brand", "style_code", "name", "color", "price", "currency",
          "category", "gender", "sizes", "origin", "material", "mfg_date", "url"]


def fetch(url, timeout=30):
    u = urllib.parse.quote(url, safe=":/?=&%#+,")
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.getcode(), r.read().decode("utf-8", "replace")


def clean(s):
    return ihtml.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or ""))).strip()


def dl_pairs(h):
    out = {}
    for dt, dd in re.findall(r"<dt>\s*(.*?)\s*</dt>\s*<dd[^>]*>(.*?)</dd>", h, re.S):
        k = ihtml.unescape(re.sub(r"<[^>]+>", "", dt)).strip()
        v = clean(dd)
        if k and k not in out:
            out[k] = v
    return out


def gosi_pairs(h):
    i = h.find("상품필수 정보")
    if i < 0:
        return {}, False
    seg = h[i:i + 4000]
    out = {}
    for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", seg, re.S):
        k = ihtml.unescape(re.sub(r"<[^>]+>", "", th)).strip()
        v = clean(td)
        if k and k not in out:
            out[k] = v
    return out, True


def collect_goods():
    """Return (ordered unique goodsNo list, est_total, gender_hint map)."""
    order, ghint, est = [], {}, 0
    seen = set()
    for code, gender in CATS:
        # est_total from this category's max page (record the largest seen)
        first = None
        for page in range(1, MAX_PAGES + 1):
            url = f"{BASE}/goods/goods_list.php?cateCd={code}&page={page}"
            try:
                st, h = fetch(url)
            except Exception as e:
                print(f"list err {code} p{page}: {e}", file=sys.stderr)
                break
            if st != 200:
                break
            if first is None:
                first = h
            ids = re.findall(r"goodsNo=([0-9]+)", h)
            new = [g for g in dict.fromkeys(ids) if g not in seen]
            for g in new:
                seen.add(g)
                order.append(g)
                ghint.setdefault(g, gender)
            if not new:
                break
            time.sleep(0.4)
        if first:
            pages = [int(x) for x in re.findall(r"page=([0-9]+)", first)]
            if pages:
                est = max(est, max(pages) * 15)
        if len(order) >= TARGET:
            break
    return order[:TARGET], est, ghint


def parse_detail(goodsNo, gender_hint, h):
    dl = dl_pairs(h)
    gz, gz_present = gosi_pairs(h)

    # name + color from og:title "name / color"; color authoritative from 고시
    ot = re.search(r'og:title"\s+content="([^"]*)"', h)
    title = ihtml.unescape(ot.group(1)).strip() if ot else ""
    name, color_title = title, ""
    if " / " in title:
        parts = [p.strip() for p in title.split(" / ")]
        name = parts[0]
        color_title = parts[-1] if len(parts) > 1 else ""
    color = gz.get("색상") or color_title

    brand = dl.get("브랜드") or "월드컵"
    style_code = dl.get("모델명") or ""
    origin = dl.get("원산지") or gz.get("제조국") or ""
    material = gz.get("제품 주소재") or gz.get("주소재") or ""

    # gender + category from 짧은설명 e.g. "여성 샌들"
    desc = dl.get("짧은설명") or ""
    gender = ""
    if re.search(r"남녀|공용|남여", desc):
        gender = "공용"
    elif "여성" in desc:
        gender = "여성"
    elif "남성" in desc:
        gender = "남성"
    elif "아동" in desc or "키즈" in desc:
        gender = "아동"
    if not gender:
        gender = gender_hint or ""
    # category = trailing noun of 짧은설명 (strip leading gender word)
    category = re.sub(r"^(남성|여성|아동|남녀공용|남여공용|공용|남녀|남여)\s*", "", desc).strip()

    # price: only 정가/소비자가(set_goods_fixedPrice) is exposed; 회원판매가 is login-gated
    fp = re.search(r'set_goods_fixedPrice"[^>]*value="([0-9.]+)"', h)
    price = ""
    if fp:
        try:
            price = str(int(float(fp.group(1))))
        except ValueError:
            price = ""

    # sizes from option data-option-value "...^|^{size}"
    sizes = []
    for v in re.findall(r'data-option-value="([^"]*)"', h):
        if "^|^" in v:
            sz = v.split("^|^")[-1].strip()
            if sz and sz not in sizes:
                sizes.append(sz)
    if not sizes:  # fallback: 고시 치수
        for sz in re.split(r"[,/]", gz.get("치수", "")):
            sz = sz.strip()
            if sz and sz not in sizes:
                sizes.append(sz)

    gosi_status = "text" if (material or origin) else ("image" if gz_present else "none")

    row = {
        "source": "worldcup", "brand": brand, "style_code": style_code,
        "name": name, "color": color, "price": price, "currency": "KRW",
        "category": category, "gender": gender, "sizes": "|".join(sizes),
        "origin": origin, "material": material, "mfg_date": "",
        "url": f"{BASE}/goods/goods_view.php?goodsNo={goodsNo}",
    }
    return row, gosi_status


def main():
    ids, est_total, ghint = collect_goods()
    print(f"collected {len(ids)} goodsNo, est_total~{est_total}", file=sys.stderr)
    rows, statuses = [], {"text": 0, "image": 0, "none": 0}
    for n, g in enumerate(ids, 1):
        url = f"{BASE}/goods/goods_view.php?goodsNo={g}"
        try:
            st, h = fetch(url)
        except Exception as e:
            print(f"detail err {g}: {e}", file=sys.stderr)
            continue
        if st != 200:
            print(f"detail {g} status {st}", file=sys.stderr)
            continue
        row, status = parse_detail(g, ghint.get(g, ""), h)
        rows.append(row)
        statuses[status] = statuses.get(status, 0) + 1
        if n % 20 == 0:
            print(f"  {n}/{len(ids)} ...", file=sys.stderr)
        time.sleep(0.35)

    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)

    # verification summary
    filled = {k: sum(1 for r in rows if r.get(k)) for k in HEADER}
    print("WROTE", len(rows), "rows ->", OUT_CSV, file=sys.stderr)
    print("EST_TOTAL", est_total, file=sys.stderr)
    print("GOSI", statuses, file=sys.stderr)
    print("FILLED", filled, file=sys.stderr)


if __name__ == "__main__":
    main()
