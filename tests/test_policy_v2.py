import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from controller.policy import decision


def config():
    return {'schedule': {'start': {'enabled': True, 'time': '09:00'}, 'stop': {'enabled': True, 'time': '00:00'}}, 'rollover': {'enabled': True, 'period_minutes': 230}}


class InstancePolicyTests(unittest.TestCase):
    def test_failed_start_does_not_retry_each_poll(self):
        state={'failed_operation_at':self.now(10).timestamp(), 'failed_operation_action':'start'}
        for hour in (10,11,12,20,23):
            self.assertIsNone(decision(config(),state,'SUSPENDED',False,self.now(hour))[0])
        tomorrow=datetime(2026,9,23,9,0,tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertEqual(decision(config(),state,'SUSPENDED',False,tomorrow)[0],'start')

    def test_failed_rollover_without_schedule_stays_paused(self):
        value=config()
        value['schedule']['start']['enabled']=False
        value['schedule']['stop']['enabled']=False
        state={'failed_operation_at':self.now(10).timestamp(),'failed_operation_action':'restart',
               'run_started_at':self.now(1).timestamp()}
        self.assertIsNone(decision(value,state,'RUNNING',True,self.now(20))[0])

    def test_failed_stop_does_not_loop_in_stop_window(self):
        state={'failed_operation_at':self.now(0,1).timestamp(),'failed_operation_action':'stop'}
        self.assertIsNone(decision(config(),state,'RUNNING',True,self.now(2))[0])

    def now(self, hour, minute=0):
        return datetime(2026, 9, 22, hour, minute, tzinfo=ZoneInfo('Asia/Shanghai'))

    def test_midnight_overrides_overdue_rollover(self):
        now = self.now(0)
        state = {'run_started_at': now.timestamp() - 15000}
        self.assertEqual(decision(config(), state, 'RUNNING', True, now)[0], 'stop')
        self.assertIsNone(decision(config(), state, 'SUSPENDED', False, now)[0])

    def test_daytime_already_running_is_untouched(self):
        now = self.now(9)
        self.assertIsNone(decision(config(), {'run_started_at': now.timestamp()-60}, 'RUNNING', True, now)[0])

    def test_shortened_period_applies_to_current_run(self):
        value = config()
        value['rollover']['period_minutes'] = 5
        now = self.now(12)
        self.assertEqual(decision(value, {'run_started_at': now.timestamp()-600}, 'RUNNING', True, now)[0], 'restart')

    def test_manual_pause_expires_at_next_start_boundary(self):
        state = {'manual_pause_at': self.now(8).timestamp()}
        self.assertIsNone(decision(config(), state, 'SUSPENDED', False, self.now(8, 30))[0])
        self.assertEqual(decision(config(), state, 'SUSPENDED', False, self.now(9))[0], 'start')

    def test_all_disabled_means_monitor_only(self):
        value = config()
        value['schedule']['start']['enabled'] = False
        value['schedule']['stop']['enabled'] = False
        value['rollover']['enabled'] = False
        self.assertIsNone(decision(value, {}, 'SUSPENDED', False, self.now(12))[0])

    def test_unscheduled_rollover_runs_all_day(self):
        value = config()
        value['schedule']['start']['enabled'] = False
        value['schedule']['stop']['enabled'] = False
        self.assertEqual(decision(value, {}, 'SUSPENDED', False, self.now(2))[0], 'start')

    def test_schedule_edit_does_not_backfill_past_stop(self):
        now = self.now(12)
        value=config()
        value['schedule']['stop']['time']='11:00'
        state={'schedule_effective_at':now.timestamp(), 'run_started_at':now.timestamp()-30}
        self.assertIsNone(decision(value,state,'RUNNING',True,now)[0])
