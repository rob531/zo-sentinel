"""Negative control for the /spine/health read-surface probe (FU-667 / #6985).

Before #6990 /spine/health 'ok' was import-health ONLY: a router that mounted
fine read ok=true even when its backend was dead. #6990 aimed the probe at
:8772, a service that is not in the Fly image, so on prod it could only read
RED and would have REJECTed every deploy. The read routes query Postgres, so the
probe does too. This test OBSERVES it going GREEN (tables readable), RED (a
read-surface table unreadable), and RED within its bound when the database
never answers -- and proves no HTTP call is made at all.

Sets a hermetic sqlite DATABASE_URL BEFORE importing the app (engine binds at
import), mirroring tests/app/test_app_skeleton.py.
"""
import os
import tempfile
import time
import uuid

_TMP = os.path.join(tempfile.gettempdir(), f"zo_spine_rs_{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}"
os.environ["APP_ENV"] = "dev"
os.environ["APP_JWT_SECRET"] = "test-secret-at-least-32-bytes-long-xx"

import pytest  # noqa: E402
import requests  # noqa: E402

import app.db as app_db  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.main import _read_surface_health, spine_health  # noqa: E402
from app.models import CadenceJobRun  # noqa: E402


@pytest.fixture(autouse=True)
def _tables_and_no_http(monkeypatch):
    init_db()

    def _no_http(*a, **k):
        raise AssertionError("the read-surface probe must not make HTTP calls (:8772 is not on Fly)")

    monkeypatch.setattr(requests, "post", _no_http)
    monkeypatch.setattr(requests.Session, "request", _no_http)


def test_read_surface_green_when_tables_readable():
    rs = _read_surface_health()
    assert rs["ok"] is True, rs
    assert rs["detail"] is None
    assert rs["backend"] == "sqlite"
    assert set(rs["tables"]) == {"mcp_server_registry", "mcp_llm_axis_scores", "cadence_job_runs"}
    # GREEN read surface must NOT drag ok below mount_ok
    h = spine_health(deep=True)
    assert h["read_surface"]["ok"] is True
    assert h["ok"] == h["mount_ok"]


def test_read_surface_red_when_a_table_is_unreadable():
    CadenceJobRun.__table__.drop(engine)
    try:
        rs = _read_surface_health()
        assert rs["ok"] is False, rs
        assert "cadence_job_runs" in rs["detail"]
        # the RED read surface folds into ok even though the mount is fine
        h = spine_health(deep=True)
        assert h["read_surface"]["ok"] is False
        assert h["ok"] is False
    finally:
        CadenceJobRun.__table__.create(engine)


def test_read_surface_red_within_bound_when_db_never_answers(monkeypatch):
    class _HungEngine:
        dialect = engine.dialect

        def connect(self):
            time.sleep(5)
            raise RuntimeError("never reached inside the bound")

    monkeypatch.setattr(app_db, "engine", _HungEngine())
    started = time.monotonic()
    rs = _read_surface_health(timeout=0.3)
    elapsed = time.monotonic() - started
    assert rs["ok"] is False, rs
    assert "no answer within" in rs["detail"]
    assert elapsed < 2.0, f"health probe inherited the hang: {elapsed:.1f}s"


def test_deep_false_is_fast_path_mount_only():
    # ?deep=false skips the probe entirely: read_surface is None and ok
    # reverts to the mount-only signal (so a load-balancer poll stays cheap).
    h = spine_health(deep=False)
    assert h["read_surface"] is None
    assert h["ok"] == h["mount_ok"]
