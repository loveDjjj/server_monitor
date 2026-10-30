"""Pure ACP scheduling and configuration validation."""

from datetime import datetime

from .policy import latest_boundary

TERMINAL = {"SUCCEEDED", "SUSPENDED", "STOPPED", "FAILED", "DELETED"}
ACTIVE = {
    "RUNNING",
    "WAITING",
    "INIT",
    "QUEUEING",
    "CREATING",
    "STARTING",
    "RESTARTING",
    "RECOVERING",
}


def validate_config(c):
    if type(c["auto_restart"]) is not bool:
        raise ValueError("自动启动开关必须为布尔值")
    for key, low, high in (
        ("hours", 1, 30),
        ("poll_seconds", 15, 3600),
        ("ready_timeout_minutes", 1, 180),
    ):
        if type(c[key]) is not int or not low <= c[key] <= high:
            raise ValueError(f"{key} 必须为 {low}–{high} 的整数")
    for action in ("start", "stop"):
        value = c["schedule"][action]
        if type(value["enabled"]) is not bool:
            raise ValueError("定时开关必须为布尔值")
        datetime.strptime(value["time"], "%H:%M")
    if (
        all(c["schedule"][a]["enabled"] for a in ("start", "stop"))
        and c["schedule"]["start"]["time"] == c["schedule"]["stop"]["time"]
    ):
        raise ValueError("启动和停止时间不能相同")
    for field in ("image", "cluster", "worker_spec", "workspace", "user_id", "mount"):
        if not isinstance(c[field], str) or not c[field].strip():
            raise ValueError(field + " 不能为空")
    return c


def plan(c, state, job, now):
    boundary = latest_boundary(now, c["schedule"])
    if boundary and boundary[0] < state.get("effective_at", 0):
        boundary = None
    # A failed start must not prevent a later scheduled stop. A failed stop
    # remains paused, so an unconfirmed mutation is never retried each poll.
    if job and job["state"] in ACTIVE and state.get("failure_action") != "stop":
        if state.get("stop_requested") or (boundary and boundary[1] == "stop"):
            return "stop"
    if state.get("paused") or state.get("failure") or state.get("pending"):
        return None
    if boundary and boundary[1] == "stop":
        return None
    if job and job["state"] not in TERMINAL:
        return None
    if job and job["state"] == "FAILED" and state.get("acknowledged_failed_uid") != job.get("uid"):
        return None
    if c["auto_restart"]:
        return "start"
    if boundary and boundary[1] == "start" and boundary[0] > state.get("seen_boundary", 0):
        return "start"
    return None
