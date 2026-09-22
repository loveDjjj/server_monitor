import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime
from controller.instance import InstanceActor
from controller.runtime_api import running_clock
from controller.policy import decision
from sco_client import AppStatus
from test_instances_v2 import FakeService


def rows(seconds, started=None, restarts=0):
    return [{'uid':'pod', 'container_infos':[{'container_type':'MAIN','container_state':'RUNNING',
        'life_span':seconds,'last_started_time':started,'restart_count':restarts}]}]


class PlatformClockTests(unittest.TestCase):
    def test_excludes_init_and_stopped_containers(self):
        value=rows(60)
        value[0]['container_infos'] += [{'container_type':'INIT','container_state':'RUNNING','life_span':1000},
                                      {'container_type':'MAIN','container_state':'TERMINATED','life_span':900}]
        self.assertEqual(running_clock(value)['seconds'],60)

    def test_running_container_zero_age_is_valid(self):
        self.assertEqual(running_clock(rows(0))['seconds'],0)

    def test_platform_start_and_restart_recalibrate_and_failure_pauses_rollover(self):
        with tempfile.TemporaryDirectory() as folder:
            actor=InstanceActor(FakeService(folder),'one')
            client=Mock(); client.base={'sco':{}}
            client.get_app.return_value=AppStatus(True,'RUNNING',1,'app',raw={})
            actor.client=Mock(return_value=client)
            with patch('controller.instance.get_instances', return_value=rows(500,'1970-01-01T00:10:00Z')), patch('controller.instance.time.time',return_value=1000):
                actor.observe()
            self.assertEqual(actor.state['run_started_at'],600)
            with patch('controller.instance.get_instances', return_value=rows(5,None,1)), patch('controller.instance.time.time',return_value=1000):
                actor.observe()
            self.assertEqual(actor.state['run_started_at'],995)
            with patch('controller.instance.get_instances',side_effect=RuntimeError('network unavailable')):
                actor.observe()
            self.assertEqual(actor.state['run_started_at'],995)
            self.assertFalse(actor.state['runtime_fresh'])
            action,_=decision(actor.config(),actor.state,'RUNNING',True,datetime.now())
            self.assertIsNone(action)
