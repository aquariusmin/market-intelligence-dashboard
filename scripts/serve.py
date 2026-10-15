"""Local dashboard server (stdlib). Replaces `python3 -m http.server` for daily use.

- binds 127.0.0.1 only and rejects any Host header other than localhost / 127.0.0.1 (DNS-rebinding guard:
  a web page on another site cannot read the dashboard data through a rebound hostname);
- serves only the files the dashboard needs, never .git/, .venv/, scripts/, data/mi.sqlite or other files;
- sends a Content-Security-Policy that allows no third-party script, style, image or network origin.

Usage: python3 scripts/serve.py [--port 3000] [--root .]
"""
import argparse
import functools
import http.server
import re
from pathlib import Path

ALLOWED = re.compile(r'^/(index\.html|real-data\.js|intel\.js|data/markets\.json|data/dashboard/[a-z_]+\.json)$')
HOSTS = {'localhost', '127.0.0.1'}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")


def allowed_path(raw):
    path = raw.split('?', 1)[0].split('#', 1)[0]
    if path == '/':
        path = '/index.html'
    return path if ALLOWED.match(path) else None


def allowed_host(host):
    name = (host or '').strip().lower()
    name = name.rsplit(':', 1)[0] if re.fullmatch(r'[^:\[\]]+(:\d+)?', name) else name
    return name in HOSTS


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _check(self):
        if not allowed_host(self.headers.get('Host')):
            self.send_error(421, 'Misdirected Request')
            return False
        path = allowed_path(self.path)
        if path is None:
            self.send_error(404)
            return False
        self.path = path
        return True

    def do_GET(self):
        if self._check():
            super().do_GET()

    def do_HEAD(self):
        if self._check():
            super().do_HEAD()


def server(root, port=0):
    handler = functools.partial(Handler, directory=str(root))
    return http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--port', type=int, default=3000)
    p.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    args = p.parse_args()
    srv = server(args.root, args.port)
    print('http://localhost:%d  (127.0.0.1 only · Ctrl+C to stop)' % srv.server_address[1])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
