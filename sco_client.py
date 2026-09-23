"""SCO CLI adapter for capacity, CCI lifecycle, rendering, and DNAT."""

from __future__ import annotations

import copy
import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml


ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_FILE = ROOT / "logs" / "manager.log"

RUNNING_STATES = {"RUNNING"}
STARTING_STATES = {"CREATING", "SCHEDULED", "PROGRESSING", "STARTING"}
STOPPED_STATES = {"SUSPENDED", "STOPPED"}

# The Windows SCO launcher updates shared entrypoint and runtime-state files.
# Serialize invocations so instance and GPU monitor threads cannot race there.
SCO_COMMAND_LOCK = threading.Lock()


class KeeperError(RuntimeError):
    pass


class CommandError(KeeperError):
    def __init__(self, argv: Sequence[str], returncode: int, output: str):
        super().__init__(f"command failed ({returncode}): {shlex.join(argv)}\n{output.strip()}")
        self.argv = list(argv)
        self.returncode = returncode
        self.output = output


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str


@dataclass
class AppStatus:
    exists: bool
    state: str = "MISSING"
    ready_replicas: int = 0
    uid: str = ""
    name: str = ""
    raw: dict[str, Any] | None = None


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise KeeperError(f"configuration must be a mapping: {path}")
    return data


def parse_reserved_idle_gpus(output: str) -> int:
    for line in output.splitlines():
        if "GPU_NUMBER" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] != "GPU_NUMBER":
            continue
        try:
            return int(float(cells[3]))
        except ValueError as exc:
            raise KeeperError(f"invalid RESERVED IDLE GPU value in line: {line}") from exc
    raise KeeperError("GPU_NUMBER row not found in AEC2 usage output")


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                self.data = json.load(handle)
        else:
            self.data = {}
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, self.path)

    def update(self, **values: Any) -> None:
        self.data.update(values)
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save()


class Runner:
    def __init__(self, dry_run: bool, timeout: int):
        self.dry_run = dry_run
        self.timeout = timeout

    def run(
        self,
        argv: Sequence[str],
        *,
        mutate: bool = False,
        check: bool = True,
    ) -> CommandResult:
        command = [str(item) for item in argv]
        if mutate and self.dry_run:
            logging.info("DRY-RUN %s", shlex.join(command))
            return CommandResult(command, 0, "")
        logging.debug("RUN %s", shlex.join(command))
        with SCO_COMMAND_LOCK:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        result = CommandResult(command, completed.returncode, completed.stdout)
        if check and result.returncode != 0:
            raise CommandError(command, result.returncode, result.stdout)
        return result


