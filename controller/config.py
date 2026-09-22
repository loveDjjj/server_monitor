"""Validated multi-instance YAML configuration."""
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml

# 保存网页编辑结果时重新生成字段说明，避免普通YAML序列化吞掉注释。
COMMENTS = {
    'version': '配置格式版本。', 'sco': 'SCO访问上下文；使用本机已认证的Profile，不填写密钥正文。',
    'executable': 'SCO可执行文件；换机器时修改绝对路径或使用PATH中的sco。',
    'profile': '本机SCO配置名称，需要先执行sco init。', 'region': '区域代码，用于容器运行时间OpenAPI。',
    'workspace': '工作空间名称，与应用name共同定位已有CCI。', 'subscription': '订阅标识，按目标环境填写。',
    'resource_group': '资源组名称。', 'zone': '可用区代码。',
    'timezone': '定时任务时区。', 'status_interval_seconds': '实例状态采样间隔（秒）；与GPU监控独立。',
    'instances': '受管实例列表；可从网页添加，新增默认只监控。', 'key': '本地唯一标识，也是状态和日志目录名称。',
    'name': '平台资源名称，不是显示名称。', 'display_name': '管理台显示名称。',
    'cluster': '计算集群名称。', 'resources': '实例资源信息；必须与平台规格对应。',
    'spec': '平台资源规格名称。', 'gpu_count': 'GPU卡数，CPU实例可为0。', 'cpu': 'vCPU数量。', 'memory': '内存容量，带单位，例如128GiB。',
    'image': '完整镜像地址；实例编辑保存后下一次启动应用。',
    'schedule': '每日定时；停止时段优先于周期续杯。', 'start': '每日启动规则。', 'stop': '每日停止规则。',
    'enabled': '是否启用当前功能。', 'time': '每日执行时间，24小时HH:MM格式。',
    'rollover': '周期续杯：到期停止，确认停止后等待5秒直接启动，不检查GPU余量。',
    'period_minutes': '续杯周期（1–230分钟）；修改后按本轮平台启动时间立即重新判断。',
    'container': '共用容器配置，仅在镜像变更替换实例时使用。', 'command': '启动命令参数列表；原部署保留sleep 4h。',
    'storage': '默认挂载配置：AFS存储卷或Secret引用，不填写Secret正文。',
    'type': '资源类型，例如PV_AFS或SECRET。', 'id': '平台资源ID。', 'mount_path': '容器内挂载路径。',
    'subdir': '卷内子目录，/表示根目录。', 'cci': '创建替换实例时使用的默认设置。', 'replicas': '期望实例副本数。',
    'priority': '调度优先级。', 'quota_type': '配额类型，默认RESERVED。', 'instance_affinity': '副本亲和策略。',
    'application_ports': '应用服务暴露端口。', 'dnat': '公网映射管理；普通启停保留绑定。',
    'eip_name': 'EIP资源名称。', 'external_ip': '公网IP。', 'external_port_candidates': '候选公网端口，按顺序选择未占用端口。',
    'internal_port': '目标容器服务端口。', 'protocol': '转发协议。', 'rule_prefix': '本实例管理的DNAT规则前缀，应独立设置。',
    'reusable_rule_names': '明确允许复用的规则白名单，避免影响其他实例。',
    'internal_instance_type': 'CCI应用必须使用CCI_DEPLOYMENT_SERVICE。',
    'interval_seconds': 'GPU余量采样间隔（秒），不影响实例调度。', 'history_hours': 'GPU历史数据保留时长（小时）。',
    'clusters': '只读监控的集群列表。', 'total_gpus': '集群总卡数的展示配置，不参与实例启停。',
}


def annotated_yaml(data):
    lines = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).splitlines()
    result = []
    for line in lines:
        match = re.match(r'^(\s*)(?:- )?([a-z_]+):', line)
        if match and match[2] in COMMENTS:
            result.append(match[1] + '# ' + COMMENTS[match[2]])
        result.append(line)
    return '\n'.join(result) + '\n'


def read_yaml(path):
    value = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('配置必须是对象')
    return value


def write_yaml(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(annotated_yaml(value), encoding='utf-8')
    temporary.replace(path)


def validate(value):
    ZoneInfo(value['timezone'])
    if not 10 <= value['status_interval_seconds'] <= 3600:
        raise ValueError('实例采样步长必须在10–3600秒之间')
    keys, identities = set(), set()
    for item in value['instances']:
        key = item['key']
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', key) or key in keys:
            raise ValueError('实例key不合法或重复')
        identity = (item['workspace'], item['name'])
        if identity in identities:
            raise ValueError('同一应用不能被重复接管')
        keys.add(key)
        identities.add(identity)
        for action in ('start', 'stop'):
            setting = item['schedule'][action]
            if type(setting['enabled']) is not bool:
                raise ValueError('开关必须为布尔值')
            datetime.strptime(setting['time'], '%H:%M')
        schedule = item['schedule']
        if schedule['start']['enabled'] and schedule['stop']['enabled'] and schedule['start']['time'] == schedule['stop']['time']:
            raise ValueError('启动和停止时间不能相同')
        rollover = item['rollover']
        if type(rollover['enabled']) is not bool or not 1 <= rollover['period_minutes'] <= 230:
            raise ValueError('续杯周期必须在1–230分钟之间')
        if not isinstance(item['image'], str) or not item['image'].strip():
            raise ValueError('镜像不能为空')
        if not isinstance(item['display_name'], str) or not item['display_name'].strip():
            raise ValueError('显示名称不能为空')
    return value
