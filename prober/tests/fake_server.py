"""A small local stand-in for nyc, so the prober can be tested without the network.

Each path maps to a list of steps. A request consumes the next step; the last
step repeats. A step is a dict: status, body (str, bytes, dict or list), delay.
"""
import json
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

API = '/universal/v1'

HTML = ('<!doctype html><html><head><title>GamesmanUni</title>'
        '<script type="module" crossorigin src="/uni/assets/index.abc123.js"></script>'
        '</head><body><div id="app"></div></body></html>')

# component id -> (path, expected value, expected remoteness)
CANARY_PATHS = {
    'solver-classic': (f'{API}/0to10by1or2/regular/positions/', 'win', 7),
    'solver-one': (f'{API}/quixo/3x3/positions/', 'win', 7),
    'solver-puzzles': (f'{API}/sevenpennies/regular/positions/', 'win', 14),
    'solver-py': (f'{API}/stormyseas/beg1/positions/', 'win', 3),
}
INSTRUCTIONS_PATH = f'{API}/0to10by1or2/regular/instructions/'


def canary_body(value, remoteness, moves=True):
    return {'position': 'p', 'positionValue': value, 'remoteness': remoteness,
            'moves': [{'move': 'm'}] if moves else []}


class FakeNyc:
    def __init__(self):
        self.routes = {}
        self.hits = Counter()
        self.queries = {}
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                fake.queries.setdefault(path, []).append(parsed.query)
                step = fake._next_step(path)
                if step is None:
                    step = {'status': 404, 'body': 'not found'}
                time.sleep(step.get('delay', 0))
                body = step.get('body', '')
                if isinstance(body, (dict, list)):
                    body = json.dumps(body)
                if isinstance(body, str):
                    body = body.encode()
                try:
                    self.send_response(step.get('status', 200))
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass  # the prober gave up on a slow response

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass

        self.server = Server(('127.0.0.1', 0), Handler)
        self.base_url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02},
                         daemon=True).start()

    def _next_step(self, path):
        with self._lock:
            self.hits[path] += 1
            steps = self.routes.get(path)
            if not steps:
                return None
            index = min(self.hits[path] - 1, len(steps) - 1)
            return steps[index]

    def set(self, path, *steps):
        self.routes[path] = list(steps)
        self.hits[path] = 0

    def set_healthy(self):
        self.set('/uni/', {'body': HTML})
        self.set('/uni/assets/index.abc123.js', {'body': 'console.log("app")'})
        self.set(f'{API}/', {'body': [{'id': f'game{i}'} for i in range(60)]})
        self.set(INSTRUCTIONS_PATH, {'body': {'instructions': '# 0 to 10 by 1 or 2\n\n## Rules\n...'}})
        for path, value, remoteness in CANARY_PATHS.values():
            self.set(path, {'body': canary_body(value, remoteness)})

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
