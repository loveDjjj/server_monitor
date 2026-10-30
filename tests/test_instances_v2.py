import copy
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from controller.instance import InstanceActor
from controller.storage import journal
from sco_client import AppStatus


class FakeService:
    def __init__(self, root):
        self.root = Path(root)
        self.stop_event = threading.Event()
        self.execute = True
        self.command_slots = threading.BoundedSemaphore(3)
        self.settings = {"timezone": "Asia/Shanghai", "status_interval_seconds": 60}
        self.item = {
            "schedule": {
                "start": {"enabled": False, "time": "09:00"},
                "stop": {"enabled": False, "time": "00:00"},
            },
            "rollover": {"enabled": True, "period_minutes": 8},
        }

    def instance_config(self, key):
        return copy.deepcopy(self.item)


class ActorTests(unittest.TestCase):
    def test_start_wait_timeout_is_sixty_minutes(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            service.stop_event = Mock()
            service.stop_event.is_set.return_value = False
            service.stop_event.wait.return_value = False
            actor = InstanceActor(service, "one")
            actor.observe = Mock(return_value=AppStatus(True, "PROGRESSING", 0, "x"))
            with patch("controller.instance.time.monotonic", side_effect=[0, 1801, 3599, 3601]):
                with self.assertRaisesRegex(RuntimeError, "PROGRESSING"):
                    actor.wait(lambda app: app.state == "RUNNING")
            self.assertEqual(service.stop_event.wait.call_count, 2)

    def test_ready_poll_recovers_from_one_read_timeout_without_resubmitting_start(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            actor = InstanceActor(service, "one")
            client = Mock()
            actor.client = Mock(return_value=client)
            actor.observe = Mock(
                side_effect=[
                    AppStatus(True, "SUSPENDED", 0, "x"),
                    subprocess.TimeoutExpired(["sco", "describe"], 45),
                    AppStatus(True, "RUNNING", 1, "x"),
                ]
            )
            op = {"id": "recover", "action": "start", "source": "manual", "steps": []}
            actor.execute_operation(op)
            self.assertEqual(op["status"], "succeeded")
            client.start_app.assert_called_once()
            events = journal(actor.logs)
            self.assertEqual([event["attempt"] for event in events if "attempt" in event], [1])
            self.assertTrue(any("查询恢复" in event["message"] for event in events))

    def test_ready_poll_stops_after_three_timeouts_and_never_resubmits_start(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), "one")
            client = Mock()
            actor.client = Mock(return_value=client)
            actor.observe = Mock(
                side_effect=[AppStatus(True, "SUSPENDED", 0, "x")]
                + [subprocess.TimeoutExpired(["sco", "describe"], 45) for _ in range(3)]
            )
            op = {"id": "timeout", "action": "start", "source": "manual", "steps": []}
            actor.execute_operation(op)
            self.assertEqual(op["status"], "failed")
            self.assertIn("连续3次状态查询超时", op["message"])
            self.assertIn("failed_operation_at", actor.state)
            client.start_app.assert_called_once()
            self.assertEqual(
                [event["attempt"] for event in journal(actor.logs) if "attempt" in event], [1, 2, 3]
            )

    def test_stop_wait_does_not_retry_read_timeout(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), "one")
            actor.observe = Mock(side_effect=subprocess.TimeoutExpired(["sco", "describe"], 45))
            with self.assertRaises(subprocess.TimeoutExpired):
                actor.wait(lambda app: app.state == "SUSPENDED")
            actor.observe.assert_called_once()

    def test_platform_transition_logs_only_changes_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            actor = InstanceActor(service, "one")
            client = Mock()
            actor.client = Mock(return_value=client)
            client.get_app.return_value = AppStatus(
                True, "SUSPENDED", 0, "x", raw={"update_time": "platform-time"}
            )
            actor.observe()
            actor.observe()
            client.get_app.return_value = AppStatus(
                True, "RUNNING", 1, "x", raw={"update_time": "later"}
            )
            with patch("controller.instance.get_instances", return_value=[]):
                actor.observe()
            transitions = [event for event in journal(actor.logs) if event["source"] == "platform"]
            self.assertEqual(len(transitions), 2)
            self.assertEqual(transitions[1]["previous"]["state"], "SUSPENDED")
            self.assertEqual(transitions[1]["current"]["platform_updated_at"], "later")
            self.assertIn("原因需核对平台审计", transitions[1]["message"])
            self.assertEqual(actor.snapshot()["observed"]["platform_updated_at"], "later")
            restored = InstanceActor(service, "one")
            restored.client = Mock(return_value=client)
            restored.observe()
            self.assertEqual(
                len([event for event in journal(actor.logs) if event["source"] == "platform"]), 2
            )

    def test_platform_container_restart_is_logged_without_app_state_change(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), "one")
            client = Mock()
            client.base = {"sco": {}}
            actor.client = Mock(return_value=client)
            client.get_app.return_value = AppStatus(True, "RUNNING", 1, "x")
            first = {
                "seconds": 100,
                "instance_uid": "pod",
                "restart_count": 0,
                "last_started_time": "2026-10-15T01:00:00Z",
            }
            second = {
                **first,
                "seconds": 10,
                "restart_count": 1,
                "last_started_time": "2026-10-15T02:00:00Z",
            }
            with (
                patch("controller.instance.get_instances", return_value=[]),
                patch("controller.instance.running_clock", side_effect=[first, second, second]),
            ):
                actor.observe()
                actor.observe()
                actor.observe()
            events = [event for event in journal(actor.logs) if event["source"] == "platform"]
            self.assertEqual(len(events), 2)
            self.assertEqual(events[1]["previous_clock"]["restart_count"], 0)
            self.assertEqual(events[1]["current_clock"]["restart_count"], 1)

    def test_resume_keeps_overdue_runtime_and_clears_failure_persistently(self):
        from datetime import datetime

        from controller.policy import decision

        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            actor = InstanceActor(service, "one")
            began = time.time() - 3600
            actor.state.update(
                run_started_at=began,
                runtime_fresh=True,
                failed_operation_at=time.time(),
                failed_operation_action="restart",
                manual_pause_at=time.time(),
            )
            actor.observe = Mock(return_value=AppStatus(True, "RUNNING", 1, "x"))
            actor.client = Mock()
            actor.execute_operation(
                {
                    "id": "resume-test",
                    "action": "resume",
                    "source": "manual",
                    "steps": [],
                    "created_at": "2026-09-22T10:00:00+00:00",
                }
            )
            self.assertEqual(actor.state["run_started_at"], began)
            self.assertNotIn("failed_operation_at", actor.state)
            self.assertNotIn("manual_pause_at", actor.state)
            self.assertEqual(
                decision(actor.config(), actor.state, "RUNNING", True, datetime.now())[0], "restart"
            )
            actor.client.assert_not_called()
            restored = InstanceActor(service, "one")
            self.assertNotIn("failed_operation_at", restored.state)
            self.assertEqual(restored.operation["action"], "resume")
            self.assertEqual(restored.operation["status"], "succeeded")

    def test_failure_pause_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), "one")
            actor.client = Mock()
            actor.observe = Mock(return_value=AppStatus(True, "SUSPENDED", 0, "x"))
            actor.client.return_value.start_app.side_effect = RuntimeError("quota exceeded")
            actor.execute_operation(
                {
                    "id": "failed",
                    "action": "start",
                    "source": "manual",
                    "steps": [],
                    "created_at": "2026-09-22T10:00:00+00:00",
                }
            )
            self.assertIn("failed_operation_at", actor.state)
            recovered = InstanceActor(FakeService(folder), "one")
            self.assertEqual(
                recovered.state["failed_operation_at"], actor.state["failed_operation_at"]
            )
            self.assertEqual(recovered.operation["status"], "failed")

    def test_two_instances_execute_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            first, second = InstanceActor(service, "first"), InstanceActor(service, "second")
            entered, release = threading.Event(), threading.Event()

            def block():
                entered.set()
                release.wait(2)
                return AppStatus(True, "RUNNING", 1, "first")

            first.observe = block
            first.client = Mock()
            second.client = Mock()
            second.observe = Mock(return_value=AppStatus(True, "RUNNING", 1, "second"))
            first.submit("start")
            self.assertTrue(entered.wait(1))
            second.submit("start")
            deadline = time.time() + 1
            while (
                second.operation["status"] not in ("failed", "succeeded") and time.time() < deadline
            ):
                time.sleep(0.01)
            self.assertEqual(second.operation["status"], "succeeded")
            self.assertEqual(first.operation["status"], "running")
            release.set()
            deadline = time.time() + 1
            while first.operation["status"] == "running" and time.time() < deadline:
                time.sleep(0.01)
            self.assertNotEqual(first.oplog, second.oplog)

    def test_start_does_not_query_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), "one")
            client = Mock()
            actor.client = Mock(return_value=client)
            actor.observe = Mock(return_value=AppStatus(True, "SUSPENDED", uid="x"))
            actor.wait = Mock(return_value=AppStatus(True, "RUNNING", 1, "x"))
            op = {"id": "test", "action": "start", "source": "manual", "steps": []}
            actor.execute_operation(op)
            client.start_app.assert_called_once()
            self.assertEqual(actor.wait.call_args.args[1], 60 * 60)
            client.get_free_gpus.assert_not_called()
            self.assertEqual(op["status"], "succeeded")

    def test_start_uses_instance_ready_timeout(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            service.item["start_timeout_minutes"] = 90
            actor = InstanceActor(service, "one")
            actor.client = Mock()
            actor.observe = Mock(return_value=AppStatus(True, "SUSPENDED", 0, "x"))
            actor.wait = Mock(return_value=AppStatus(True, "RUNNING", 1, "x"))
            op = {"id": "ready-timeout", "action": "start", "source": "manual", "steps": []}
            actor.execute_operation(op)
            self.assertEqual(actor.wait.call_args.args[1], 90 * 60)
            self.assertTrue(any("最长90分钟" in step["message"] for step in op["steps"]))

    def test_duplicate_operation_rejected_per_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            actor = InstanceActor(service, "one")
            actor.operation = {"status": "running"}
            with self.assertRaises(ValueError):
                actor.submit("restart")
            other = InstanceActor(service, "two")
            self.assertIsNone(other.operation)

    def test_rollover_waits_exactly_five_seconds_without_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            service.stop_event = Mock()
            service.stop_event.wait.return_value = False
            actor = InstanceActor(service, "one")
            client = Mock()
            actor.client = Mock(return_value=client)
            actor.observe = Mock(return_value=AppStatus(True, "RUNNING", 1, "x"))
            actor.wait = Mock(
                side_effect=[
                    AppStatus(True, "SUSPENDED", 0, "x"),
                    AppStatus(True, "RUNNING", 1, "x"),
                ]
            )
            op = {"id": "roll", "action": "restart", "source": "rollover", "steps": []}
            actor.execute_operation(op)
            service.stop_event.wait.assert_called_once_with(5)
            client.stop_app.assert_called_once()
            client.start_app.assert_called_once()
            client.get_free_gpus.assert_not_called()
            self.assertEqual(op["status"], "succeeded")

    def test_ready_transition_uses_platform_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), "one")
            client = Mock()
            client.base = {"sco": {}}
            actor.client = Mock(return_value=client)
            client.get_app.return_value = AppStatus(True, "PROGRESSING", 0, "x")
            actor.observe()
            client.get_app.return_value = AppStatus(True, "RUNNING", 1, "x")
            with (
                patch(
                    "controller.instance.get_instances",
                    return_value=[
                        {
                            "uid": "pod",
                            "container_infos": [
                                {
                                    "container_type": "MAIN",
                                    "container_state": "RUNNING",
                                    "life_span": 120,
                                }
                            ],
                        }
                    ],
                ),
                patch("controller.instance.time.time", return_value=1000),
            ):
                actor.observe()
            self.assertEqual(actor.state["run_started_at"], 880)

    def test_unknown_running_origin_not_fabricated(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), "one")
            client = Mock()
            actor.client = Mock(return_value=client)
            client.get_app.return_value = AppStatus(True, "RUNNING", 1, "x")
            actor.observe()
            self.assertIsNone(actor.state["run_started_at"])
