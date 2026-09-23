import threading
import time
import unittest
from unittest.mock import Mock, patch

from sco_client import KeeperError, Runner, parse_reserved_idle_gpus


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