class SCOClient:
    def __init__(
        self,
        base_config: dict[str, Any],
        instance_config: dict[str, Any],
        state: StateStore,
        rendered_path: Path,
        dry_run_override: bool | None = None,
    ):
        self.base = base_config
        self.task = instance_config
        self.state = state
        configured_dry_run = bool(instance_config["controller"].get("dry_run", True))
        dry_run = configured_dry_run if dry_run_override is None else dry_run_override
        self.runner = Runner(dry_run, int(instance_config["controller"].get("command_timeout_seconds", 60)))
        self.rendered_path = rendered_path
        self._validate()

    def _validate(self) -> None:
        if int(self.task["application"]["gpu_count"]) < 0:
            raise KeeperError("GPU count cannot be negative")
        ports = [int(value) for value in self.base["dnat"].get("external_port_candidates", [])]
        if self.base['dnat'].get('enabled') and not ports:
            raise KeeperError("at least one DNAT external port candidate is required")

    @property
    def sco(self) -> str:
        return str(self.base["sco"]["executable"])

    @property
    def app_name(self) -> str:
        return str(self.task["application"]["name"])

    def sco_global(self) -> list[str]:
        config = self.base["sco"]
        return [
            self.sco,
            "--profile",
            str(config["profile"]),
            "--subscription",
            str(config["subscription"]),
            "--resource-group",
            str(config["resource_group"]),
        ]

    def cci_command(self, *args: str) -> list[str]:
        return [
            *self.sco_global(),
            "cci",
            "apps",
            *args,
            "--workspace-name",
            str(self.base["sco"]["workspace"]),
        ]

    def eip_command(self, *args: str) -> list[str]:
        return [*self.sco_global(), "eip", *args]

    def get_free_gpus(self) -> int:
        cluster = str(self.task["application"]["cluster"])
        result = self.runner.run([*self.sco_global(), "aec2", "clusters", "usage", "--name", cluster])
        return parse_reserved_idle_gpus(result.stdout)

    def get_app(self) -> AppStatus:
        result = self.runner.run(
            self.cci_command("describe", self.app_name, "-o", "json"),
            check=False,
        )
        if result.returncode != 0:
            lowered = result.stdout.lower()
            if "appnotfound" in lowered or ('"code":5' in lowered and "not found" in lowered):
                return AppStatus(False, name=self.app_name)
            raise CommandError(result.argv, result.returncode, result.stdout)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise KeeperError("CCI describe did not return valid JSON") from exc
        return AppStatus(
            True,
            str(payload.get("state", "UNKNOWN")).upper(),
            int(payload.get("readyreplicas", payload.get("ready_replicas", 0)) or 0),
            str(payload.get("uid", "")),
            str(payload.get("name", self.app_name)),
            payload,
        )

    def render_cci_config(self) -> dict[str, Any]:
        app = self.task["application"]
        base_cci = self.base["cci"]
        mounts = copy.deepcopy(self.base["storage"])
        payload = {
            "display_name": app["display_name"],
            "resource_pool": {
                "name": app["cluster"],
                "available_zone": self.base["sco"]["zone"],
            },
            "replicas": int(base_cci["replicas"]),
            "template": {
                "containers": [
                    {
                        "name": self.base["container"]["name"],
                        "image_path": self.base["container"]["image"],
                        "resource_request": {
                            "cpu": str(app["cpu"]),
                            "nvidia.com/gpu": str(app["gpu_count"]),
                            "memory": app["memory"],
                        },
                        "command": copy.deepcopy(self.base["container"]["command"]),
                        "volume_mounts": mounts,
                    }
                ],
                "init_containers": [],
                "resource_spec": {"name": app["resource_spec"]},
            },
            "scheduling": {
                "priority": base_cci["priority"],
                "quota_type": base_cci["quota_type"],
                "instance_affinity": base_cci["instance_affinity"],
            },
        }
        self.rendered_path.parent.mkdir(parents=True, exist_ok=True)
        with self.rendered_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=False, sort_keys=False)
        return payload

    def create_app(self) -> None:
        self.render_cci_config()
        ports = ",".join(str(value) for value in self.base["cci"]["application_ports"])
        self.runner.run(
            self.cci_command(
                "create",
                self.app_name,
                "--config",
                str(self.rendered_path),
                "--ports",
                ports,
            ),
            mutate=True,
        )
        if not self.runner.dry_run:
            self.state.update(run_started_at=time.time(), last_action="create", cooldown_until=0)

    def start_app(self) -> None:
        self.runner.run(self.cci_command("start", self.app_name), mutate=True)
        if not self.runner.dry_run:
            self.state.update(run_started_at=time.time(), last_action="start", cooldown_until=0)

    def stop_app(self, reason: str, free_gpus: int, cooldown_seconds: int = 30) -> None:
        self.runner.run(self.cci_command("stop", self.app_name), mutate=True)
        if not self.runner.dry_run:
            self.state.update(
                last_action="stop",
                last_stop_reason=reason,
                stopped_at=time.time(),
                cooldown_until=time.time() + cooldown_seconds,
                run_started_at=0,
                pre_stop_free_gpus=free_gpus,
            )

    def delete_app(self) -> None:
        self.runner.run(self.cci_command("delete", self.app_name), mutate=True)
        if not self.runner.dry_run:
            self.state.update(last_action="delete", run_started_at=0, cooldown_until=0)

    def get_eip(self) -> dict[str, Any]:
        result = self.runner.run(
            self.eip_command("describe", self.base["dnat"]["eip_name"], "-o", "json")
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise KeeperError("EIP describe did not return valid JSON") from exc
        if not isinstance(payload, dict) or not payload.get("uid"):
            raise KeeperError("EIP describe response is missing its UID")
        return payload

    def get_dnat_rules(self) -> list[dict[str, Any]]:
        dnat = self.base["dnat"]
        result = self.runner.run(self.eip_command("dnat", "list", dnat["eip_name"], "-o", "json"))
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise KeeperError("EIP DNAT list did not return valid JSON") from exc
        return list(payload.get("dnat_rules", []))

    def managed_rule(self, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
        prefix = str(self.base["dnat"]["rule_prefix"])
        reusable = set(self.base["dnat"].get("reusable_rule_names", []))
        for rule in rules:
            name = str(rule.get("name", ""))
            if (name.startswith(prefix) or name in reusable) and not rule.get("deleted", False):
                return rule
        return None

    def choose_dnat_port(self, rules: list[dict[str, Any]]) -> int:
        managed = self.managed_rule(rules)
        if managed:
            return int(managed["properties"]["external_port"])
        used = {
            int(rule.get("properties", {}).get("external_port", 0) or 0)
            for rule in rules
            if not rule.get("deleted", False)
        }
        for candidate in self.base["dnat"]["external_port_candidates"]:
            if int(candidate) not in used:
                return int(candidate)
        raise KeeperError("all configured DNAT external ports are already in use")

    def build_dnat_rule(self, port: int, app: AppStatus, eip: dict[str, Any]) -> dict[str, Any]:
        dnat = self.base["dnat"]
        sco_config = self.base["sco"]
        rule_name = f"{dnat['rule_prefix']}-{port}"
        ownership = (app.raw or {}).get("ownership", {})
        eip_properties = eip.get("properties", {})
        return {
            "id": (
                f"/subscriptions/{sco_config['subscription']}"
                f"/resourceGroups/{sco_config['resource_group']}"
                f"/zones/{sco_config['zone']}/eips/{dnat['eip_name']}"
                f"/dnatRules/{rule_name}"
            ),
            "name": rule_name,
            "display_name": rule_name,
            "description": f"managed by sco-cci-manager for {self.app_name}",
            "uid": str(uuid.uuid4()),
            "resource_type": "network.eip.v1.dnatRule",
            "creator_id": ownership.get("userid", ""),
            "owner_id": eip.get("owner_id", ""),
            "tenant_id": eip.get("tenant_id", sco_config["subscription"]),
            "zone": sco_config["zone"],
            "state": "ACTIVE",
            "sku_id": "",
            "tags": {},
            "properties": {
                "nat_gateway_id": eip_properties.get("association_id", ""),
                "eip_id": eip["uid"],
                "external_ip": dnat["external_ip"],
                "external_port": str(port),
                "protocol": dnat["protocol"],
                "internal_ip": "",
                "internal_port": str(dnat["internal_port"]),
                "priority": 1,
                "internal_instance_type": dnat["internal_instance_type"],
                "internal_instance_name": app.uid,
            },
        }

    def ensure_dnat(self, app: AppStatus) -> None:
        if not self.base["dnat"].get("enabled", False) or not app.uid:
            return
        rules = self.get_dnat_rules()
        managed = self.managed_rule(rules)
        port = self.choose_dnat_port(rules)
        if managed is None:
            rule = self.build_dnat_rule(port, app, self.get_eip())
            rule_name = rule["name"]
            self.runner.run(
                self.eip_command(
                    "dnat",
                    "create",
                    self.base["dnat"]["eip_name"],
                    rule_name,
                    "-d",
                    json.dumps(rule, separators=(",", ":")),
                ),
                mutate=True,
            )
            if self.runner.dry_run:
                return
            rules = self.get_dnat_rules()
            managed = self.managed_rule(rules)
            if managed is None:
                raise KeeperError("managed DNAT rule was not visible after creation")
        properties = managed.get("properties", {})
        if managed.get("state") == "ACTIVE" and properties.get("internal_instance_name") == app.uid:
            return
        if managed.get("state") == "ACTIVE" and properties.get("internal_instance_name") != app.uid:
            logging.info(
                "reclaiming DNAT rule %s from instance %s",
                managed["name"],
                properties.get("internal_instance_name", ""),
            )
            self.runner.run(
                self.eip_command(
                    "dnat",
                    "unbind",
                    self.base["dnat"]["eip_name"],
                    managed["name"],
                ),
                mutate=True,
            )
            return
        if managed.get("state") in {"BINDING", "BINDWAITING"}:
            logging.info("DNAT rule %s is still %s", managed["name"], managed["state"])
            return
        bind_payload = copy.deepcopy(managed)
        bind_properties = bind_payload.setdefault("properties", {})
        bind_properties["internal_port"] = str(self.base["dnat"]["internal_port"])
        bind_properties["internal_instance_name"] = app.uid
        bind_properties["internal_instance_type"] = self.base["dnat"]["internal_instance_type"]
        self.runner.run(
            self.eip_command(
                "dnat",
                "bind",
                self.base["dnat"]["eip_name"],
                managed["name"],
                "-d",
                json.dumps(bind_payload, separators=(",", ":")),
            ),
            mutate=True,
        )

def configure_logging(verbose: bool = False, console: bool | None = None) -> None:
    DEFAULT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    enable_console = console if console is not None else sys.stderr.isatty()
    if enable_console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
    file_handler = logging.FileHandler(DEFAULT_LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
