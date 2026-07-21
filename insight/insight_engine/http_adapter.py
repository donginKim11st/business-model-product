"""HTTP 어댑터 — 순수 route() 디스패치 + 얇은 stdlib 서버 껍데기."""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from insight_engine import sync, jobs, metrics, router
from insight_engine.types import ExtractTarget, EngineConfig

_DEFAULT_STORE_DIR = "insight_engine_jobs"


def _cfg(body: dict) -> EngineConfig:
    return EngineConfig(model=body.get("model", "gpt-4o-mini"),
                        execution=body.get("execution", "sync"))


def _openai_client():
    """OpenAI 클라이언트 생성(테스트에서 monkeypatch 지점)."""
    from openai import OpenAI
    return OpenAI()


def route(method: str, path: str, body: dict):
    if method == "POST" and path == "/extract":
        t = ExtractTarget(keyword=body["keyword"], uid=body.get("uid", ""))
        r = sync.extract_one(t, _cfg(body))
        return 200, {"result": r.__dict__}

    if method == "POST" and path == "/jobs":
        cfg = _cfg(body)
        targets = [ExtractTarget(keyword=t["keyword"], uid=t.get("uid", ""))
                   for t in body.get("targets", [])]
        if cfg.execution == "batch":
            result = router.submit(targets, cfg, client=_openai_client())
        else:
            import os, tempfile
            os.makedirs(_DEFAULT_STORE_DIR, exist_ok=True)
            store = jobs.JobStore(tempfile.mktemp(dir=_DEFAULT_STORE_DIR, suffix=".jsonl"))
            result = router.submit(targets, cfg, sync_store=store)
        return 202, result

    if method == "POST" and path == "/batch/status":
        client = _openai_client()
        rows = [{"batch_id": i, "status": client.batches.retrieve(i).status}
                for i in body.get("batch_ids", [])]
        return 200, {"batches": rows}

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


def resolve_bind(host=None, port=None):
    """서버 바인딩 호스트·포트를 결정. 서버 배포용으로 env 우선(기본 0.0.0.0)."""
    host = host or os.environ.get("INSIGHT_HTTP_HOST", "0.0.0.0")
    port = int(port if port is not None else os.environ.get("INSIGHT_HTTP_PORT", "8770"))
    return host, port


def serve(host=None, port=None):
    """REST 서비스 기동. 외부 요청을 받으려면 0.0.0.0 바인딩(기본).
    로컬만 열려면 INSIGHT_HTTP_HOST=127.0.0.1."""
    host, port = resolve_bind(host, port)
    print(f"insight-engine REST on http://{host}:{port}  (/extract · /jobs · /metrics)", flush=True)
    HTTPServer((host, port), _Handler).serve_forever()


if __name__ == "__main__":
    serve()
