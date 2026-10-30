import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sco_client import SCO_COMMAND_LOCK, KeeperError, Runner, parse_reserved_idle_gpus

USAGE_OUTPUT = """
+-----------------+----------------+---------------+---------------+------------+-----------+-----------+
|  RESOURCE NAME  | RESERVED TOTAL | RESERVED USED | RESERVED IDLE | SPOT TOTAL | SPOT USED | SPOT IDLE |
+-----------------+----------------+---------------+---------------+------------+-----------+-----------+
|   GPU_NUMBER    |       32       |      30       |       2       |     0      |     0     |     0     |
+-----------------+----------------+---------------+---------------+------------+-----------+-----------+
"""


class CapacityParsingTests(unittest.TestCase):
    def test_parse_reserved_idle_gpus(self):
        self.assertEqual(parse_reserved_idle_gpus(USAGE_OUTPUT), 2)

    def test_missing_gpu_row_fails_closed(self):
        with self.assertRaises(KeeperError):
            parse_reserved_idle_gpus("no capacity here")


class RunnerConcurrencyTests(unittest.TestCase):
    def test_log_snapshot_returns_partial_utf8_and_truncates(self):
        result = Runner(True, 0.5).capture(
            [sys.executable, "-u", "-c", "import time; print('ready', flush=True); time.sleep(30)"],
            max_bytes=100,
        )
        self.assertTrue(result["partial"])
        self.assertIn("ready", result["text"])
        result = Runner(True, 3).capture([sys.executable, "-c", "print('a'*1000)"], max_bytes=20)
        self.assertTrue(result["partial"])
        self.assertLessEqual(len(result["text"]), 20)

    def test_logs_never_queue_behind_lifecycle_command(self):
        with SCO_COMMAND_LOCK, patch("sco_client.subprocess.Popen") as spawn:
            with self.assertRaises(KeeperError):
                Runner(True, 3).capture(["unused"])
            spawn.assert_not_called()

    def test_timed_out_log_reader_kills_descendants(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "child-survived"
            child = f"import time,pathlib; time.sleep(2); pathlib.Path({str(marker)!r}).touch(); time.sleep(30)"
            parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); print('spawned',flush=True); time.sleep(30)"
            result = Runner(True, 0.7).capture([sys.executable, "-u", "-c", parent])
            self.assertTrue(result["partial"])
            self.assertIn("spawned", result["text"])
            time.sleep(2.1)
            self.assertFalse(marker.exists())

    def test_sco_commands_are_serialized_across_runner_instances(self):
        active = 0
        maximum_active = 0
        counter_lock = threading.Lock()
        start = threading.Barrier(3)

        def fake_run(*args, **kwargs):
            nonlocal active, maximum_active
            with counter_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.05)
            with counter_lock:
                active -= 1
            return Mock(returncode=0, stdout="ok")

        def worker():
            start.wait()
            Runner(True, 1).run(["sco", "version"])

        with patch("sco_client.subprocess.run", side_effect=fake_run):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join(timeout=2)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(maximum_active, 1)


if __name__ == "__main__":
    unittest.main()
