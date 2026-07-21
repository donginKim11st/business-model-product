"""HTTP 어댑터 — 순수 route() 디스패치 + 얇은 stdlib 서버 껍데기."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from insight_engine import sync, jobs, metrics
from insight_engine.types import ExtractTarget, EngineConfig

_DEFAULT_STORE_DIR = "insight_engine_jobs"


def _cfg(body: dict) -> EngineConfig:
    return EngineConfig(model=body.get("model", "gpt-4o-mini"))


def route(method: str, path: str, body: dict):
    if method == "POST" and path == "/extract":
        t = ExtractTarget(keyword=body["keyword"], uid=body.get("uid", ""))
        r = sync.extract_one(t, _cfg(body))
        return 200, {"result": r.__dict__}

    if method == "POST" and path == "/jobs":
        import os, tempfile
        os.makedirs(_DEFAULT_STORE_DIR, exist_ok=True)
        store = jobs.JobStore(tempfile.mktemp(dir=_DEFAULT_STORE_DIR, suffix=".jsonl"))
        targets = [ExtractTarget(keyword=t["keyword"], uid=t.get("uid", ""))
                   for t in body.get("targets", [])]
        job_id = jobs.submit(targets, _cfg(body), store)
        return 202, {"job_id": job_id}

    if method == "GET" and path.startswith("/jobs/"):
        jid = path[len("/jobs/"):]
        if jid not in jobs._JOBS:
            return 404, {"error": f"unknown job {jid}"}
        return 200, jobs.get(jid)

    if method == "GET" and path == "/metrics":
        snap = metrics.snapshot(list(jobs._JOBS.values()))
        return 200, {"snapshot": snap, "alerts": metrics.alerts(snap)}

    return 404, {"error": "not found"}


class _Handler(BaseHTTPRequestHandler):
    def _dispatch(self, method):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        code, payload = route(method, self.path.rstrip("/") or "/", body)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def serve(port: int = 8767):
    HTTPServer(("127.0.0.1", port), _Handler).serve_forever()
