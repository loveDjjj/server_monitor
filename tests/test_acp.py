import copy
import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from controller.acp import ACP
from controller.acp_client import normalize_job, parse_jobs, parse_workers
from controller.acp_policy import plan, validate_config
from controller.config import write_yaml
from tests.fixtures import acp_config, job


class ACPTests(unittest.TestCase):
    def setUp(self):
        self.c = acp_config()
        self.now = datetime(2026, 10, 30, 12, tzinfo=ZoneInfo("Asia/Shanghai"))

    def actor(self, folder):
        root = Path(folder)
        write_yaml(root / "config/acp.yaml", self.c)
        service = SimpleNamespace(
            root=root,
            execute=True,
            stop_event=threading.Event(),
            settings={"timezone": "Asia/Shanghai"},
        )
        a = ACP(service)
        a.worker_details = Mock()
        return a

    def test_worker_ips_columns_and_invalid_table(self):
        rows = parse_workers(
            "| WORKER_NAME | RESOURCE | HOST_IP | POD_IP | PHASE |\n| worker-0 | 4 GPUs | 10.1.2.3 | 10.2.3.4 | Running |\n| | memory | | | |\n| worker-1 | 4 GPUs | - | - | Pending |"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["pod_ip"], "10.2.3.4")
        self.assertIsNone(rows[1]["pod_ip"])
        with self.assertRaises(ValueError):
            parse_workers("upstream unavailable")
        self.assertEqual(parse_jobs("No jobs found", "owner"), [])

    def test_default_and_successful_end_restart(self):
        self.assertIsNone(plan(self.c, {}, None, self.now))
        self.c["auto_restart"] = True
        for state in ("SUCCEEDED", "SUSPENDED"):
            self.assertEqual(plan(self.c, {}, job(state=state), self.now), "start")
        for state in ("FAILED", "RUNNING", "STARTING", "UNKNOWN", "QUEUEING", "SUSPENDING"):
            self.assertIsNone(plan(self.c, {}, job(state=state), self.now))

    def test_failure_blocks_start_but_not_scheduled_stop(self):
        self.c["schedule"]["stop"]["enabled"] = True
        now = self.now.replace(hour=23, minute=1)
        state = {"failure": "startup timed out", "failure_action": "start"}
        self.assertEqual(plan(self.c, state, job(), now), "stop")
        state["failure_action"] = "stop"
        self.assertIsNone(plan(self.c, state, job(), now))

    def test_pause_pending_and_cross_midnight(self):
        self.c["auto_restart"] = True
        for state in ({"paused": True}, {"failure": "timeout"}, {"pending": {"name": "one"}}):
            self.assertIsNone(plan(self.c, state, None, self.now))
        for a in ("start", "stop"):
            self.c["schedule"][a]["enabled"] = True
        self.assertIsNone(plan(self.c, {}, None, self.now.replace(hour=1)))
        self.assertEqual(plan(self.c, {}, None, self.now), "start")

    def test_schedule_edits_and_daily_start_do_not_backfill(self):
        self.c["schedule"]["start"]["enabled"] = True
        self.assertEqual(plan(self.c, {}, None, self.now), "start")
        for state in (
            {"seen_boundary": self.now.timestamp()},
            {"effective_at": self.now.timestamp()},
        ):
            self.assertIsNone(plan(self.c, state, job(state="SUCCEEDED"), self.now))

    def test_validation(self):
        for field, value in [("hours", 32), ("hours", True), ("auto_restart", "true")]:
            c = copy.deepcopy(self.c)
            c[field] = value
            with self.assertRaises(ValueError):
                validate_config(c)

    def test_timeout_intent_survives_restart_and_blocks_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.observe = Mock(return_value=None)
            a.cli = Mock(side_effect=TimeoutError("timeout"))
            a.execute("start", "manual")
            self.assertIn("pending", a.state)
            a.execute("start", "manual")
            self.assertEqual(a.cli.call_count, 1)
            self.assertEqual(ACP(a.service).state["pending"], a.state["pending"])

    def test_pending_exact_identity_and_current_independent_of_history(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.state["pending"] = {"name": "requested"}
            owned = job(display="requested")

            def cli(*args, **kwargs):
                if args[0] == "describe":
                    return json.dumps(owned)
                return json.dumps([owned]) if "--display-name" in args else "[]"

            a.cli = Mock(side_effect=cli)
            current = a.observe()
            self.assertEqual(current["name"], "pt-one")
            self.assertEqual(a.state["current_uid"], "uid-one")
            self.assertNotIn("pending", a.state)
            self.assertEqual(a.jobs, [])
            self.assertIsNone(a.state.get("failure"))
            self.assertEqual(a.snapshot()["current_job"]["uid"], "uid-one")

    def test_reject_other_owner_and_reused_name(self):
        with self.assertRaises(ValueError):
            normalize_job(job(), "other")
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.state.update(current="pt-one", current_uid="old-uid")
            a.cli = Mock(return_value=json.dumps(job()))
            with self.assertRaises(ValueError):
                a.observe()

    def test_resume_acknowledges_only_failed_uid(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.state.update(
                current="pt-one", current_uid="uid-one", managed=["pt-one"], failure="failed"
            )
            a.cli = Mock(
                side_effect=lambda *args, **kw: json.dumps(job(state="FAILED"))
                if args[0] == "describe"
                else "[]"
            )
            a.execute("resume", "manual")
            a.observe()
            self.assertNotIn("failure", a.state)
            self.c["auto_restart"] = True
            self.assertEqual(plan(self.c, a.state, a.current_job, self.now), "start")
            self.assertIsNone(plan(self.c, a.state, job(state="FAILED", uid="new-uid"), self.now))

    def test_stop_request_during_start_is_durable_and_no_second_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.operation = {"action": "start"}
            with patch("controller.acp.threading.Thread") as thread:
                a.submit("stop")
                thread.assert_not_called()
            self.assertTrue(a.state["stop_requested"])
            self.assertTrue(a.state["paused"])
            self.assertTrue(ACP(a.service).state["stop_requested"])

    def test_cancel_before_create_does_not_submit(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.state.update(stop_requested=True, paused=True)
            a.observe = Mock(return_value=None)
            a.cli = Mock()
            a.execute("start", "manual")
            a.cli.assert_not_called()
            self.assertTrue(a.state["paused"])

    def test_wait_honors_manual_stop_and_stop_boundary(self):
        for manual in (True, False):
            with self.subTest(manual=manual), tempfile.TemporaryDirectory() as folder:
                a = self.actor(folder)
                a.state.update(
                    current="pt-one",
                    current_uid="uid-one",
                    managed=["pt-one"],
                    stop_requested=manual,
                )
                a.service.stop_event = Mock()
                a.service.stop_event.wait.return_value = False
                a.observe = Mock(side_effect=[job(state="STARTING"), job(state="SUSPENDED")])
                a.cli = Mock()
                with (
                    patch("controller.acp.time.time", return_value=10),
                    patch(
                        "controller.acp.latest_boundary",
                        return_value=(11, "stop") if not manual else None,
                    ),
                ):
                    a.wait_for(
                        "pt-one", lambda j: j["state"] == "RUNNING", 120, scheduled_stop=True
                    )
                a.cli.assert_called_once_with("stop", "pt-one", mutate=True)
                self.assertNotIn("stop_requested", a.state)

    def test_stop_failure_is_not_retried_by_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.state.update(current="pt-one", current_uid="uid-one", managed=["pt-one"])
            a.observe = Mock(return_value=job())
            a.cli = Mock(side_effect=TimeoutError())
            a.execute("stop", "schedule")
            self.assertEqual(a.state["failure_action"], "stop")

    def test_stop_boundary_during_create_is_not_missed(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.operation = {"action": "start", "started_at": "1970-01-01T00:00:10+00:00"}
            a.state.update(current="pt-one", current_uid="uid-one", managed=["pt-one"])
            a.service.stop_event = Mock()
            a.service.stop_event.wait.return_value = False
            a.observe = Mock(side_effect=[job(state="STARTING"), job(state="SUSPENDED")])
            a.cli = Mock()
            with (
                patch("controller.acp.time.time", return_value=20),
                patch("controller.acp.latest_boundary", return_value=(15, "stop")),
            ):
                a.wait_for("pt-one", lambda j: j["state"] == "RUNNING", 120, scheduled_stop=True)
            a.cli.assert_called_once_with("stop", "pt-one", mutate=True)

    def test_dry_run_and_duplicate_start_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.operation = {"action": "start"}
            with self.assertRaises(ValueError):
                a.submit("start")
            a.service.execute = False
            with self.assertRaises(ValueError):
                a.submit("stop")

    def test_running_not_duplicated_and_unowned_not_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.observe = Mock(return_value=job())
            a.cli = Mock()
            a.execute("start", "manual")
            a.cli.assert_not_called()
            a.execute("stop", "manual")
            a.cli.assert_not_called()

    def test_successful_create_arguments_and_late_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)

            def observe():
                if a.state.get("pending"):
                    a.state.update(
                        current="pt-one",
                        current_uid="uid-one",
                        managed=["pt-one"],
                        stop_requested=True,
                        paused=True,
                    )
                    a.state.pop("pending")
                return None

            a.observe = Mock(side_effect=observe)
            a.cli = Mock(return_value="submitted")
            a.wait_for = Mock()
            a.execute("start", "manual")
            self.assertIn("N6lS.Iu.I10.4.56c792g", a.cli.call_args.args)
            self.assertIn("bash /data/260010081/ops/acp-start.sh --hours 30", a.cli.call_args.args)
            self.assertTrue(a.state["paused"])
            self.assertNotIn("failure", a.state)

    def test_paginated_history_keeps_current_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.current_job = normalize_job(job(), "owner")
            a.state["current"] = "pt-one"
            a.cli = Mock(return_value=json.dumps([job(name="pt-old")]))
            a.history(2)
            self.assertIn("2", a.cli.call_args.args)
            self.assertEqual(a.snapshot()["current_job"]["name"], "pt-one")
            self.assertEqual(a.history_page, 2)

    def test_log_snapshot_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.actor(folder)
            a.current_job = normalize_job(job(), "owner")
            client = Mock()
            client.logs.return_value = {"text": "partial output", "partial": True}
            a.client = Mock(return_value=client)
            self.assertTrue(a.logs("pt-one")["partial"])
            a.logs("pt-one")
            client.logs.assert_called_once()
