"""관측 — 잡 상태 집계 + 알림 임계치."""


def snapshot(job_states: list) -> dict:
    total = sum(s.get("total", 0) for s in job_states)
    done = sum(s.get("done", 0) for s in job_states)
    empty = sum(s.get("empty", 0) for s in job_states)
    errors = sum(s.get("errors", 0) for s in job_states)
    cost = sum(s.get("cost_usd", 0.0) for s in job_states)
    paused = sum(1 for s in job_states if s.get("quota_paused"))
    return {
        "total": total, "done": done, "empty": empty, "errors": errors,
        "cost_usd": cost, "quota_paused_jobs": paused,
        "error_rate": (errors / done) if done else 0.0,
    }


def alerts(snap: dict, *, error_rate_max: float = 0.2) -> list:
    msgs = []
    if snap.get("error_rate", 0.0) > error_rate_max:
        msgs.append(f"실패율 초과: {snap['error_rate']:.0%} > {error_rate_max:.0%}")
    if snap.get("quota_paused_jobs", 0) > 0:
        msgs.append(f"쿼터 정지 잡 {snap['quota_paused_jobs']}개")
    return msgs
