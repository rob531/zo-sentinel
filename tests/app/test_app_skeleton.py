"""App-skeleton functional test: auth + RBAC end-to-end over a hermetic sqlite DB.
Sets DATABASE_URL to a temp sqlite file BEFORE importing the app (engine binds at
import). Proves the assembled app boots and enforces auth/roles -- the deploy gate.
"""
import os
import tempfile
import uuid

_TMP = os.path.join(tempfile.gettempdir(), f"zo_app_test_{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}"
os.environ["APP_ENV"] = "dev"
os.environ["APP_JWT_SECRET"] = "test-secret-at-least-32-bytes-long-xx"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def test_auth_and_rbac_end_to_end():
    with TestClient(app) as c:  # context manager fires lifespan -> init_db()
        assert c.get("/health").json()["status"] == "ok"

        assert c.post("/auth/register", json={"email": "admin@x.io", "password": "pw",
                      "org_id": "org1", "role": "admin"}).status_code == 201
        assert c.post("/auth/register", json={"email": "member@x.io", "password": "pw",
                      "org_id": "org1", "role": "member"}).status_code == 201
        # duplicate email -> 409
        assert c.post("/auth/register", json={"email": "member@x.io", "password": "pw",
                      "org_id": "org1"}).status_code == 409

        mtok = c.post("/auth/login", json={"email": "member@x.io", "password": "pw"}).json()["access_token"]
        mh = {"Authorization": f"Bearer {mtok}"}
        me = c.get("/auth/me", headers=mh)
        assert me.status_code == 200 and me.json()["role"] == "member" and me.json()["org_id"] == "org1"

        assert c.get("/auth/me").status_code == 401                 # no token
        assert c.get("/rbac/admin/ping", headers=mh).status_code == 403   # member -> admin route

        atok = c.post("/auth/login", json={"email": "admin@x.io", "password": "pw"}).json()["access_token"]
        ah = {"Authorization": f"Bearer {atok}"}
        assert c.get("/rbac/admin/ping", headers=ah).status_code == 200   # admin ok

        assert c.post("/auth/login", json={"email": "member@x.io", "password": "wrong"}).status_code == 401

