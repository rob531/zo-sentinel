"""HTTP smoke against a RUNNING app container -- the deploy rehearsal's assertion
half. Hits a LIVE URL (real gunicorn/uvicorn + migrated Postgres + the built image),
not an in-process TestClient, so it catches prod-server/Dockerfile/migration breakage
the unit test cannot. Exits non-zero on any failure.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("APP_URL", "http://127.0.0.1:8000")


def _req(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def _wait_health(timeout=90):
    for _ in range(timeout):
        try:
            s, _b = _req("GET", "/health")
            if s == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    assert _wait_health(), "health endpoint never came up"
    org = f"org-{uuid.uuid4().hex[:8]}"
    s, _ = _req("POST", "/auth/register", {"email": f"admin@{org}.io", "password": "pw",
                "org_id": org, "role": "admin"}); assert s == 201, f"admin register {s}"
    s, _ = _req("POST", "/auth/register", {"email": f"member@{org}.io", "password": "pw",
                "org_id": org, "role": "member"}); assert s == 201, f"member register {s}"
    s, b = _req("POST", "/auth/login", {"email": f"member@{org}.io", "password": "pw"})
    assert s == 200, f"member login {s}"; mtok = b["access_token"]
    s, b = _req("GET", "/auth/me", token=mtok); assert s == 200 and b["role"] == "member", f"me {s} {b}"
    s, _ = _req("GET", "/auth/me"); assert s == 401, f"unauth me should 401, got {s}"
    s, _ = _req("GET", "/rbac/admin/ping", token=mtok); assert s == 403, f"member->admin should 403, got {s}"
    s, b = _req("POST", "/auth/login", {"email": f"admin@{org}.io", "password": "pw"}); atok = b["access_token"]
    s, _ = _req("GET", "/rbac/admin/ping", token=atok); assert s == 200, f"admin->admin should 200, got {s}"
    print("DEPLOY SMOKE PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"DEPLOY SMOKE FAIL: {e}")
        sys.exit(1)