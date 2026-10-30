"""ACP CLI arguments, account validation and response parsing."""

import ipaddress
import json

from sco_client import Runner


def parse_workers(output):
    rows = []
    header = None
    for line in output.splitlines():
        cells = [s.strip() for s in line.split("|")[1:-1]]
        if "WORKER_NAME" in cells:
            header = cells
            if not {"WORKER_NAME", "HOST_IP", "POD_IP", "PHASE"} <= set(header):
                raise ValueError("ACP Worker 表头发生变化")
            continue
        if not header or len(cells) != len(header):
            continue
        values = dict(zip(header, cells))
        if not values["WORKER_NAME"]:
            continue
        row = {"name": values["WORKER_NAME"], "state": values["PHASE"]}
        for field in ("host_ip", "pod_ip"):
            try:
                row[field] = str(ipaddress.ip_address(values[field.upper()]))
            except ValueError:
                row[field] = None
        rows.append(row)
    if header is None and output.strip() and "no workers" not in output.lower():
        raise ValueError("ACP Worker 输出不是可识别的表格")
    return rows


def normalize_job(job, user_id):
    if not isinstance(job, dict) or job.get("ownership", {}).get("user_id") != user_id:
        raise ValueError("ACP 任务不属于配置账号")
    if not job.get("name") or not job.get("uid") or not job.get("state"):
        raise ValueError("ACP 任务缺少名称、UID或状态")
    roles = job.get("roles") or []
    role = roles[0] if roles else {}
    return {
        k: job.get(k)
        for k in (
            "name",
            "display_name",
            "uid",
            "state",
            "create_time",
            "start_time",
            "complete_time",
        )
    } | {
        "cluster": job.get("resource_pool", {}).get("name"),
        "image": role.get("image_path"),
        "command": role.get("startup_script"),
        "resources": role.get("resource_spec", []),
    }


def parse_jobs(output, user_id):
    if output.strip().lower() == "no jobs found":
        return []
    raw = json.loads(output)
    if not isinstance(raw, list):
        raise ValueError("ACP 列表格式不符合预期")
    return [
        normalize_job(j, user_id) for j in raw if j.get("ownership", {}).get("user_id") == user_id
    ]


class ACPClient:
    def __init__(self, sco, workspace, execute):
        self.sco, self.workspace, self.execute = sco, workspace, execute

    def argv(self, *args):
        s = self.sco
        return [
            s["executable"],
            "--profile",
            s["profile"],
            "--subscription",
            s["subscription"],
            "--resource-group",
            s["resource_group"],
            "acp",
            "jobs",
            *args,
            "--workspace-name",
            self.workspace,
        ]

    def run(self, *args, mutate=False, timeout=45):
        return Runner(not self.execute, timeout).run(self.argv(*args), mutate=mutate).stdout

    def logs(self, name):
        return Runner(True, 3).capture(self.argv("stream-logs", name))
