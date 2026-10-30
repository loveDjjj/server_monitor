"""Independent configuration fixtures; do not read deployment YAML."""

from controller.config import write_yaml


def acp_config():
    return {
        "version": 1,
        "workspace": "space",
        "user_id": "owner",
        "cluster": "cluster",
        "worker_spec": "N6lS.Iu.I10.4.56c792g",
        "image": "registry/test:tag",
        "mount": "volume:/data",
        "hours": 30,
        "poll_seconds": 30,
        "ready_timeout_minutes": 120,
        "auto_restart": False,
        "schedule": {
            "start": {"enabled": False, "time": "08:00"},
            "stop": {"enabled": False, "time": "23:00"},
        },
    }


def job(name="pt-one", state="RUNNING", uid="uid-one", display="test"):
    return {
        "name": name,
        "uid": uid,
        "display_name": display,
        "state": state,
        "ownership": {"user_id": "owner"},
        "roles": [],
        "create_time": "2026-10-30T00:00:00Z",
    }


def make_root(root):
    defaults = {
        "version": 1,
        "sco": {
            "executable": "sco",
            "profile": "default",
            "subscription": "sub",
            "resource_group": "default",
            "workspace": "space",
            "zone": "zone",
        },
        "container": {"name": "main", "image": "image", "command": ["bash", "fallback.sh"]},
        "storage": [],
        "cci": {
            "replicas": 1,
            "priority": "NORMAL",
            "quota_type": "RESERVED",
            "instance_affinity": "POD_AFFINITY_TYPE_UNSPECIFIED",
            "application_ports": [22],
        },
        "dnat": {"enabled": False},
    }
    instances = []
    for key, hours, gpus in [("h100", "4", 2), ("cpu", "24", 0)]:
        instances.append(
            {
                "key": key,
                "name": key,
                "display_name": key,
                "workspace": "space",
                "cluster": "cluster",
                "resources": {"spec": "spec", "gpu_count": gpus, "cpu": "2", "memory": "4GiB"},
                "image": "image",
                "command": ["bash", "start.sh", "--hours", hours],
                "schedule": acp_config()["schedule"],
                "rollover": {"enabled": False, "period_minutes": 230},
                "dnat": {"enabled": False},
            }
        )
    write_yaml(root / "config/defaults.yaml", defaults)
    write_yaml(
        root / "config/instances.yaml",
        {
            "version": 2,
            "timezone": "Asia/Shanghai",
            "status_interval_seconds": 30,
            "instances": instances,
        },
    )
    write_yaml(root / "config/gpu-monitor.yaml", {"enabled": False, "clusters": []})
    write_yaml(root / "config/acp.yaml", acp_config())
