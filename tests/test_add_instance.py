import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from controller.service import Service
from controller.config import read_yaml


class AddInstanceTests(unittest.TestCase):
    def make_service(self, folder):
        service = Service.__new__(Service)
        service.root = Path(folder)
        service.config_lock = threading.RLock()
        service.stop_event = threading.Event()
        service.actors = {}
        service.settings = {'timezone':'Asia/Shanghai', 'status_interval_seconds':60, 'instances':[]}
        service.defaults = {'sco':{'executable':'sco', 'profile':'default', 'subscription':'sub', 'resource_group':'default'}}
        return service

    def payload(self):
        return {'name':'test-app','uid':'uid-one','display_name':'测试实例','state':'RUNNING','ready_replicas':1,
                'resource_pool':{'name':'debug'},'template':{'resource_spec':{'name':'gpu-spec'},'containers':[
                    {'image_path':'registry/image:tag','resource_request':{'cpu':'14','memory':'60GiB','nvidia.com/gpu':'1'}}]}}

    def test_add_discovers_configuration_and_disables_automation(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.make_service(folder)
            with patch('controller.service.Runner.run',return_value=Mock(stdout=json.dumps(self.payload()))) as run, patch('threading.Thread.start') as start:
                result=service.add_instance({'workspace':'space','name':'test-app'})
                self.assertEqual(result['observed']['uid'],'uid-one')
                self.assertEqual(result['config']['resources']['gpu_count'],1)
                self.assertFalse(result['config']['schedule']['start']['enabled'])
                self.assertFalse(result['config']['schedule']['stop']['enabled'])
                self.assertFalse(result['config']['rollover']['enabled'])
                self.assertFalse(result['config']['dnat']['enabled'])
                self.assertIn('describe', run.call_args.args[0])
                start.assert_called_once()
                with self.assertRaisesRegex(ValueError,'已经'):
                    service.add_instance({'workspace':'space','name':'test-app'})
                self.assertEqual(run.call_count,1)
                self.assertEqual(len(read_yaml(Path(folder)/'config/instances.yaml')['instances']),1)

    def test_cli_error_does_not_save_or_register(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.make_service(folder)
            with patch('controller.service.Runner.run',side_effect=RuntimeError('not found')):
                with self.assertRaises(RuntimeError):
                    service.add_instance({'workspace':'space','name':'missing'})
            self.assertEqual(service.actors,{})
            self.assertFalse((Path(folder)/'config/instances.yaml').exists())

    def test_accepts_camelcase_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.make_service(folder)
            payload=self.payload()
            payload['displayName']=payload.pop('display_name')
            payload['resourcePool']=payload.pop('resource_pool')
            with patch('controller.service.Runner.run',return_value=Mock(stdout=json.dumps(payload))), patch('threading.Thread.start'):
                result=service.add_instance({'workspace':'space','name':'test-app'})
            self.assertEqual(result['config']['display_name'],'测试实例')

    def test_rejects_command_option_as_name(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.make_service(folder)
            with self.assertRaises(ValueError):
                service.add_instance({'workspace':'space','name':'--help'})
