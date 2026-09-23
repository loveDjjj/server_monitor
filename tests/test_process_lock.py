import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from controller.process_lock import acquire_process_lock


LOCK_SCRIPT = (
    'import sys\n'
    'from pathlib import Path\n'
    'from controller.process_lock import acquire_process_lock\n'
    'lock = acquire_process_lock(Path(sys.argv[1]))\n'
    'lock.close()\n'
)


class ProcessLockTests(unittest.TestCase):
    def test_lock_blocks_another_process_and_releases_on_close(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'manager.lock'
            lock = acquire_process_lock(path)
            blocked = subprocess.run(
                [sys.executable, '-c', LOCK_SCRIPT, str(path)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)

            lock.close()
            acquired = subprocess.run(
                [sys.executable, '-c', LOCK_SCRIPT, str(path)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(acquired.returncode, 0, acquired.stderr)


if __name__ == '__main__':
    unittest.main()
