"""Serve fixtures/out/ on http://localhost:8099 with CORS, like raw.githubusercontent.com.

    python3 fixtures/serve.py [--port 8099]

The `flaky` set alternates: the first load works, the next fails with 503, the next works, and so
on, to test that a page keeps the last data after a failed refresh and recovers after. Open
/flaky/reset to start the sequence again.
"""

import argparse
import functools
import http.server
from pathlib import Path

served = {}


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/flaky/reset':
            served.clear()
            self.send_response(204)
            self.end_headers()
            return
        if path.startswith('/flaky/'):
            served[path] = served.get(path, 0) + 1
            if served[path] % 2 == 0:
                self.send_error(503, 'flaky fixture: every second load fails')
                return
        super().do_GET()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', type=int, default=8099)
    args = parser.parse_args()
    root = Path(__file__).parent / 'out'
    handler = functools.partial(Handler, directory=str(root))
    print(f'Serving {root} on http://localhost:{args.port}/<set>')
    http.server.ThreadingHTTPServer(('127.0.0.1', args.port), handler).serve_forever()


if __name__ == '__main__':
    main()
