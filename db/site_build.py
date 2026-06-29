#!/usr/bin/env python3
"""정적 사이트 빌드 — data/ 의 4개 리포트 HTML 에 공통 네비게이션을 주입해 site/ 로 묶는다.

생성기(exec_report/report_site/seller_dashboard/consumer_guide)는 각각 단일 HTML 을 만든다.
여기서는 그 결과물을 건드리지 않고(재생성에도 견고), <body> 직후에 self-contained 플로팅 nav 만 끼워
'한 사이트'처럼 서로 오갈 수 있게 한다. 데이터/통계는 일절 변형하지 않음.

  python3 db/site_build.py            # data/ → site/ (nav 주입 + README)
"""
import os
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (소스 data 파일, 대상 site 파일, nav 키, 메뉴 라벨)
PAGES = [
    ("data/package_explorer.html", "explorer.html",  "explorer", "📦 패키지 탐색기"),
    ("data/exec_report.html",      "index.html",     "exec",   "📈 경영진 리포트"),
    ("data/report.html",           "dashboard.html", "charts", "📊 차트 대시보드"),
    ("data/seller_dashboard.html", "seller.html",    "seller", "🏷️ 셀러 인텔리전스"),
    ("data/consumer_guide.html",   "guide.html",     "guide",  "🕯️ 정직 가이드"),
]


def nav_html(current):
    links = []
    for _src, dest, key, label in PAGES:
        cls = "navcur" if key == current else "navln"
        links.append(f'<a class="{cls}" href="./{dest}">{label}</a>')
    return (
        "<style>"
        "#sitenav{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:99999;"
        "display:flex;gap:3px;background:rgba(18,22,31,.93);backdrop-filter:blur(9px);"
        "border:1px solid rgba(255,255,255,.13);border-radius:999px;padding:5px;"
        "box-shadow:0 10px 34px rgba(0,0,0,.30);font-family:-apple-system,'Pretendard','Apple SD Gothic Neo',sans-serif}"
        "#sitenav a{text-decoration:none;font-size:12.5px;font-weight:700;padding:7px 13px;border-radius:999px;"
        "color:#cfd6e2;white-space:nowrap;transition:.12s;line-height:1}"
        "#sitenav a.navln:hover{background:rgba(255,255,255,.10);color:#fff}"
        "#sitenav a.navcur{background:#fff;color:#10233b;cursor:default}"
        "@media(max-width:640px){#sitenav a{font-size:11px;padding:6px 9px}}"
        "@media print{#sitenav{display:none}}"
        "</style>"
        f'<nav id="sitenav" aria-label="리포트 네비게이션">{"".join(links)}</nav>'
    )


def inject(htmlstr, current):
    nav = nav_html(current)
    # <body> (속성 유무 무관) 직후에 주입
    if "<body>" in htmlstr:
        return htmlstr.replace("<body>", "<body>" + nav, 1)
    # 혹시 속성 있는 body 면 첫 닫는 '>' 뒤에
    i = htmlstr.find("<body")
    if i >= 0:
        j = htmlstr.find(">", i)
        return htmlstr[:j + 1] + nav + htmlstr[j + 1:]
    return htmlstr  # body 없으면 그대로(이상 케이스)


README = """# 상품 인사이트 인텔리전스 — 리포트

비공개(검색 차단) 정적 사이트. 네이버 리뷰·쇼핑·유튜브를 직접 수집한 **실측 데이터**만 사용(합성·과장 없음).
하단 플로팅 네비게이션으로 5개 화면을 오갈 수 있습니다.

- `explorer.html` — **패키지 탐색기** (전체 패키지 검색·필터, 카드 펼치면 인사이트+소비자 가이드+셀러 결합)
- `index.html` — **경영진/투자자용 리포트** (차트 + 카탈로그 모달)
- `dashboard.html` — **차트 대시보드** (추출 현황·분포·가격 분석)
- `seller.html` — **셀러 인텔리전스** (몰별 가격 경쟁력 + 약점·갭 + 다중 제품 비교)
- `guide.html` — **소비자 정직 가이드** (광고·협찬 없이 왜 사야/말아야 + 주요 몰 최저가, 근거 📎)

robots `noindex` 적용 — 검색엔진 비노출. URL 공유로만 열람.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="site")
    args = ap.parse_args()
    site = os.path.join(ROOT, args.site)
    os.makedirs(site, exist_ok=True)
    built = []
    for src, dest, key, _label in PAGES:
        sp = os.path.join(ROOT, src)
        if not os.path.exists(sp):
            print(f"  ! 소스 없음(건너뜀): {src}")
            continue
        with open(sp, encoding="utf-8") as f:
            h = f.read()
        out = inject(h, key)
        with open(os.path.join(site, dest), "w", encoding="utf-8") as f:
            f.write(out)
        built.append(f"{dest}({len(out):,}B)")
    with open(os.path.join(site, "README.md"), "w", encoding="utf-8") as f:
        f.write(README)
    # robots.txt 보장(검색 차단)
    rp = os.path.join(site, "robots.txt")
    if not os.path.exists(rp):
        with open(rp, "w", encoding="utf-8") as f:
            f.write("User-agent: *\nDisallow: /\n")
    print("사이트 빌드 →", site, "·", " ".join(built))


if __name__ == "__main__":
    main()
