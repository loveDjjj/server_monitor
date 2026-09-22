"""Read-only CCI runtime API. Credentials are used in memory, never logged."""
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
try:
    import tomllib
except ImportError:
    import tomli as tomllib


def get_instances(sco, app):
    resource_id = app['id']
    if not resource_id.startswith('/subscriptions/') or '/apps/' not in resource_id:
        raise ValueError('应用资源标识不完整')
    region = sco.get('region', 'cn-sh-01')
    host = f'cci.{region}.sensecore.cn'
    profile = sco.get('profile', 'default')
    if Path(profile).name != profile:
        raise ValueError('Profile名称无效')
    config_dir = Path(os.environ.get('SCO_CONFIG', str(Path.home()/'.config/sco')))
    with (config_dir/'profiles'/f'{profile}.toml').open('rb') as stream:
        credentials = tomllib.load(stream)
    key = credentials['access_key_id']
    secret = credentials['access_key_secret']
    items, seen = [], set()
    token = '1'
    while token and token != '0':
        if token in seen:
            raise ValueError('实例列表分页重复')
        seen.add(token)
        target = '/compute/cci/data/v2' + resource_id + '/instances?' + urlencode({'page_size':500,'page_token':token})
        date = format_datetime(datetime.now(timezone.utc), usegmt=True)
        canonical = f'date: {date}\nhost: {host}\n@request-target: get {target}'
        signature = base64.b64encode(hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).digest()).decode()
        authorization = f'hmac accesskey="{key}", algorithm="hmac-sha256", headers="date host @request-target", signature="{signature}"'
        req = Request('https://' + host + target, headers={'Date':date,'Authorization':authorization,'Accept':'application/json'})
        try:
            with urlopen(req, timeout=20) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f'容器运行时间接口返回 HTTP {exc.code}') from None
        items.extend(payload.get('instances', []))
        token = payload.get('next_page_token', '')
    return items


def running_clock(instances):
    """Oldest running MAIN container governs app-wide rollover; INIT is excluded."""
    clocks = []
    for instance in instances:
        for container in instance.get('container_infos', []):
            if container.get('container_type') != 'MAIN' or container.get('container_state') != 'RUNNING':
                continue
            span = container.get('life_span')
            if isinstance(span, bool) or not isinstance(span, (int, float)) or span < 0:
                continue
            clocks.append({'seconds':span, 'instance_uid':instance.get('uid') or instance.get('name'),
                           'container_name':container.get('container_name'), 'restart_count':container.get('restart_count',0),
                           'last_started_time':container.get('last_started_time')})
    return max(clocks, key=lambda x:x['seconds']) if clocks else None
