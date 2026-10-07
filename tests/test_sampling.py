import itertools
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from controller.service import Service
from controller.config import read_yaml


class SamplingTests(unittest.TestCase):
    def test_gpu_prune_failure_keeps_sampling(self):
        with tempfile.TemporaryDirectory() as folder:
            service = Service.__new__(Service)
            service.root = Path(folder)
            service.stop_event = Mock()
            service.stop_event.wait.side_effect = [False, False, True]
            service.gpu_config = {'enabled': True, 'interval_seconds': 10, 'history_hours': 24}
            service.defaults = {'sco': {'executable': 'sco', 'profile': 'default',
                                        'subscription': 'sub', 'resource_group': 'default'}}
            service.gpu_latest = {}
            with patch('controller.service.Runner.run', return_value=Mock(stdout='usage')) as run, \
                    patch('controller.service.parse_reserved_idle_gpus', return_value=2), \
                    patch('controller.service.prune', side_effect=PermissionError('file in use')) as prune, \
                    patch('controller.service.time.monotonic', side_effect=itertools.count(step=100).__next__), \
                    patch('controller.service.logging.warning') as warning:
                service.gpu_loop({'key': 'gpu', 'name': 'cluster'})
            self.assertEqual(run.call_count, 2)
            self.assertEqual(prune.call_count, 2)
            self.assertEqual(warning.call_count, 2)
            self.assertEqual(service.gpu_latest['gpu']['free_gpus'], 2)
            self.assertEqual(len((service.root / 'runtime/gpu/gpu/metrics.jsonl').read_text().splitlines()), 2)

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
