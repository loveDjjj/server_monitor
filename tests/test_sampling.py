import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from controller.service import Service
from controller.config import read_yaml


class SamplingTests(unittest.TestCase):
    def test_update_persists_and_reschedules(self):
        with tempfile.TemporaryDirectory() as folder:
            service=Service.__new__(Service)
            service.root=Path(folder)
            service.config_lock=threading.RLock()
            service.settings={'timezone':'Asia/Shanghai','status_interval_seconds':60,'instances':[]}
            actor=Mock(next_poll=123)
            service.actors={'one':actor}
            self.assertEqual(service.update_sampling({'status_interval_seconds':90}),{'status_interval_seconds':90})
            self.assertEqual(actor.next_poll,0)
            self.assertEqual(read_yaml(service.root/'config/instances.yaml')['status_interval_seconds'],90)
            for invalid in (0,9,3601,True,10.5,'60'):
                with self.assertRaises(ValueError):
                    service.update_sampling({'status_interval_seconds':invalid})
