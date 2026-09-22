import copy
import tempfile
import threading
import unittest
import time
from pathlib import Path
from unittest.mock import Mock, patch
from controller.instance import InstanceActor
from sco_client import AppStatus


class FakeService:
    def __init__(self, root):
        self.root = Path(root)
        self.stop_event = threading.Event()
        self.execute = True
        self.command_slots = threading.BoundedSemaphore(3)
        self.settings = {'timezone': 'Asia/Shanghai', 'status_interval_seconds': 60}
        self.item = {'schedule': {'start': {'enabled': False, 'time': '09:00'}, 'stop': {'enabled': False, 'time': '00:00'}},
                     'rollover': {'enabled': True, 'period_minutes': 8}}

    def instance_config(self, key):
        return copy.deepcopy(self.item)


class ActorTests(unittest.TestCase):
    def test_start_wait_timeout_is_twenty_minutes(self):
        with tempfile.TemporaryDirectory() as folder:
            service=FakeService(folder)
            service.stop_event=Mock()
            service.stop_event.is_set.return_value=False
            service.stop_event.wait.return_value=False
            actor=InstanceActor(service,'one')
            actor.observe=Mock(return_value=AppStatus(True,'PROGRESSING',0,'x'))
            with patch('controller.instance.time.monotonic',side_effect=[0,601,1199,1201]):
                with self.assertRaisesRegex(RuntimeError,'PROGRESSING'):
                    actor.wait(lambda app: app.state=='RUNNING')
            self.assertEqual(service.stop_event.wait.call_count,2)

    def test_resume_keeps_overdue_runtime_and_clears_failure_persistently(self):
        from controller.policy import decision
        from datetime import datetime
        with tempfile.TemporaryDirectory() as folder:
            service=FakeService(folder)
            actor=InstanceActor(service,'one')
            began=time.time()-3600
            actor.state.update(run_started_at=began,runtime_fresh=True,
                               failed_operation_at=time.time(),failed_operation_action='restart',manual_pause_at=time.time())
            actor.observe=Mock(return_value=AppStatus(True,'RUNNING',1,'x'))
            actor.client=Mock()
            actor.execute_operation({'id':'resume-test','action':'resume','source':'manual','steps':[],
                                     'created_at':'2026-09-22T10:00:00+00:00'})
            self.assertEqual(actor.state['run_started_at'],began)
            self.assertNotIn('failed_operation_at',actor.state)
            self.assertNotIn('manual_pause_at',actor.state)
            self.assertEqual(decision(actor.config(),actor.state,'RUNNING',True,datetime.now())[0],'restart')
            actor.client.assert_not_called()
            restored=InstanceActor(service,'one')
            self.assertNotIn('failed_operation_at',restored.state)
            self.assertEqual(restored.operation['action'],'resume')
            self.assertEqual(restored.operation['status'],'succeeded')
    def test_failure_pause_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            actor=InstanceActor(FakeService(folder),'one')
            actor.client=Mock()
            actor.observe=Mock(return_value=AppStatus(True,'SUSPENDED',0,'x'))
            actor.client.return_value.start_app.side_effect=RuntimeError('quota exceeded')
            actor.execute_operation({'id':'failed','action':'start','source':'manual','steps':[],
                                     'created_at':'2026-09-22T10:00:00+00:00'})
            self.assertIn('failed_operation_at',actor.state)
            recovered=InstanceActor(FakeService(folder),'one')
            self.assertEqual(recovered.state['failed_operation_at'],actor.state['failed_operation_at'])
            self.assertEqual(recovered.operation['status'],'failed')

    def test_two_instances_execute_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            first, second = InstanceActor(service, 'first'), InstanceActor(service, 'second')
            entered, release = threading.Event(), threading.Event()
            def block():
                entered.set()
                release.wait(2)
                return AppStatus(True, 'RUNNING', 1, 'first')
            first.observe = block
            first.client = Mock()
            second.client = Mock()
            second.observe = Mock(return_value=AppStatus(True, 'RUNNING', 1, 'second'))
            first.submit('start')
            self.assertTrue(entered.wait(1))
            second.submit('start')
            deadline = time.time()+1
            while second.operation['status'] not in ('failed','succeeded') and time.time()<deadline:
                time.sleep(.01)
            self.assertEqual(second.operation['status'],'succeeded')
            self.assertEqual(first.operation['status'],'running')
            release.set()
            deadline = time.time()+1
            while first.operation['status']=='running' and time.time()<deadline:
                time.sleep(.01)
            self.assertNotEqual(first.oplog,second.oplog)

    def test_start_does_not_query_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), 'one')
            client = Mock()
            actor.client = Mock(return_value=client)
            actor.observe = Mock(return_value=AppStatus(True, 'SUSPENDED', uid='x'))
            actor.wait = Mock(return_value=AppStatus(True, 'RUNNING', 1, 'x'))
            op = {'id': 'test', 'action': 'start', 'source': 'manual', 'steps': []}
            actor.execute_operation(op)
            client.start_app.assert_called_once()
            client.get_free_gpus.assert_not_called()
            self.assertEqual(op['status'], 'succeeded')

    def test_duplicate_operation_rejected_per_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            actor = InstanceActor(service, 'one')
            actor.operation = {'status': 'running'}
            with self.assertRaises(ValueError):
                actor.submit('restart')
            other = InstanceActor(service, 'two')
            self.assertIsNone(other.operation)

    def test_rollover_waits_exactly_five_seconds_without_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            service = FakeService(folder)
            service.stop_event = Mock()
            service.stop_event.wait.return_value = False
            actor = InstanceActor(service, 'one')
            client = Mock()
            actor.client = Mock(return_value=client)
            actor.observe = Mock(return_value=AppStatus(True, 'RUNNING', 1, 'x'))
            actor.wait = Mock(side_effect=[AppStatus(True,'SUSPENDED',0,'x'), AppStatus(True,'RUNNING',1,'x')])
            op={'id':'roll','action':'restart','source':'rollover','steps':[]}
            actor.execute_operation(op)
            service.stop_event.wait.assert_called_once_with(5)
            client.stop_app.assert_called_once()
            client.start_app.assert_called_once()
            client.get_free_gpus.assert_not_called()
            self.assertEqual(op['status'],'succeeded')

    def test_ready_transition_uses_platform_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), 'one')
            client = Mock()
            client.base = {'sco': {}}
            actor.client = Mock(return_value=client)
            client.get_app.return_value = AppStatus(True, 'PROGRESSING', 0, 'x')
            actor.observe()
            client.get_app.return_value = AppStatus(True, 'RUNNING', 1, 'x')
            with patch('controller.instance.get_instances', return_value=[{'uid':'pod', 'container_infos':[
                {'container_type':'MAIN','container_state':'RUNNING','life_span':120}]}]), patch('controller.instance.time.time', return_value=1000):
                actor.observe()
            self.assertEqual(actor.state['run_started_at'], 880)

    def test_unknown_running_origin_not_fabricated(self):
        with tempfile.TemporaryDirectory() as folder:
            actor = InstanceActor(FakeService(folder), 'one')
            client = Mock()
            actor.client = Mock(return_value=client)
            client.get_app.return_value = AppStatus(True, 'RUNNING', 1, 'x')
            actor.observe()
            self.assertIsNone(actor.state['run_started_at'])
