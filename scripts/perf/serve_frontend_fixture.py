"""Production static bundles with identical local synthetic API responses."""
import argparse
import functools
import gzip
import json
import mimetypes
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--port', type=int, required=True)
p.add_argument('--fixtures', type=Path, default=Path(__file__).resolve().parents[2] / 'docs/performance/artifacts/r7-controlled-fixtures.json')
a = p.parse_args()
root = a.root.resolve()
fixtures = json.loads(a.fixtures.read_text())
stamp = fixtures['/api/catalysts/feed']['as_of']
state = {'requests': [], 'failure_prefix': None, 'failures_left': 0}

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def send_body(self, code, data, kind='application/json', cache='private, no-store'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        use_gzip = 'gzip' in self.headers.get('Accept-Encoding', '') and len(data) > 1024
        if use_gzip:
            data = gzip.compress(data, compresslevel=6)
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', cache)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Vary', 'Accept-Encoding')
        if use_gzip:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/__review__/state':
            return self.send_body(200, state)
        if path.startswith('/api/'):
            if path == '/api/catalysts/updates':
                # No background changes in a fixed-response experiment; 204
                # tells EventSource not to reconnect.
                self.send_response(204)
                self.end_headers()
                return
            if path == '/api/stocks/data/status':
                tickers = parse_qs(urlsplit(self.path).query).get('tickers', [''])[0].split(',')
                return self.send_body(200, {'items': [{'ticker': ticker, 'status': 'ready', 'refresh_status': 'ready',
                    'resources': {key: {'available': True, 'fresh': True, 'as_of': stamp}
                    for key in ('overview', 'daily_chart', 'signals')}} for ticker in tickers if ticker]})
            if path in fixtures:
                state['requests'].append({'url': self.path, 'status': 200})
                return self.send_body(200, fixtures[path])
            if path.startswith('/api/earnings/analysis/'):
                return self.send_body(200, {'status': 'idle', 'analysis': None})
            state['requests'].append({'url': self.path, 'status': 404})
            return self.send_body(404, {'detail': {'code': 'unknown_local_fixture', 'path': path}})
        if state['failure_prefix'] and path.startswith(state['failure_prefix']) and state['failures_left']:
            state['failures_left'] -= 1
            state['requests'].append({'url': self.path, 'status': 503})
            return self.send_body(503, {'error': 'local controlled asset failure'})
        file = root / path.lstrip('/')
        if not file.is_file() and not Path(path).suffix:
            file = root / 'index.html'
        if not file.is_file() or not file.resolve().is_relative_to(root):
            state['requests'].append({'url': self.path, 'status': 404})
            return self.send_body(404, {'error': 'file not found'})
        state['requests'].append({'url': self.path, 'status': 200})
        cache = 'public, max-age=31536000, immutable' if path.startswith('/assets/') else 'public, max-age=300'
        if file.name == 'index.html':
            cache = 'no-cache, no-store, must-revalidate'
        return self.send_body(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream', cache)

    def do_POST(self):
        # Consume request bodies before reusing an HTTP/1.1 connection.
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path == '/__review__/fault':
            value = json.loads(body)
            state['failure_prefix'] = value.get('prefix')
            state['failures_left'] = int(value.get('count', 0))
            return self.send_body(200, state)
        if self.path == '/api/catalysts/tickers/batch':
            return self.send_body(200, {'results': {}})
        return self.send_body(405, {'error': 'local fixture does not allow product writes'})

print(json.dumps({'root': str(root), 'port': a.port, 'fixture_time': stamp}), flush=True)
class FixtureServer(ThreadingHTTPServer):
    # Browsers can open speculative connections alongside their asset burst.
    request_queue_size = 128

FixtureServer(('127.0.0.1', a.port), Handler).serve_forever()
