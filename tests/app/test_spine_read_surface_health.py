"""Negative control for the /spine/health read-surface probe (FU-667 / #6985).

Before this fix /spine/health 'ok' was import-health ONLY: a router that mounted
fine read ok=true even when its :8772 backend was dead, which is how a 3-day
read-surface outage stayed invisible. This test OBSERVES the probe going RED
(connection refused, and HTTP 500) and GREEN (HTTP 200), and proves the RED
folds into the top-level `ok` while `mount_ok` keeps the old signal intact.

Sets a hermetic sqlite DATABASE_URL BEFORE importing the app (engine binds at
import), mirroring tests/app/test_app_skeleton.py.
"""
import os
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

_TMP = os.path.join(tempfile.gettempdir(), f"zo_spine_rs_{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}"
os.environ["APP_ENV"] = "dev"
os.environ["APP_JWT_SECRET"] = "test-secret-at-least-32-bytes-long-xx"

from app.main import _read_surface_health, spine_health  # noqa: E402


class _StubHandler(BaseHTTPRequestHandler):
    status = 200  # class-level knob the test flips

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile.read(length)
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"rows": []}')

    def log_message(self, *a):  # silence
        pass


def _serve(status):
    _StubHandler.status = status
    srv = HTTPServer(("127.0.0.1", 0), _StubHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def _with_backend(url):
    os.environ["WRITE_SERVICE_URL"] = url


def test_read_surface_green_when_backend_200():
    srv = _serve(200)
    try:
        _with_backend(f"http://127.0.0.1:{srv.server_address[1]}")
        rs = _read_surface_health()
        assert rs["ok"] is True, rs
        assert rs["status_code"] == 200, rs
        # GREEN read surface must NOT drag ok below mount_ok
        h = spine_health(deep=True)
        assert h["read_surface"]["ok"] is True
        assert h["ok"] == h["mount_ok"]
    finally:
        srv.shutdown()


def test_read_surface_red_on_connection_refused():
    # Nothing listening on this port -> connrefused, observed RED (fast, no hang).
    srv = _serve(200)
    port = srv.server_address[1]
    srv.shutdown()  # free the port so the connect is refused
    _with_backend(f"http://127.0.0.1:{port}")
    rs = _read_surface_health()
    assert rs["ok"] is False, rs
    assert rs["status_code"] is None
    assert rs["detail"]  # carries the exception class
    # the RED read surface folds into ok even though the mount is fine
    h = spine_health(deep=True)
    assert h["read_surface"]["ok"] is False
    assert h["ok"] is False


def test_read_surface_red_on_http_500():
    srv = _serve(500)
    try:
        _with_backend(f"http://127.0.0.1:{srv.server_address[1]}")
        rs = _read_surface_health()
        assert rs["ok"] is False, rs
        assert rs["status_code"] == 500
        assert spine_health(deep=True)["ok"] is False
    finally:
        srv.shutdown()


def test_deep_false_is_fast_path_mount_only():
    # ?deep=false skips the network probe entirely: read_surface is None and
    # ok reverts to the mount-only signal (so a load-balancer poll stays cheap).
    _with_backend("http://127.0.0.1:1")  # would be RED if probed
    h = spine_health(deep=False)
    assert h["read_surface"] is None
    assert h["ok"] == h["mount_ok"]
