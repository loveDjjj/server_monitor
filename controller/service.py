"""Multi-instance service with separate cluster monitoring threads."""
import copy
import logging
import threading
import time
import json
import re
import uuid
from pathlib import Path
from .config import read_yaml, write_yaml, validate
from .storage import append, journal, stamp, prune
from .instance import InstanceActor
from sco_client import Runner, parse_reserved_idle_gpus


class Service:
    def __init__(self, root, execute=True):
        self.root = Path(root)
        self.execute = execute
        self.settings = validate(read_yaml(self.root / 'config/instances.yaml'))
        self.defaults = read_yaml(self.root / 'config/defaults.yaml')
        self.gpu_config = read_yaml(self.root / 'config/gpu-monitor.yaml')
        self.stop_event = threading.Event()
        self.config_lock = threading.RLock()
        self.command_slots = threading.BoundedSemaphore(3)
        self.actors = {item['key']: InstanceActor(self, item['key']) for item in self.settings['instances']}
        self.gpu_latest = {}

    def instance_config(self, key):
        with self.config_lock:
            for item in self.settings['instances']:
                if item['key'] == key:
                    return copy.deepcopy(item)
        raise KeyError(key)

    def start(self):
        for actor in self.actors.values():
            threading.Thread(target=actor.loop, daemon=True).start()
        for cluster in self.gpu_config['clusters']:
            threading.Thread(target=self.gpu_loop, args=(cluster,), daemon=True).start()

    def snapshot(self):
        with self.config_lock:
            actors = list(self.actors.values())
        return {'execute': self.execute, 'timezone': self.settings['timezone'],
                'default_workspace': self.defaults['sco'].get('workspace', ''),
                'status_interval_seconds': self.settings['status_interval_seconds'],
                'instances': [a.snapshot() for a in actors],
                'gpu': {'config': self.gpu_config, 'latest': copy.deepcopy(self.gpu_latest)}}

    def add_instance(self, incoming):
        workspace, name = (incoming.get(field, '') for field in ('workspace', 'name'))
        for value in (workspace, name):
            if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', value):
                raise ValueError('请填写有效的Workspace和实例名称，不是显示名称或UID')
        def check_duplicate():
            if any(x['workspace'] == workspace and x['name'] == name for x in self.settings['instances']):
                raise ValueError('该实例已经在监控列表中')
        with self.config_lock:
            check_duplicate()
        sco = self.defaults['sco']
        result = Runner(True, 45).run([sco['executable'], '--profile', sco['profile'],
            '--subscription', sco['subscription'], '--resource-group', sco['resource_group'],
            'cci', 'apps', 'describe', name, '--workspace-name', workspace, '-o', 'json'])
        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError('平台没有返回有效实例信息，未添加') from exc
        def normalize(obj):
            if isinstance(obj, dict):
                return {k.replace('_', '').lower(): normalize(v) for k,v in obj.items()}
            if isinstance(obj, list):
                return [normalize(v) for v in obj]
            return obj
        obj = normalize(raw)
        template = obj.get('template', {})
        containers = template.get('containers', [])
        if not obj.get('uid') or obj.get('name') != name or not containers:
            raise ValueError('实例不存在或返回的实例信息不完整，未添加')
        container = containers[0]
        request = container.get('resourcerequest', {})
        item = {'key': 'cci-' + uuid.uuid4().hex[:12], 'name': name, 'workspace': workspace,
                'display_name': obj.get('displayname') or name,
                'cluster': obj.get('resourcepool', {}).get('name', ''),
                'resources': {'spec': template.get('resourcespec', {}).get('name', ''),
                              'gpu_count': int(request.get('nvidia.com/gpu', 0)),
                              'cpu': str(request.get('cpu', '0')), 'memory': request.get('memory', '')},
                'image': container.get('imagepath', ''),
                'schedule': {'start': {'enabled': False, 'time': '09:00'}, 'stop': {'enabled': False, 'time': '00:00'}},
                'rollover': {'enabled': False, 'period_minutes': 230}, 'dnat': {'enabled': False}}
        with self.config_lock:
            check_duplicate()  # Two simultaneous requests must not register twice.
            data = copy.deepcopy(self.settings)
            data['instances'].append(item)
            validate(data)
            write_yaml(self.root / 'config/instances.yaml', data)
            self.settings = data
            actor = InstanceActor(self, item['key'])
            actor.observed = {'state': obj.get('state', 'UNKNOWN'), 'ready': obj.get('readyreplicas', 0),
                              'uid': obj['uid'], 'sampled_at': stamp()}
            actor.state['uid'] = obj['uid']
            actor.persist()
            self.actors[item['key']] = actor
        actor.event('已添加已有实例监控，所有自动化默认关闭', source='config')
        threading.Thread(target=actor.loop, daemon=True).start()
        return actor.snapshot()

    def update_instance(self, key, incoming):
        actor = self.actors[key]
        allowed = {'display_name', 'image', 'schedule', 'rollover'}
        if set(incoming) - allowed:
            raise ValueError('只能修改名称、镜像、定时和周期设置')
        with actor.lock, self.config_lock:
            data = copy.deepcopy(self.settings)
            item = next(x for x in data['instances'] if x['key'] == key)
            proposed_image = incoming.get('image', actor.state.get('pending_image', item['image']))
            item.update(copy.deepcopy(incoming))
            validate(data)
            old = self.instance_config(key)
            item['image'] = old['image']
            if proposed_image != old['image']:
                actor.state['pending_image'] = proposed_image
            else:
                actor.state.pop('pending_image', None)
            if item['schedule'] != old['schedule']:
                actor.state['schedule_seen_at'] = time.time()
                actor.state['schedule_effective_at'] = time.time()
            actor.persist()
            write_yaml(self.root / 'config/instances.yaml', data)
            self.settings = data
            actor.event('配置已保存；镜像下次启动应用，调度参数立即生效', source='config')
            return actor.snapshot()

    def commit_image(self, key, image):
        with self.config_lock:
            data = copy.deepcopy(self.settings)
            next(x for x in data['instances'] if x['key'] == key)['image'] = image
            write_yaml(self.root / 'config/instances.yaml', data)
            self.settings = data

    def update_sampling(self, incoming):
        if set(incoming) != {'status_interval_seconds'}:
            raise ValueError('仅支持修改实例采样间隔')
        interval = incoming['status_interval_seconds']
        if type(interval) is not int or not 10 <= interval <= 3600:
            raise ValueError('实例采样间隔必须为10–3600秒的整数')
        with self.config_lock:
            value = copy.deepcopy(self.settings)
            value['status_interval_seconds'] = interval
            validate(value)
            write_yaml(self.root / 'config/instances.yaml', value)
            self.settings = value
            actors = list(self.actors.values())
        for actor in actors:
            actor.next_poll = 0
        return {'status_interval_seconds': interval}

    def update_gpu(self, incoming):
        if set(incoming) - {'enabled', 'interval_seconds', 'history_hours'}:
            raise ValueError('不支持的监控设置')
        with self.config_lock:
            value = {**self.gpu_config, **incoming}
            if type(value['enabled']) is not bool or not 10 <= value['interval_seconds'] <= 3600 or not 1 <= value['history_hours'] <= 168:
                raise ValueError('监控步长10–3600秒，保留时长1–168小时')
            write_yaml(self.root / 'config/gpu-monitor.yaml', value)
            self.gpu_config = value
            self.gpu_generation = getattr(self, 'gpu_generation', 0) + 1
            return value

    def gpu_loop(self, cluster):
        runner = Runner(True, 40)
        next_sample = 0
        next_prune = 0
        generation = getattr(self, 'gpu_generation', 0)
        while not self.stop_event.wait(1):
            if generation != getattr(self, 'gpu_generation', 0):
                generation = getattr(self, 'gpu_generation', 0)
                next_sample = 0
            if not self.gpu_config['enabled'] or time.monotonic() < next_sample:
                continue
            interval = self.gpu_config['interval_seconds']
            sample = {'epoch': time.time(), 'timestamp': stamp(), 'free_gpus': None, 'error': None}
            try:
                sco = self.defaults['sco']
                argv = [sco['executable'], '--profile', sco['profile'], '--subscription', sco['subscription'],
                        '--resource-group', sco['resource_group'], 'aec2', 'clusters', 'usage', '--name', cluster['name']]
                sample['free_gpus'] = parse_reserved_idle_gpus(runner.run(argv).stdout)
            except Exception as exc:
                sample['error'] = str(exc)
            self.gpu_latest[cluster['key']] = sample
            path = self.root / 'runtime/gpu' / cluster['key'] / 'metrics.jsonl'
            try:
                append(path, sample)
            except OSError as exc:
                logging.warning('GPU history append failed for %s: %s', cluster['key'], exc)
            if time.monotonic() >= next_prune:
                next_prune = time.monotonic() + 60
                try:
                    prune(path, time.time() - self.gpu_config['history_hours'] * 3600)
                except OSError as exc:
                    logging.warning('GPU history prune failed for %s: %s', cluster['key'], exc)
            next_sample = time.monotonic() + interval

    def metrics(self, key, hours=8):
        if key not in {c['key'] for c in self.gpu_config['clusters']}:
            raise KeyError(key)
        cutoff = time.time() - min(168, max(1, hours)) * 3600
        return [p for p in journal(self.root / 'runtime/gpu' / key / 'metrics.jsonl', 100000) if p.get('epoch', 0) >= cutoff]
