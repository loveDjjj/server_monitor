"""One actor per CCI; no GPU monitor dependency."""
import copy
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from sco_client import SCOClient, StateStore
from .policy import decision, latest_boundary
from .storage import append, journal, save_json, read_json, stamp
from .runtime_api import get_instances, running_clock


class InstanceActor:
    def __init__(self, service, key):
        self.service, self.key = service, key
        self.lock = threading.RLock()
        self.stop_event = service.stop_event
        self.directory = service.root / 'runtime/instances' / key
        self.logdir = service.root / 'logs/instances' / key
        self.state = read_json(self.directory / 'state.json', {})
        self.state.setdefault('schedule_seen_at', time.time())
        self.observed = {'state': 'UNKNOWN', 'ready': 0, 'sampled_at': None}
        self.operation = None
        self.error = ''
        self.reason = '等待首次采样'
        self.next_poll = 0
        self.logs = self.logdir / 'events.jsonl'
        self.oplog = self.logdir / 'operations.jsonl'
        for op in self.operations():
            if op['status'] in ('queued', 'running'):
                append(self.oplog, {**op, 'status': 'interrupted', 'message': '服务重启，执行结果需重新查询', 'finished_at': stamp()})
        records = self.operations()
        if records:
            self.operation = records[0]
            if self.operation['status'] in ('failed', 'interrupted') and not self.state.get('failed_operation_at'):
                self.state['failed_operation_at'] = datetime.fromisoformat(
                    self.operation.get('finished_at', self.operation['created_at'])).timestamp()
                self.state['failed_operation_action'] = self.operation['action']
                self.persist()

    def config(self):
        return self.service.instance_config(self.key)

    def persist(self):
        with self.lock:
            save_json(self.directory / 'state.json', self.state)

    def client(self, image=None):
        item = self.config()
        base = copy.deepcopy(self.service.defaults)
        base['sco']['workspace'] = item['workspace']
        base['container']['image'] = image or item['image']
        base['dnat'] = copy.deepcopy(item.get('dnat', {'enabled': False, 'external_port_candidates': [22]}))
        res = item['resources']
        task = {'application': {'name': item['name'], 'display_name': item['display_name'], 'cluster': item['cluster'],
                    'resource_spec': res['spec'], 'gpu_count': res['gpu_count'], 'cpu': res['cpu'], 'memory': res['memory']},
                'controller': {'command_timeout_seconds': 45}}
        store = StateStore(self.directory / 'client-state.json')
        store.load()
        return SCOClient(base, task, store, self.directory / 'rendered.yaml', dry_run_override=not self.service.execute)

    def event(self, message, **extra):
        append(self.logs, {'timestamp': stamp(), 'message': message, **extra})

    def operations(self):
        records = {}
        for item in journal(self.oplog, 2000):
            records[item['id']] = item
        return list(reversed(list(records.values())))[:40]

    def observe(self):
        client = self.client()
        app = client.get_app()
        clock, runtime_error = None, None
        sampled = time.time()
        if app.state == 'RUNNING':
            try:
                clock = running_clock(get_instances(client.base['sco'], app.raw))
                sampled = time.time()
                if clock is None:
                    runtime_error = '平台未返回运行中主容器的运行时间'
            except Exception as exc:
                runtime_error = str(exc)
        with self.lock:
            self.observed = {'state': app.state, 'ready': app.ready_replicas, 'uid': app.uid, 'sampled_at': stamp(),
                             'runtime_error': runtime_error}
            if self.state.get('uid') != app.uid or not self.state.get('runtime_source'):
                self.state['run_started_at'] = None
            self.state['runtime_fresh'] = clock is not None
            if clock is not None:
                began, source = sampled - clock['seconds'], 'life_span'
                if clock.get('last_started_time'):
                    try:
                        parsed = datetime.fromisoformat(clock['last_started_time'].replace('Z', '+00:00'))
                        if parsed.tzinfo and 0 < parsed.timestamp() <= sampled:
                            began, source = parsed.timestamp(), 'last_started_time'
                    except (ValueError, TypeError):
                        pass
                self.state.update(run_started_at=began, runtime_source=source, runtime_sampled_at=sampled,
                                  container_clock=clock, expecting_start=False)
            elif app.state in ('SUSPENDED', 'STOPPED'):
                self.state['run_started_at'] = None
            self.state['uid'] = app.uid
            self.persist()
            self.error = ''
        return app

    def snapshot(self):
        with self.lock:
            return {'key': self.key, 'config': self.config(), 'observed': copy.deepcopy(self.observed),
                    'run_started_at': self.state.get('run_started_at'), 'reason': self.reason, 'error': self.error,
                    'runtime_fresh': self.state.get('runtime_fresh', False),
                    'runtime_source': self.state.get('runtime_source'),
                    'runtime_sampled_at': self.state.get('runtime_sampled_at'),
                    'operation': copy.deepcopy(self.operation), 'pending_image': self.state.get('pending_image'),
                    'manual_paused': bool(self.state.get('manual_pause_at'))}

    def submit(self, action, source='manual', reason='人工操作'):
        if action not in ('start', 'stop', 'restart', 'resume'):
            raise ValueError('未知操作')
        with self.lock:
            if self.operation and self.operation['status'] in ('queued', 'running'):
                raise ValueError('该实例已有操作正在执行')
            op = {'id': uuid.uuid4().hex[:12], 'action': action, 'source': source, 'status': 'queued',
                  'message': reason, 'created_at': stamp(), 'steps': []}
            self.operation = op
            append(self.oplog, copy.deepcopy(op))
            thread = threading.Thread(target=self.execute_operation, args=(op,), daemon=True)
            thread.start()
            return copy.deepcopy(op)

    def stage(self, op, message, **extra):
        with self.lock:
            op.pop('resume_at', None)
            op.update(status='running', message=message, **extra)
            op['steps'].append({'time': stamp(), 'message': message})
            append(self.oplog, copy.deepcopy(op))

    def wait(self, predicate, timeout=1200):
        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set():
            app = self.observe()
            if predicate(app):
                return app
            if app.state in ('FAILED', 'ERROR'):
                raise RuntimeError('平台报告实例失败')
            if time.monotonic() >= deadline:
                raise RuntimeError('等待超时，当前平台状态：' + app.state)
            self.stop_event.wait(5)
        raise RuntimeError('服务正在退出')

    def execute_operation(self, op):
        try:
            with self.service.command_slots:
                self.stage(op, '查询实例状态')
                app = self.observe()
                if not app.exists and not self.state.get('replacement_in_progress'):
                    raise RuntimeError('受管实例不存在，不自动创建')
                action = op['action']
                if action == 'resume':
                    with self.lock:
                        self.state.pop('failed_operation_at', None)
                        self.state.pop('failed_operation_action', None)
                        self.state.pop('manual_pause_at', None)
                        self.error = ''
                        self.persist()
                        self.stage(op, '已恢复自动调度；保留本轮运行起点，超期周期将在下一轮执行')
                        op.update(status='succeeded', finished_at=stamp(), message='自动调度已恢复')
                    return
                if not self.service.execute:
                    self.stage(op, '演练模式：未执行云端变更')
                else:
                    client = self.client()
                    if app.state not in ('RUNNING', 'SUSPENDED', 'STOPPED', 'MISSING'):
                        raise RuntimeError('实例处于过渡状态，请等待当前操作完成')
                    if action in ('stop', 'restart') and app.state not in ('SUSPENDED', 'STOPPED'):
                        self.stage(op, '提交停止命令，等待平台停止')
                        client.stop_app(op['source'], 0)
                        app = self.wait(lambda a: a.state in ('SUSPENDED', 'STOPPED'), 300)
                    if action == 'stop':
                        if op['source'] == 'manual':
                            self.state['manual_pause_at'] = time.time()
                        self.stage(op, '已确认停止，DNAT保留')
                    else:
                        if action == 'restart':
                            self.stage(op, '已停止，等待5秒', resume_at=time.time()+5)
                            if self.stop_event.wait(5):
                                raise RuntimeError('服务正在退出')
                        config = self.config()
                        boundary = latest_boundary(datetime.now(ZoneInfo(self.service.settings['timezone'])), config['schedule'])
                        if op['source'] != 'manual' and config['schedule']['stop']['enabled'] and boundary and boundary[1] == 'stop':
                            self.stage(op, '进入定时停止时段，取消本次续杯启动')
                        elif app.state != 'RUNNING' or not app.ready_replicas:
                            pending = self.state.get('pending_image')
                            if app.exists and app.state not in ('SUSPENDED', 'STOPPED'):
                                self.stage(op, '实例正在变化，等待平台稳定')
                                app = self.wait(lambda a: a.state in ('SUSPENDED', 'STOPPED') or (a.state == 'RUNNING' and a.ready_replicas))
                            if pending:
                                self.stage(op, '应用新镜像：替换实例并保留配置备份')
                                save_json(self.directory / ('image-backup-' + op['id'] + '.json'), config)
                                if app.state == 'RUNNING':
                                    client.stop_app('image-update', 0)
                                    self.wait(lambda a: a.state in ('SUSPENDED', 'STOPPED'), 300)
                                self.state['replacement_in_progress'] = pending
                                self.persist()
                                if app.exists:
                                    client.delete_app()
                                    self.wait(lambda a: not a.exists, 300)
                                self.client(pending).create_app()
                            else:
                                self.stage(op, '提交启动命令，不检查GPU余量')
                                client.start_app()
                            self.state['expecting_start'] = True
                            self.persist()
                            self.stage(op, '等待实例 Running / Ready（最长20分钟）')
                            app = self.wait(lambda a: a.state == 'RUNNING' and a.ready_replicas >= 1)
                            if pending:
                                self.service.commit_image(self.key, pending)
                                if self.state.get('pending_image') == pending:
                                    self.state.pop('pending_image', None)
                                self.state.pop('replacement_in_progress', None)
                            self.stage(op, '实例已就绪')
                            self.state.pop('manual_pause_at', None)
                            if pending and self.config().get('dnat', {}).get('enabled'):
                                self.stage(op, '恢复新实例DNAT绑定')
                                deadline = time.monotonic() + 180
                                while time.monotonic() < deadline:
                                    current = self.client()
                                    current.ensure_dnat(app)
                                    rule = current.managed_rule(current.get_dnat_rules())
                                    if rule and rule.get('state') == 'ACTIVE' and rule.get('properties', {}).get('internal_instance_name') == app.uid:
                                        break
                                    if self.stop_event.wait(5):
                                        raise RuntimeError('服务正在退出')
                                else:
                                    raise RuntimeError('实例已就绪，但DNAT恢复超时')
                        else:
                            self.stage(op, '实例已经运行，无需重复启动')
                            self.state.pop('manual_pause_at', None)
                    self.state['schedule_seen_at'] = time.time()
                    self.persist()
            with self.lock:
                self.state.pop('failed_operation_at', None)
                self.state.pop('failed_operation_action', None)
                self.persist()
                op.update(status='succeeded', finished_at=stamp(), message='操作完成')
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                op.update(status='failed', finished_at=stamp(), message=str(exc))
                self.state['failed_operation_at'] = time.time()
                self.state['failed_operation_action'] = op['action']
                self.persist()
                op['message'] += '\n已暂停自动重试，可手动重试或等待下一次定时任务。'
        finally:
            append(self.oplog, copy.deepcopy(op))
            self.event(op['message'], operation_id=op['id'], action=op['action'], source=op['source'], status=op['status'])
            self.next_poll = 0

    def loop(self):
        while not self.stop_event.wait(1):
            try:
                with self.lock:
                    busy = self.operation and self.operation['status'] in ('queued', 'running')
                if busy:
                    continue
                if time.monotonic() >= self.next_poll:
                    self.observe()
                    self.next_poll = time.monotonic() + self.service.settings['status_interval_seconds']
                if self.error:
                    continue
                action, reason = decision(self.config(), self.state, self.observed['state'], self.observed['ready'],
                                          datetime.now(ZoneInfo(self.service.settings['timezone'])))
                self.reason = reason
                if action and self.service.execute:
                    self.submit(action, 'rollover' if reason == '周期续杯' else 'schedule', reason)
            except Exception as exc:
                self.error = str(exc)
                self.next_poll = time.monotonic() + self.service.settings['status_interval_seconds']
                self.event(str(exc), source='monitor', status='failed')
                self.stop_event.wait(5)
