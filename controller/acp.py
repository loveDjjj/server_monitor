"""Durable ACP operations and monitoring, independent of CCI rollover."""

import copy
import json
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from .acp_client import ACPClient, normalize_job, parse_jobs, parse_workers
from .acp_policy import TERMINAL, plan, validate_config
from .config import read_yaml, write_yaml
from .policy import latest_boundary
from .storage import append, journal, read_json, save_json, stamp

ACTION_LABELS = {"start": "启动新任务", "stop": "停止任务", "resume": "恢复自动调度"}


class ACP:
    def __init__(self, service):
        self.service = service
        self.path = service.root / "config/acp.yaml"
        self.config = validate_config(read_yaml(self.path))
        self.state_path = service.root / "runtime/acp/state.json"
        self.log_path = service.root / "logs/acp/events.jsonl"
        self.state = read_json(self.state_path, {"managed": [], "seen_boundary": time.time()})
        self.lock = threading.RLock()
        self.jobs = []
        self.current_job = None
        self.history_page = 1
        self.history_more = False
        self.history_sampled = 0
        self.workers = {}
        self.log_cache = {}
        self.sampled_at = None
        self.error = ""
        self.operation = None
        self.next_poll = 0
        if self.state.get("operation"):
            old = self.state.pop("operation")
            self.state["failure"] = "上次操作中断，请核对平台状态后恢复"
            self.state["failure_action"] = old["action"]
            self.event(self.state["failure"], status="interrupted", action=old["action"])
            self.persist()

    def persist(self):
        save_json(self.state_path, self.state)

    def event(self, message, **extra):
        with self.lock:
            append(self.log_path, {"timestamp": stamp(), "message": message, **extra})

    def client(self):
        return ACPClient(
            self.service.defaults["sco"], self.config["workspace"], self.service.execute
        )

    def cli(self, *args, **kwargs):
        return self.client().run(*args, **kwargs)

    def describe(self, name, expected_uid=None):
        job = normalize_job(
            json.loads(self.cli("describe", name, "-o", "json")), self.config["user_id"]
        )
        if job["name"] != name or (expected_uid and job["uid"] != expected_uid):
            raise ValueError("ACP 名称或UID变化，暂停操作以避免作用于其他任务")
        return job

    def history(self, page=1):
        if type(page) is not int or not 1 <= page <= 10000:
            raise ValueError("历史页码无效")
        jobs = parse_jobs(
            self.cli(
                "list",
                "--user-id",
                self.config["user_id"],
                "--page-size",
                "100",
                "--page-token",
                str(page),
                "-o",
                "json",
            ),
            self.config["user_id"],
        )
        with self.lock:
            self.jobs = sorted(jobs, key=lambda j: j["create_time"] or "", reverse=True)
            self.history_page = page
            self.history_more = len(jobs) == 100
            self.history_sampled = time.monotonic()
        return {"page": page, "has_more": self.history_more}

    def reconcile_pending(self):
        with self.lock:
            pending = copy.deepcopy(self.state.get("pending"))
        if not pending:
            return
        jobs = parse_jobs(
            self.cli(
                "list",
                "--user-id",
                self.config["user_id"],
                "--display-name",
                pending["name"],
                "--page-size",
                "100",
                "-o",
                "json",
            ),
            self.config["user_id"],
        )
        matches = [j for j in jobs if j["display_name"] == pending["name"]]
        if len(matches) > 1:
            raise RuntimeError("同一提交记录对应多个任务，请人工核对")
        if not matches:
            return
        job = self.describe(matches[0]["name"], matches[0]["uid"])
        with self.lock:
            self.state["current"] = job["name"]
            self.state["current_uid"] = job["uid"]
            if job["name"] not in self.state.setdefault("managed", []):
                self.state["managed"].append(job["name"])
            self.state.pop("pending", None)
            self.persist()
            self.history_sampled = 0

    def observe(self):
        self.reconcile_pending()
        with self.lock:
            name, uid = self.state.get("current"), self.state.get("current_uid")
        current = self.describe(name, uid) if name else None
        with self.lock:
            self.current_job = current
            if current:
                if not uid:
                    self.state["current_uid"] = current["uid"]
                    self.persist()
                if (
                    current["state"] == "FAILED"
                    and self.state.get("acknowledged_failed_uid") != current["uid"]
                    and not self.state.get("failure")
                ):
                    self.state["failure"] = "ACP 任务失败，请查看 Worker 和任务日志"
                    self.state["failure_action"] = "start"
                    self.persist()
                    self.event(self.state["failure"], status="failed", job=current["name"])
            self.sampled_at, self.error = stamp(), ""
        if time.monotonic() - self.history_sampled >= 60:
            try:
                self.history(self.history_page)
            except Exception as exc:
                self.event("历史列表查询失败：" + str(exc), status="warning")
        if current:
            try:
                self.worker_details(current["name"])
            except Exception as exc:
                with self.lock:
                    if current["name"] in self.workers:
                        self.workers[current["name"]]["error"] = str(exc)
                self.event("Worker 查询失败：" + str(exc), status="warning")
        return current

    def known_job(self, name):
        with self.lock:
            candidates = self.jobs + ([self.current_job] if self.current_job else [])
            job = next((j for j in candidates if j["name"] == name), None)
        if not job:
            raise ValueError("任务不在当前账号的已查询列表中")
        return job

    def worker_details(self, name):
        job = self.known_job(name)
        self.describe(name, job["uid"])
        rows = parse_workers(self.cli("get-workers", name))
        value = {"items": rows, "sampled_at": stamp(), "error": None}
        with self.lock:
            self.workers[name] = value
        return value

    def logs(self, name):
        self.known_job(name)
        with self.lock:
            cached = self.log_cache.get(name)
            if cached and time.monotonic() - cached[0] < 10:
                return copy.deepcopy(cached[1])
        result = self.client().logs(name)
        result["sampled_at"] = stamp()
        with self.lock:
            self.log_cache[name] = (time.monotonic(), result)
        return result

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(
                {
                    "config": self.config,
                    "jobs": self.jobs,
                    "current_job": self.current_job,
                    "history_page": self.history_page,
                    "history_more": self.history_more,
                    "workers": self.workers,
                    "state": self.state,
                    "sampled_at": self.sampled_at,
                    "error": self.error,
                    "operation": self.operation,
                    "events": list(reversed(journal(self.log_path, 80))),
                }
            )

    def update(self, incoming):
        allowed = {"hours", "auto_restart", "schedule", "image", "cluster", "ready_timeout_minutes"}
        if set(incoming) - allowed:
            raise ValueError("不支持的 ACP 设置")
        with self.lock:
            if self.operation:
                raise ValueError("ACP 操作执行中，请先停止或等待完成")
            value = validate_config({**copy.deepcopy(self.config), **incoming})
            if value["schedule"] != self.config["schedule"]:
                self.state["effective_at"] = time.time()
            write_yaml(self.path, value)
            self.config = value
            self.persist()
            self.next_poll = 0
            self.event("ACP 配置已保存", source="config")
        return self.snapshot()

    def submit(self, action, source="manual"):
        if action not in ACTION_LABELS:
            raise ValueError("不支持的 ACP 操作")
        with self.lock:
            if not self.service.execute:
                raise ValueError("演练模式禁止提交操作")
            if self.operation:
                if action == "stop" and self.operation["action"] == "start":
                    self.state.update(stop_requested=True, paused=True)
                    self.operation["stop_requested"] = True
                    self.persist()
                    self.event("已请求取消启动并停止，等待当前操作确认", source=source)
                    return copy.deepcopy(self.operation)
                raise ValueError("已有 ACP 操作执行中")
            self.operation = {
                "id": uuid.uuid4().hex[:12],
                "action": action,
                "source": source,
                "status": "running",
                "started_at": stamp(),
            }
            self.state["operation"] = copy.deepcopy(self.operation)
            self.persist()
            threading.Thread(target=self.execute, args=(action, source), daemon=True).start()
            return copy.deepcopy(self.operation)

    def stop_current(self, job):
        if job["name"] not in self.state.get("managed", []):
            raise RuntimeError("仅允许停止本配置创建的任务")
        if job["uid"] != self.state.get("current_uid"):
            raise RuntimeError("当前任务UID不一致，未提交停止")
        with self.lock:
            if self.operation:
                self.operation["stage"] = "正在停止"
            self.state["failure_action"] = "stop"
            self.persist()
        if job["state"] not in TERMINAL and job["state"] != "SUSPENDING":
            self.cli("stop", job["name"], mutate=True)
        self.wait_for(job["name"], lambda j: j["state"] in TERMINAL, 5)
        with self.lock:
            self.state.pop("stop_requested", None)
            self.state.pop("failure", None)
            self.state.pop("failure_action", None)
            self.persist()

    def wait_for(self, name, predicate, minutes, scheduled_stop=False):
        deadline = time.monotonic() + minutes * 60
        began = (
            datetime.fromisoformat(self.operation["started_at"]).timestamp()
            if self.operation and self.operation.get("started_at")
            else time.time()
        )
        while not self.service.stop_event.wait(5):
            job = self.observe()
            if not job or job["name"] != name:
                raise RuntimeError("无法确认当前ACP任务")
            boundary = latest_boundary(
                datetime.now(ZoneInfo(self.service.settings["timezone"])), self.config["schedule"]
            )
            stop_due = (
                scheduled_stop and boundary and boundary[1] == "stop" and boundary[0] >= began
            )
            if scheduled_stop and (self.state.get("stop_requested") or stop_due):
                self.event(
                    "取消启动并停止任务",
                    source="manual" if self.state.get("stop_requested") else "schedule",
                )
                self.stop_current(job)
                return
            if predicate(job):
                return
            if job["state"] == "FAILED":
                raise RuntimeError("ACP 平台报告失败")
            if time.monotonic() >= deadline:
                raise RuntimeError("ACP 等待平台确认超时，自动启动已暂停")
        raise RuntimeError("管理台正在退出")

    def start_new(self, current, source):
        if self.state.get("pending"):
            raise RuntimeError("前次提交结果不明确，禁止重复提交")
        if current and current["state"] not in TERMINAL:
            raise RuntimeError("当前任务仍在运行或排队，未重复提交")
        if source != "manual":
            action = plan(
                self.config,
                self.state,
                current,
                datetime.now(ZoneInfo(self.service.settings["timezone"])),
            )
            if action != "start":
                return
        name = (
            "oneday-acp-"
            + datetime.now(ZoneInfo(self.service.settings["timezone"])).strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:4]
        )
        with self.lock:
            if self.state.get("stop_requested"):
                self.state.pop("stop_requested", None)
                return
            self.state["pending"] = {"name": name, "created_at": stamp()}
            self.state.pop("acknowledged_failed_uid", None)
            self.persist()
            c = copy.deepcopy(self.config)
        output = self.cli(
            "create",
            "--aec2-name",
            c["cluster"],
            "--job-name",
            name,
            "--container-image-url",
            c["image"],
            "--training-framework",
            "pytorch",
            "--worker-spec",
            c["worker_spec"],
            "--worker-nodes",
            "1",
            "--priority",
            "NORMAL",
            "--quota-type",
            "reserved",
            "--storage-mount",
            c["mount"],
            "--command",
            f"bash /data/260010081/ops/acp-start.sh --hours {c['hours']}",
            mutate=True,
            timeout=90,
        )
        self.event(output[-4000:], source=source)
        self.observe()
        if self.state.get("pending"):
            raise RuntimeError("平台尚未确认新任务；保留提交记录，暂停重复创建")
        with self.lock:
            if not self.state.get("stop_requested"):
                self.state.pop("paused", None)
            self.state.pop("failure", None)
            self.state.pop("failure_action", None)
            self.persist()
        self.wait_for(
            self.state["current"],
            lambda j: j["state"] in {"RUNNING", "SUCCEEDED"},
            c["ready_timeout_minutes"],
            scheduled_stop=True,
        )

    def execute(self, action, source):
        try:
            self.event("开始" + ACTION_LABELS[action], source=source)
            current = self.observe()
            if action == "resume":
                if self.state.get("pending"):
                    raise RuntimeError("提交结果尚不明确，需按任务名核对平台，禁止重复提交")
                with self.lock:
                    if current and current["state"] == "FAILED":
                        self.state["acknowledged_failed_uid"] = current["uid"]
                    self.state.pop("paused", None)
                    self.state.pop("failure", None)
                    self.state.pop("failure_action", None)
            elif action == "stop":
                if source == "manual":
                    with self.lock:
                        self.state["paused"] = True
                        self.persist()
                if self.state.get("pending"):
                    raise RuntimeError("任务提交结果待核对，已暂停自动启动")
                if current:
                    self.stop_current(current)
            else:
                self.start_new(current, source)
            self.event("操作完成：" + ACTION_LABELS[action], source=source, status="succeeded")
        except Exception as exc:
            with self.lock:
                self.state["failure"] = str(exc)
                if self.state.get("failure_action") != "stop":
                    self.state["failure_action"] = action
            self.event(str(exc), source=source, status="failed")
        finally:
            with self.lock:
                self.state["seen_boundary"] = time.time()
                self.state.pop("operation", None)
                self.operation = None
                self.persist()
                self.next_poll = 0

    def loop(self):
        while not self.service.stop_event.wait(1):
            if self.operation or time.monotonic() < self.next_poll:
                continue
            try:
                current = self.observe()
                with self.lock:
                    action = plan(
                        self.config,
                        self.state,
                        current,
                        datetime.now(ZoneInfo(self.service.settings["timezone"])),
                    )
                if action and self.service.execute:
                    self.submit(action, "schedule")
            except Exception as exc:
                self.error = str(exc)
                self.event(str(exc), status="warning", source="monitor")
            self.next_poll = time.monotonic() + self.config["poll_seconds"]
