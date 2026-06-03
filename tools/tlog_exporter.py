#!/usr/bin/env python3
"""
tlog_exporter.py -- read-only HTTP exporter for recorded terminal sessions.

Serves the `script(1)` typescripts under --dir (default /home/workspace/logs/tlog)
so the Tower can pull them over the tailnet into its long-term SQLite memory
store (sessions.db). Stdlib-only, GET-only, never writes/deletes. Binds 0.0.0.0
by default -- Modal does not expose container ports publicly, so only the tailnet
peers (+ localhost) reach it; that matches the sibling services (write_service
:8772, ladder_shim :8796). Pairs with the Tower-side pull_sessions.py.

Launched by go.sh under `zm go` (see tools/patch_go_sh_tlog.py). Capture itself
is `script(1)` via the zorec wrapper / rc hook (tools/telemetry_capture_setup.py)
-- tlog(1) is unusable on this Modal container (needs an audit session id the
container doesn't provide: "Failed retrieving session ID").

Endpoints:
    GET /health      -> {"ok": true, ...}
    GET /sessions    -> [{"name","size","mtime"}...]   (newest first)
    GET /raw/<name>  -> the raw typescript bytes (text/plain)

Optional shared-secret: set TLOG_EXPORT_TOKEN in the env to require a matching
`X-Tlog-Token` header (or ?token=) on /sessions and /raw. Unset = open on the
tailnet (the default; these logs are LLM-access keys at most, batch-scrubbed).

Usage:
    python3 tlog_exporter.py --dir /home/workspace/logs/tlog --port 8788
"""
from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

SAFE = re.compile(r"^[A-Za-z0-9._-]+$")   # basenames only -- no path traversal


class Handler(BaseHTTPRequestHandler):
    server_version = "tlog-exporter/1.0"
    directory = "."
    token = None

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode("utf-8", "replace")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authed(self, qs):
        if not self.token:
            return True
        return (self.headers.get("X-Tlog-Token") == self.token
                or (qs.get("token") or [None])[0] == self.token)

    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        if path == "/health":
            return self._send(200, {"ok": True, "dir": self.directory,
                                    "auth": bool(self.token)})
        if not self._authed(qs):
            return self._send(401, {"error": "unauthorized"})
        if path in ("/", "/sessions"):
            out = []
            try:
                for n in os.listdir(self.directory):
                    p = os.path.join(self.directory, n)
                    if os.path.isfile(p) and SAFE.match(n):
                        st = os.stat(p)
                        out.append({"name": n, "size": st.st_size,
                                    "mtime": st.st_mtime})
            except FileNotFoundError:
                pass
            out.sort(key=lambda d: d["mtime"], reverse=True)
            return self._send(200, out)
        if path.startswith("/raw/"):
            name = path[len("/raw/"):]
            if not SAFE.match(name):
                return self._send(400, {"error": "bad name"})
            p = os.path.join(self.directory, name)
            if not os.path.isfile(p):
                return self._send(404, {"error": "not found"})
            with open(p, "rb") as f:
                return self._send(200, f.read(), "text/plain; charset=utf-8")
        return self._send(404, {"error": "no such endpoint"})

    # read-only: refuse anything that isn't GET
    def do_POST(self):
        self._send(405, {"error": "read-only"})
    do_PUT = do_DELETE = do_PATCH = do_POST

    def log_message(self, fmt, *args):   # quiet; go.sh redirects stdout to a log
        pass


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/home/workspace/logs/tlog")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--bind", default="0.0.0.0")
    a = ap.parse_args(argv)
    os.makedirs(a.dir, exist_ok=True)
    Handler.directory = a.dir
    Handler.token = os.environ.get("TLOG_EXPORT_TOKEN") or None
    httpd = ThreadingHTTPServer((a.bind, a.port), Handler)
    print(f"tlog_exporter serving {a.dir} on {a.bind}:{a.port} "
          f"(auth={'on' if Handler.token else 'off'})", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
