"""Same-origin local HTTP API, serving the bundled UI."""
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from .storage import journal


class Handler(BaseHTTPRequestHandler):
    service = None

    def log_message(self, *args):
        pass

    def reply(self, data, status=200, mime='application/json; charset=utf-8'):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path)
        parts = route.path.strip('/').split('/')
        try:
            if route.path == '/':
                return self.reply((self.service.root / 'templates/console.html').read_bytes(), mime='text/html; charset=utf-8')
            assets = {'/static/console.js': 'text/javascript', '/static/console.css': 'text/css'}
            if route.path in assets:
                return self.reply((self.service.root / route.path.lstrip('/')).read_bytes(), mime=assets[route.path])
            if route.path == '/api/status':
                return self.reply(self.service.snapshot())
            if len(parts) == 4 and parts[:2] == ['api', 'instances']:
                actor = self.service.actors[parts[2]]
                if parts[3] == 'operations':
                    return self.reply(actor.operations())
                if parts[3] == 'events':
                    return self.reply(list(reversed(journal(actor.logs))))
            if len(parts) == 4 and parts[:2] == ['api', 'gpu'] and parts[3] == 'metrics':
                return self.reply(self.service.metrics(parts[2], int(parse_qs(route.query).get('hours', ['8'])[0])))
            self.reply({'error': '接口不存在'}, 404)
        except KeyError:
            self.reply({'error': '实例或集群不存在'}, 404)
        except Exception as exc:
            self.reply({'error': str(exc)}, 400)

    def do_POST(self):
        try:
            if self.headers.get('Origin') not in (None, 'http://' + self.headers.get('Host', '')):
                return self.reply({'error': '不允许跨站操作'}, 403)
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                return self.reply({'error': '请求需要JSON格式'}, 415)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 65536:
                raise ValueError('请求过大')
            value = json.loads(self.rfile.read(size) or '{}')
            if not isinstance(value, dict):
                raise ValueError('请求必须是对象')
            parts = urlparse(self.path).path.strip('/').split('/')
            if parts == ['api', 'instances']:
                return self.reply(self.service.add_instance(value), 201)
            if parts == ['api', 'instances', 'sampling']:
                return self.reply(self.service.update_sampling(value))
            if parts == ['api', 'gpu', 'config']:
                return self.reply(self.service.update_gpu(value))
            if len(parts) >= 4 and parts[:2] == ['api', 'instances']:
                key = parts[2]
                if len(parts) == 4 and parts[3] == 'config':
                    return self.reply(self.service.update_instance(key, value))
                if len(parts) == 5 and parts[3] == 'actions':
                    return self.reply(self.service.actors[key].submit(parts[4]), 202)
            self.reply({'error': '接口不存在'}, 404)
        except KeyError:
            self.reply({'error': '实例不存在或缺少配置字段'}, 404)
        except Exception as exc:
            self.reply({'error': str(exc)}, 400)
