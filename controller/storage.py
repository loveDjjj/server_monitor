"""Small atomic JSON snapshots and append-only event journals."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone


def stamp():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def append(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + '\n')


def journal(path, limit=200):
    from collections import deque
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding='utf-8') as stream:
        lines = deque(stream, maxlen=limit)
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def prune(path, cutoff):
    """Called only by the journal's single writer; readers see an atomic replacement."""
    path = Path(path)
    records = journal(path, 100000)
    retained = [item for item in records if item.get('epoch', 0) >= cutoff]
    if len(retained) == len(records):
        return
    temporary = path.with_suffix('.tmp')
    temporary.write_text(''.join(json.dumps(item, ensure_ascii=False)+'\n' for item in retained), encoding='utf-8')
    os.replace(temporary, path)
