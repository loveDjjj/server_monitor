import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from controller.config import read_yaml
from controller.policy import decision
from controller.service import Service
from tests.fixtures import make_root


class AutoStartTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 30, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.config = {
            "auto_start_on_stop": True,
            "schedule": {
                "start": {"enabled": False, "time": "08:00"},
                "stop": {"enabled": False, "time": "04:00"},
            },
            "rollover": {"enabled": False, "period_minutes": 230},
        }

    def test_stopped_only_and_disabled_compatibility(self):
        for state in ("STOPPED", "SUSPENDED"):
            self.assertEqual(
                decision(self.config, {}, state, 0, self.now), ("start", "停止后自动启动")
            )
        for state in ("RUNNING", "PROGRESSING", "CREATING", "UNKNOWN", "MISSING", "FAILED"):
            self.assertIsNone(decision(self.config, {}, state, 1, self.now)[0])
        self.config.pop("auto_start_on_stop")
        self.assertIsNone(decision(self.config, {}, "STOPPED", 0, self.now)[0])

    def test_pause_and_stop_schedule_override(self):
        for state in (
            {"manual_pause_at": self.now.timestamp()},
            {"failed_operation_at": self.now.timestamp(), "failed_operation_action": "start"},
        ):
            self.assertIsNone(decision(self.config, state, "STOPPED", 0, self.now)[0])
        self.config["schedule"]["stop"]["enabled"] = True
        self.assertIsNone(decision(self.config, {}, "STOPPED", 0, self.now)[0])

    def test_rollover_still_works(self):
        self.config["rollover"]["enabled"] = True
        self.assertEqual(
            decision(
                self.config,
                {"run_started_at": self.now.timestamp() - 240 * 60},
                "RUNNING",
                1,
                self.now,
            )[0],
            "restart",
        )

    def test_persistence_validation_and_instance_command(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            make_root(target)
            service = Service(target, execute=False)
            actor = next(iter(service.actors.values()))
            actor.state["manual_pause_at"] = 123
            service.update_instance(actor.key, {"auto_start_on_stop": True})
            saved = read_yaml(target / "config/instances.yaml")
            self.assertTrue(saved["instances"][0]["auto_start_on_stop"])
            self.assertEqual(actor.state["manual_pause_at"], 123)
            for value in (1, "true", None):
                with self.assertRaises(ValueError):
                    service.update_instance(actor.key, {"auto_start_on_stop": value})
            for item in saved["instances"]:
                client = service.actors[item["key"]].client()
                payload = client.render_cci_config()
                self.assertEqual(payload["template"]["containers"][0]["command"], item["command"])
            reloaded = Service(target, execute=False)
            self.assertTrue(reloaded.instance_config(actor.key)["auto_start_on_stop"])
