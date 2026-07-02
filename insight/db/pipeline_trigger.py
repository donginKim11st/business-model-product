#!/usr/bin/env python3
"""호스트 측 추출 파이프라인 트리거 서버.

n8n(Docker) 에서 host.docker.internal:8765 로 POST 하면 정형/비정형 추출 파이프라인을
백그라운드로 기동한다. 정형·비정형은 각각 독립 락·로그라서 동시에 돌려도 안전하고, 이미
실행 중이면 그 사실만 반환(스크립트가 직접 락을 관리하므로 중복 기동해도 무해).

엔드포인트(헤더 X-Token 일치 필요):
  POST /step/<stage>[?batch=N]  ★ n8n 배치 드라이버용 ★ 한 배치를 **동기 실행**하고,
                                끝나면 {이번 처리량 + 진행률(total/done/remaining)} JSON 반환.
                                동일 stage 가 이미 처리 중이면 즉시 busy+진행률만 반환.
  GET  /progress[/<stage>]      작업 안 돌리고 현재 진행률(Mongo 집계)만 반환.
  POST /run/<stage>             (레거시) 호스트 상시 루프를 백그라운드 기동(fire-and-forget).
  POST /run                     (레거시) 루프형 둘 다 기동.
  GET  /status[/<stage>]        루프 실행 여부 + 로그 끝부분.

배치 드라이버 모델: n8n Schedule 이 주기적으로 POST /step/<stage> 를 호출 → 응답의
progress.remaining 으로 IF 분기. 매 배치가 n8n Execution 으로 남아 진행률·로그가 보인다.
환경변수: TRIGGER_TOKEN(공유 토큰), TRIGGER_PORT(기본 8765), STEP_TIMEOUT(동기 배치 상한초, 기본 900).
"""
import os
import json
import subprocess
import http.server
import socketserver
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]          # .../insight
TOKEN = os.environ.get("TRIGGER_TOKEN", "")
PORT = int(os.environ.get("TRIGGER_PORT", "8765"))
STEP_TIMEOUT = int(os.environ.get("STEP_TIMEOUT", "900"))   # 동기 배치 실행 상한(초)
PROGRESS = ROOT / "db" / "pipeline_progress.py"
PY = os.environ.get("PY", "/usr/bin/python3")
EXTRACTED = str(ROOT.parent / "identity" / "outputs" / "all_brands.csv")  # 보정 큐용 identity 산출

# 보정 API/DB — 지연 로딩(서버 기동을 무거운 import 체인에 묶지 않음).
_CDB = None


def _cdb():
    global _CDB
    if _CDB is None:
        import identity_guidelines_db as g
        _CDB = g.get_db()
    return _CDB


def _calib():
    import identity_calib_api as c
    return c

