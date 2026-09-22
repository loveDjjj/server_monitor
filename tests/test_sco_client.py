import unittest

from sco_client import KeeperError, parse_reserved_idle_gpus


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


if __name__ == "__main__":
    unittest.main()
