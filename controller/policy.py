"""Pure scheduling decisions; GPU availability is intentionally not an input."""
from datetime import datetime, timedelta


def latest_boundary(now, schedule):
    events = []
    for action in ('start', 'stop'):
        setting = schedule[action]
        if not setting['enabled']:
            continue
        hour, minute = map(int, setting['time'].split(':'))
        moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if moment > now:
            moment -= timedelta(days=1)
        events.append((moment.timestamp(), action))
    return max(events) if events else None


def decision(config, state, app_state, ready, now):
    """Return (action, reason). Runtime timestamps are UTC epoch seconds."""
    boundary = latest_boundary(now, config['schedule'])
    start = config['schedule']['start']['enabled']
    stop = config['schedule']['stop']['enabled']
    both = start and stop
    current = now.timestamp()
    if app_state not in {'RUNNING', 'SUSPENDED', 'STOPPED'}:
        return None, '等待平台状态稳定' if app_state != 'MISSING' else '实例不存在，请检查配置'
    # A new daily start releases a manual pause; an old boundary never does.
    paused = state.get('manual_pause_at', 0)
    pause_active = paused and not (boundary and boundary[1] == 'start' and boundary[0] > paused)
    if pause_active:
        return None, '人工停止，等待下一次定时启动或人工启动'
    schedule_effective = state.get('schedule_effective_at', 0)
    if boundary and boundary[0] < schedule_effective:
        boundary = None
    failed_at = state.get('failed_operation_at')
    retry_boundary = 'stop' if state.get('failed_operation_action') == 'stop' else 'start'
    failure_active = failed_at and not (boundary and boundary[1] == retry_boundary and boundary[0] > failed_at)
    if failure_active:
        # A failed start must not prevent the next scheduled stop, but must not loop.
        if retry_boundary == 'start' and both and boundary and boundary[1] == 'stop' and app_state == 'RUNNING':
            return 'stop', '定时停止时段'
        return None, '上次操作失败，自动重试已暂停；等待人工操作或下一次定时任务'
    if both and boundary and boundary[1] == 'stop':
        return ('stop', '定时停止时段') if app_state == 'RUNNING' else (None, '定时停止时段')
    if boundary and not both and boundary[0] > state.get('schedule_seen_at', current):
        if boundary[1] == 'stop' and app_state == 'RUNNING':
            return 'stop', '定时停止'
        if boundary[1] == 'start' and app_state in {'STOPPED', 'SUSPENDED'}:
            return 'start', '定时启动'
    if app_state == 'RUNNING':
        began = state.get('run_started_at')
        if ready and began and state.get('runtime_fresh', True) and config['rollover']['enabled'] and current - began >= config['rollover']['period_minutes'] * 60:
            return 'restart', '周期续杯'
        if config['rollover']['enabled'] and not state.get('runtime_fresh', True):
            return None, '等待平台运行时间恢复，周期续杯暂缓'
        return None, '运行中' if ready else '等待实例就绪'
    if both and boundary and boundary[1] == 'start':
        return 'start', '定时运行时段'
    if config['rollover']['enabled'] and not (stop and boundary and boundary[1] == 'stop'):
        return 'start', '周期持续运行'
    return None, '等待定时启动'