# 스테이지별 스크립트 · 로그 · 락(run_*.sh 와 일치해야 함).
# in_run_all: 무인자 POST /run 이 함께 기동하는지(상시 루프=True, 1회 배치=False).
STAGES = {
    "structured": {
        "script": ROOT / "db" / "run_structured_loop.sh",
        "step": ROOT / "db" / "step_structured.sh",
        "log": ROOT / "db" / "structured_pipeline.log",
        "lock_pid": Path("/tmp/source_structured_loop.lock/pid"),
        "in_run_all": True,
    },
    "unstructured": {
        "script": ROOT / "db" / "run_unstructured_loop.sh",
        "step": ROOT / "db" / "step_unstructured.sh",
        "log": ROOT / "db" / "unstructured_pipeline.log",
        "lock_pid": Path("/tmp/source_unstructured_loop.lock/pid"),
        "in_run_all": True,
    },
    "youtube": {
        "script": ROOT / "db" / "run_youtube_backfill.sh",
        "step": ROOT / "db" / "step_youtube.sh",
        "log": ROOT / "db" / "youtube_backfill.log",
        "lock_pid": Path("/tmp/youtube_backfill.lock/pid"),
        "in_run_all": False,   # 일일 1회 배치 — /step/youtube 로만 명시 호출.
    },
    "rebuild": {
        "script": ROOT / "db" / "run_rebuild.sh",
        "step": ROOT / "db" / "step_rebuild.sh",
        "log": ROOT / "db" / "rebuild.log",
        "lock_pid": Path("/tmp/insights_rebuild.lock/pid"),
        "in_run_all": False,   # 일일 1회 리빌드 — /step/rebuild 로만. 진행률 없음.
    },
    "identity": {
        "script": ROOT / "db" / "run_identity_loop.sh",
        "step": ROOT / "db" / "step_identity.sh",
        "log": ROOT / "db" / "identity_pipeline.log",
        "lock_pid": Path("/tmp/source_identity_loop.lock/pid"),
        "in_run_all": False,   # 조인 단계 — /step/identity 로 호출(크롤은 identity extract 별도).
    },
    "catalog": {
        "step": ROOT.parent / "identity" / "step_catalog.sh",
        "log": ROOT.parent / "identity" / "catalog_pipeline.log",
        "lock_pid": Path("/tmp/_noop_catalog.lock/pid"),
        "in_run_all": False,   # 스케줄/버튼 — /step/catalog. 정형 all_brands.csv → 카탈로그명 추출(규칙·무료·멱등).
    },
    "catalog_geo": {
        "step": ROOT.parent / "identity" / "step_catalog_geo.sh",
        "log": ROOT.parent / "identity" / "catalog_pipeline.log",
        "lock_pid": Path("/tmp/_noop_catalog_geo.lock/pid"),
        "in_run_all": False,   # LLM canonical 배치 드레인 — /step/catalog_geo?batch=N (OPENAI_API_KEY 필요).
    },
    "furniture": {
        "step": ROOT.parent / "identity" / "step_furniture.sh",
        "log": ROOT.parent / "identity" / "catalog_pipeline.log",
        "lock_pid": Path("/tmp/_noop_furniture.lock/pid"),
        "in_run_all": False,   # 가구 카탈로그 재빌드(무료·멱등) — /step/furniture.
    },
    "furniture_geo": {
        "step": ROOT.parent / "identity" / "step_furniture_geo.sh",
        "log": ROOT.parent / "identity" / "catalog_pipeline.log",
        "lock_pid": Path("/tmp/_noop_furniture_geo.lock/pid"),
        "in_run_all": False,   # 가구 canonical LLM 드레인 — /step/furniture_geo?batch=N (키 필요).
    },
    "report": {
        "step": ROOT / "db" / "step_report.sh",
        "log": ROOT / "db" / "exports" / "export.log",
        "lock_pid": Path("/tmp/_noop_report.lock/pid"),
        "in_run_all": False,   # 버튼 호출 — /step/report. HTML 리포트 생성, 진행률 없음.
    },
    "excel": {
        "step": ROOT / "db" / "step_excel.sh",
        "log": ROOT / "db" / "exports" / "export.log",
        "lock_pid": Path("/tmp/_noop_excel.lock/pid"),
        "in_run_all": False,   # 버튼 호출 — /step/excel. xlsx 생성, 진행률 없음.
    },
    "dashboard": {
        "step": ROOT / "db" / "step_dashboard.sh",
        "log": ROOT / "db" / "exports" / "export.log",
        "lock_pid": Path("/tmp/_noop_dashboard.lock/pid"),
        "in_run_all": False,   # 버튼 호출 — /step/dashboard. 통합 리포트 HTML, 진행률 없음.
    },
}


