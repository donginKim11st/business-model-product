#!/usr/bin/env python3
"""언블로커(Web Unlocker) API 프로바이더 무관 래퍼 — Akamai/Kasada JS챌린지 사이트용.
환경변수로 프로바이더+키만 넣으면 동작. 미설정이면 available()=False (호출측이 patchright 폴백).

지원(환경변수 UNBLOCKER_PROVIDER 로 선택):
  scraperapi   : UNBLOCKER_KEY  → http://api.scraperapi.com?api_key=K&render=true&url=
  zenrows      : UNBLOCKER_KEY  → https://api.zenrows.com/v1/?apikey=K&js_render=true&url=
  scrapingbee  : UNBLOCKER_KEY  → https://app.scrapingbee.com/api/v1/?api_key=K&render_js=true&url=
  brightdata   : UNBLOCKER_KEY(=Bearer 토큰), UNBLOCKER_ZONE → https://api.brightdata.com/request (POST)
  custom       : UNBLOCKER_ENDPOINT 에 {url} 치환 (+ 선택 UNBLOCKER_KEY 헤더)

사용:
  from unblocker import available, fetch
  if available(): html = fetch(target_url, country="kr", render=True)
"""
import json
import os
import urllib.parse
import urllib.request

PROVIDER = os.environ.get("UNBLOCKER_PROVIDER", "").lower()
KEY = os.environ.get("UNBLOCKER_KEY", "")
ZONE = os.environ.get("UNBLOCKER_ZONE", "")
ENDPOINT = os.environ.get("UNBLOCKER_ENDPOINT", "")


def available():
    if PROVIDER == "custom":
        return bool(ENDPOINT)
    if PROVIDER == "brightdata":
        return bool(KEY and ZONE)
    return bool(PROVIDER and KEY)


def fetch(url, country="kr", render=True, timeout=90):
    """언블로커 경유로 url의 렌더된 HTML 반환. 미설정/실패 시 예외."""
    if not available():
        raise RuntimeError("언블로커 미설정 (UNBLOCKER_PROVIDER/UNBLOCKER_KEY)")
    if PROVIDER == "scraperapi":
        q = urllib.parse.urlencode({"api_key": KEY, "url": url, "render": "true",
                                    "country_code": country})
        return _get(f"http://api.scraperapi.com/?{q}", timeout)
    if PROVIDER == "zenrows":
        q = urllib.parse.urlencode({"apikey": KEY, "url": url, "js_render": "true",
                                    "proxy_country": country})
        return _get(f"https://api.zenrows.com/v1/?{q}", timeout)
    if PROVIDER == "scrapingbee":
        q = urllib.parse.urlencode({"api_key": KEY, "url": url, "render_js": "true",
                                    "country_code": country})
        return _get(f"https://app.scrapingbee.com/api/v1/?{q}", timeout)
    if PROVIDER == "brightdata":
        body = json.dumps({"zone": ZONE, "url": url, "format": "raw",
                           "country": country}).encode()
        req = urllib.request.Request("https://api.brightdata.com/request", data=body,
                                     headers={"Authorization": f"Bearer {KEY}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    if PROVIDER == "custom":
        target = ENDPOINT.replace("{url}", urllib.parse.quote(url, safe=""))
        headers = {"Authorization": f"Bearer {KEY}"} if KEY else {}
        req = urllib.request.Request(target, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    raise RuntimeError(f"알 수 없는 UNBLOCKER_PROVIDER: {PROVIDER}")


def _get(u, timeout):
    with urllib.request.urlopen(u, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")
