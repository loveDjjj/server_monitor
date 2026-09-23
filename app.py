#!/usr/bin/env python3
"""Single entrypoint for the local multi-instance CCI console."""
import argparse
from pathlib import Path
from http.server import ThreadingHTTPServer
from controller.http import Handler
from controller.process_lock import acquire_process_lock
from controller.service import Service
from sco_client import configure_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    args.root.joinpath('runtime').mkdir(parents=True, exist_ok=True)
    try:
        lock = acquire_process_lock(args.root.joinpath('runtime/manager.lock'))
    except OSError as exc:
        raise SystemExit('同一数据目录已有管理进程在运行') from exc
    configure_logging()
    # Bind before starting collectors: a duplicate process must not mutate resources.
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    service = Service(args.root, execute=not args.dry_run)
    Handler.service = service
    service.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop_event.set()
        server.server_close()
        lock.close()


if __name__ == '__main__':
    main()