def running_pid(stage):
    """해당 스테이지가 실제로 살아있으면 PID, 아니면 None."""
    try:
        pid = int(STAGES[stage]["lock_pid"].read_text().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def tail(path, n=15):
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except Exception:
        return []


def start_stage(stage):
    """이미 실행 중이면 그 사실을, 아니면 백그라운드로 기동한 결과를 반환."""
    if "script" not in STAGES[stage]:
        return {"stage": stage, "error": "step-only",
                "message": f"{stage} 는 POST /step/{stage} 로만 호출하세요(백그라운드 루프 없음)."}
    pid = running_pid(stage)
    if pid:
        return {
            "stage": stage, "started": False, "already_running": True, "pid": pid,
            "message": f"{stage} 파이프라인이 이미 실행 중입니다.",
            "log_tail": tail(STAGES[stage]["log"]),
        }
    # 백그라운드 기동 — start_new_session 으로 트리거 서버와 분리(서버가 죽어도 생존).
    with open(STAGES[stage]["log"], "a", encoding="utf-8") as lf:
        subprocess.Popen(
            ["/bin/zsh", str(STAGES[stage]["script"])],
            cwd=str(ROOT),
            stdout=lf, stderr=lf,
            start_new_session=True,
        )
    return {"stage": stage, "started": True, "message": f"{stage} 파이프라인을 시작했습니다."}


def status_stage(stage):
    pid = running_pid(stage)
    return {"stage": stage, "running": pid is not None, "pid": pid,
            "log_tail": tail(STAGES[stage]["log"])}


def run_step(stage, batch):
    """한 배치를 동기 실행하고, 스크립트 stdout 마지막 JSON 줄(진행률 포함)을 파싱해 반환."""
    cmd = ["/bin/zsh", str(STAGES[stage]["step"])]
    if batch:
        cmd.append(str(batch))
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"stage": stage, "error": "timeout",
                "message": f"배치가 {STEP_TIMEOUT}s 안에 안 끝남 — batch 를 줄이거나 STEP_TIMEOUT 상향.",
                "progress": progress_stage(stage)}
    # stdout 마지막의 유효 JSON 줄을 결과로(단계 로그는 파일로 가므로 보통 1줄).
    for line in reversed([l for l in p.stdout.splitlines() if l.strip()]):
        try:
            return json.loads(line)
        except Exception:
            continue
    return {"stage": stage, "error": "no-json", "rc": p.returncode,
            "stderr_tail": p.stderr.splitlines()[-8:], "stdout_tail": p.stdout.splitlines()[-8:]}


def progress_stage(stage=None):
    """작업을 돌리지 않고 Mongo 집계로 현재 진행률만 반환."""
    cmd = [PY, str(PROGRESS)]
    if stage:
        cmd += ["--stage", stage]
    try:
        out = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"error": f"progress 집계 실패: {e}"}


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        if not TOKEN:
            return True
        return self.headers.get("X-Token") == TOKEN

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/status":
            self._send(200, {"stages": [status_stage(s) for s in STAGES]})
        elif path.startswith("/status/") and path[len("/status/"):] in STAGES:
            self._send(200, status_stage(path[len("/status/"):]))
        elif path == "/progress":
            self._send(200, progress_stage())
        elif path.startswith("/progress/") and path[len("/progress/"):] in STAGES:
            self._send(200, progress_stage(path[len("/progress/"):]))
        elif path == "/calib/ui":
            self._send_html(200, _calib().PAGE)
        elif path == "/calib/status":
            self._send(200, _calib().status(_cdb()))
        elif path == "/calib/queue":
            q = parse_qs(urlparse(self.path).query)
            cat = (q.get("category") or [""])[0]
            n = int((q.get("n") or ["15"])[0])
            src = (q.get("source") or ["perturb"])[0]
            self._send(200, _calib().queue(_cdb(), cat, n, src, EXTRACTED))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth_ok():
            self._send(401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        if path == "/calib/label":
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                payload = {}
            self._send(200, _calib().save_label(_cdb(), payload))
        elif path == "/calib/recommend":
            q = parse_qs(parsed.query)
            cat = (q.get("category") or [""])[0]
            apply = (q.get("apply") or ["0"])[0] in ("1", "true")
            by = (q.get("by") or ["web"])[0]
            self._send(200, _calib().recommend(_cdb(), cat, apply, by))
        elif path.startswith("/step/") and path[len("/step/"):] in STAGES:
            stage = path[len("/step/"):]
            batch = (parse_qs(parsed.query).get("batch") or [None])[0]
            self._send(200, run_step(stage, batch))      # ★ 동기 — 배치 끝날 때까지 응답 대기
        elif path == "/run":  # 레거시: 루프형(정형+비정형)만 백그라운드 기동
            self._send(200, {"stages": [start_stage(s) for s in STAGES
                                        if STAGES[s]["in_run_all"]]})
        elif path.startswith("/run/") and path[len("/run/"):] in STAGES:
            self._send(200, start_stage(path[len("/run/"):]))
        else:
            self._send(404, {"error": "not found",
                             "hint": "POST /step/<stage>?batch=N | /run/<stage> | /run"})

    def log_message(self, *args):
        pass  # 액세스 로그 억제


if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as srv:
        print(f"pipeline trigger server listening on 0.0.0.0:{PORT} "
              f"(stages={list(STAGES)})", flush=True)
        srv.serve_forever()
