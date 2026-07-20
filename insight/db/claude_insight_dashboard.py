#!/usr/bin/env python3
"""claude 비정형 인사이트 실시간 대시보드 — HTTP 서버(:8767).

/      : 대시보드 HTML (JS가 10초마다 /data 폴링, 새로고침 없이 갱신)
/data  : 진행 수치 JSON (로컬 products.catalogs[].insight 집계 + 루프 상태 + 로그 요약)
REMOTE_URI 설정 시 원격 10xtf.aiCatalogUnstructuredAttribute 건수도 포함(60초 캐시).
멈추려면: pkill -f claude_insight_dashboard
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "db" / "claude_insight_pipeline.log"
PORT = int(os.environ.get("DASH_PORT", "8767"))
POLL_S = 10

DB_NAME = os.environ.get("INSIGHTS_DB", "insights_demo")
DB = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:47017/?directConnection=true"))[DB_NAME]

_remote_cache = {"t": 0.0, "n": None}


def loop_alive():
    try:
        out = subprocess.run(["pgrep", "-f", "run_claude_insight_loop"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return bool(out)
    except Exception:
        return False


def counts():
    base = [{"$unwind": "$catalogs"}, {"$match": {"catalogs.insight": {"$exists": True}}}]
    total = next(DB.products.aggregate([{"$unwind": "$catalogs"}, {"$count": "n"}]), {}).get("n", 0)
    done = next(DB.products.aggregate(base + [{"$count": "n"}]), {}).get("n", 0)
    now = datetime.now(timezone.utc)
    recent = {}
    for key, dt in (("h1", timedelta(hours=1)), ("h24", timedelta(hours=24))):
        cutoff = (now - dt).isoformat(timespec="seconds")
        recent[key] = next(DB.products.aggregate(base + [
            {"$match": {"catalogs.insight.fetched_at": {"$gte": cutoff}}},
            {"$count": "n"}]), {}).get("n", 0)
    return total, done, recent


def daily(days=14):
    base = [{"$unwind": "$catalogs"}, {"$match": {"catalogs.insight": {"$exists": True}}}]
    rows = list(DB.products.aggregate(base + [
        {"$project": {"d": {"$substrCP": ["$catalogs.insight.fetched_at", 0, 10]}}},
        {"$group": {"_id": "$d", "n": {"$sum": 1}}},
        {"$sort": {"_id": -1}}, {"$limit": days}]))
    return [{"d": r["_id"], "n": r["n"]} for r in rows]


def remote_count():
    uri = os.environ.get("REMOTE_URI")
    if not uri:
        return None
    if time.time() - _remote_cache["t"] < 60:
        return _remote_cache["n"]
    try:
        dst = MongoClient(uri, serverSelectionTimeoutMS=5000)["10xtf"]
        n = dst.aiCatalogUnstructuredAttribute.estimated_document_count()
    except Exception:
        n = -1
    _remote_cache.update(t=time.time(), n=n)
    return n


def log_tail(n=8):
    if not LOG.exists():
        return []
    lines = LOG.read_text(errors="ignore").splitlines()
    return [l for l in lines if re.search(r"\[claude야간", l)][-n:]


def today_rate():
    """오늘(UTC) 처리분의 평균 시간당 건수 — 첫 처리 시각~현재 경과시간으로 나눈다."""
    now = datetime.now(timezone.utc)
    day0 = now.strftime("%Y-%m-%d")
    base = [{"$unwind": "$catalogs"},
            {"$match": {"catalogs.insight.fetched_at": {"$gte": day0}}}]
    row = next(DB.products.aggregate(base + [
        {"$group": {"_id": None, "n": {"$sum": 1},
                    "first": {"$min": "$catalogs.insight.fetched_at"}}}]), None)
    if not row or not row["n"]:
        return 0, 0.0
    try:
        first = datetime.fromisoformat(row["first"])
    except ValueError:
        return row["n"], 0.0
    hours = max((now - first).total_seconds() / 3600, 1 / 60)
    return row["n"], round(row["n"] / hours, 1)


def data_json():
    total, done, recent = counts()
    n_today, rate_today = today_rate()
    return json.dumps({
        "db": DB_NAME, "total": total, "done": done, "remain": total - done,
        "pct": round(done / total * 100, 2) if total else 0,
        "h1": recent["h1"], "h24": recent["h24"],
        "today": n_today, "rate_today": rate_today,
        "rate24": round(recent["h24"] / 24, 1),
        "alive": loop_alive(), "remote": remote_count(), "daily": daily(),
        "log": log_tail(), "ts": datetime.now().strftime("%F %T"),
    }, ensure_ascii=False)


PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>claude 인사이트 대시보드</title>
<style>
body{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;margin:2rem;background:#fafafa;color:#222}
h1{font-size:1.3rem} .cards{display:flex;gap:1rem;flex-wrap:wrap;margin:1rem 0}
.card{background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:1rem 1.4rem;min-width:150px}
.k{font-size:.8rem;color:#777} .v{font-size:1.6rem;font-weight:700;margin-top:.2rem}
.bar{height:10px;background:#eee;border-radius:5px;overflow:hidden;margin:.6rem 0 1.4rem}
.bar i{display:block;height:100%;background:#5c6bc0;width:0;transition:width .6s}
.log{background:#1e1e1e;color:#c5e1a5;padding:1rem;border-radius:8px;font-size:.75rem;overflow-x:auto;line-height:1.6}
.meta{color:#999;font-size:.8rem}
</style></head><body>
<h1>claude 비정형 인사이트 진행 현황 <span id="st"></span></h1>
<div class="meta" id="meta">불러오는 중…</div>
<div class="bar"><i id="bar"></i></div>
<div class="cards">
<div class="card"><div class="k">전체 SKU</div><div class="v" id="total">-</div></div>
<div class="card"><div class="k">인사이트 완료</div><div class="v" id="done">-</div></div>
<div class="card"><div class="k">잔여</div><div class="v" id="remain">-</div></div>
<div class="card"><div class="k">최근 1시간</div><div class="v" id="h1">-</div></div>
<div class="card"><div class="k">최근 24시간</div><div class="v" id="h24">-</div></div>
<div class="card"><div class="k">평균 시간당 (오늘)</div><div class="v" id="rateToday">-</div></div>
<div class="card"><div class="k">평균 시간당 (24h)</div><div class="v" id="rate24">-</div></div>
<div class="card" id="remoteCard" style="display:none"><div class="k">원격 10xtf 적재</div><div class="v" id="remote">-</div></div>
</div>
<h2 style="font-size:1rem">일자별 처리 건수</h2>
<div class="card" style="min-width:0;max-width:560px"><table id="dailyT" style="width:100%;border-collapse:collapse;font-size:.85rem"></table></div>
<h2 style="font-size:1rem">루프 로그 (최근)</h2>
<div class="log" id="log">(로그 없음)</div>
<script>
const f = n => n.toLocaleString('ko-KR');
async function tick(){
  try{
    const d = await (await fetch('/data')).json();
    total.textContent = f(d.total); done.textContent = f(d.done) + ' (' + d.pct + '%)';
    remain.textContent = f(d.remain); h1.textContent = f(d.h1); h24.textContent = f(d.h24);
    rateToday.textContent = d.rate_today + '건/h'; rate24.textContent = d.rate24 + '건/h';
    bar.style.width = d.pct + '%';
    st.innerHTML = d.alive ? "<span style='color:#2e7d32'>● 가동 중</span>"
                           : "<span style='color:#c62828'>● 중지됨</span>";
    let eta = '';
    if(d.h1 > 0) eta = ' · 현재 속도 유지 시 잔여 약 ' + (d.remain/d.h1/24).toFixed(1) + '일';
    meta.textContent = 'DB=' + d.db + ' · 갱신 ' + d.ts + ' · POLL초 폴링' + eta;
    if(d.remote !== null){
      remoteCard.style.display = '';
      remote.textContent = d.remote < 0 ? '접속 실패' : f(d.remote);
    }
    const mx = Math.max(...d.daily.map(r=>r.n), 1);
    dailyT.innerHTML = '<tr><th style="text-align:left;padding:.2rem .5rem">일자</th><th style="text-align:right;padding:.2rem .5rem">건수</th><th></th></tr>' +
      d.daily.map(r => '<tr><td style="padding:.2rem .5rem">' + r.d + '</td>' +
        '<td style="text-align:right;padding:.2rem .5rem">' + f(r.n) + '</td>' +
        '<td style="width:55%"><div style="height:8px;border-radius:4px;background:#5c6bc0;width:' +
        (r.n/mx*100).toFixed(1) + '%"></div></td></tr>').join('');
    log.innerHTML = (d.log.length ? d.log : ['(로그 없음)'])
      .map(l => '<div>' + l.replace(/</g,'&lt;') + '</div>').join('');
  }catch(e){ meta.textContent = '갱신 실패: ' + e; }
}
tick(); setInterval(tick, POLL*1000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/data":
            body = data_json().encode()
            ctype = "application/json; charset=utf-8"
        else:
            body = PAGE.replace("POLL초", f"{POLL_S}초").replace("POLL*1000", f"{POLL_S}*1000").encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"대시보드: http://localhost:{PORT}  (폴링 {POLL_S}s)")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
