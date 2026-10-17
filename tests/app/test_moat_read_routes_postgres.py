"""The moat read routes answer from the app database, never from :8772 (FU-667 / #6985).

/registry/freshness-report and /scoring/waves/summary POSTed SQL to
http://127.0.0.1:8772/query -- the factory DuckDB write_service, which is not in
the Fly image -- so on Fly both could only 500 (Errno 111).
Their tables are the app's own (mcp_server_registry, cadence_job_runs). These
tests seed those tables, block every HTTP call, and check the numbers.

Also pins /freshness `never_scored` (now NOT EXISTS instead of NOT IN) to the
set difference: orphan scores and multi-version rows must not move it.

Sets a hermetic sqlite DATABASE_URL BEFORE importing the app (engine binds at
import), mirroring tests/app/test_app_skeleton.py.
"""
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_TMP = os.path.join(tempfile.gettempdir(), f"zo_moat_routes_{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}"
os.environ["APP_ENV"] = "dev"
os.environ["APP_JWT_SECRET"] = "test-secret-at-least-32-bytes-long-xx"

import pytest  # noqa: E402
import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import scoring_freshness_surface  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CadenceJobRun, McpLlmAxisScore, McpServerRegistry  # noqa: E402

NOW = datetime.now(timezone.utc)


@pytest.fixture
def client(monkeypatch):
    def _no_http(*a, **k):
        raise AssertionError("read route made an HTTP call; :8772 does not exist on Fly")

    monkeypatch.setattr(requests, "post", _no_http)
    monkeypatch.setattr(requests.Session, "request", _no_http)
    with TestClient(app) as c:  # lifespan -> init_db()
        with SessionLocal() as s:
            for model in (McpServerRegistry, McpLlmAxisScore, CadenceJobRun):
                s.query(model).delete()
            s.commit()
        yield c


def _seed(*rows):
    with SessionLocal() as s:
        s.add_all(rows)
        s.commit()


def test_registry_freshness_report_reads_the_registry(client):
    _seed(
        McpServerRegistry(server_id="a1", registry_source="smithery", last_scanned=NOW - timedelta(days=1)),
        McpServerRegistry(server_id="a2", registry_source="smithery", last_scanned=NOW - timedelta(days=5)),
        McpServerRegistry(server_id="b1", registry_source="glama", last_scanned=NOW - timedelta(days=40)),
        McpServerRegistry(server_id="b2", registry_source="glama", last_scanned=NOW - timedelta(days=45)),
        McpServerRegistry(server_id="c1", registry_source=None, last_scanned=NOW - timedelta(days=20)),
        McpServerRegistry(server_id="d1", registry_source="smithery", last_scanned=None),  # never scanned
    )
    r = client.get("/registry/freshness-report?stale_threshold_days=30")
    assert r.status_code == 200, r.text
    body = r.json()
    by_source = {s["source"]: s for s in body["sources"]}
    assert set(by_source) == {"smithery", "glama", "unknown"}
    assert by_source["smithery"]["total_servers"] == 2
    assert by_source["smithery"]["stale_servers_pct"] == 0
    assert by_source["smithery"]["max_days_since_last_scan"] == 5
    assert by_source["glama"]["total_servers"] == 2
    assert by_source["glama"]["stale_servers_pct"] == 100
    assert by_source["glama"]["max_days_since_last_scan"] == 45
    assert by_source["unknown"]["total_servers"] == 1
    assert body["overall_stale_pct"] == pytest.approx(40.0)
    assert len(body["alerts"]) == 1 and "glama" in body["alerts"][0]


def test_registry_freshness_report_empty_registry(client):
    r = client.get("/registry/freshness-report")
    assert r.status_code == 200, r.text
    assert r.json() == {"sources": [], "overall_stale_pct": 0, "alerts": []}


def test_scoring_wave_summary_reads_cadence_job_runs(client):
    t1, t2 = datetime(2026, 10, 10, 3, 0), datetime(2026, 10, 15, 3, 0)
    _seed(
        CadenceJobRun(job="score_wave_1", status="ok", started_at=t1, finished_at=t1,
                      rows_affected=100, detail={"cost_usd": 1.5}),
        CadenceJobRun(job="score_wave_2", status="failed", started_at=t2, finished_at=t2,
                      rows_affected=50, detail={"cost_usd": 0.75}),
        CadenceJobRun(job="scoring_batch", status="ok", started_at=t1, finished_at=t1,
                      rows_affected=200, detail={"cost_usd": "3.0"}),
        CadenceJobRun(job="score_wave_3", status="running", started_at=t2,
                      finished_at=None, rows_affected=None, detail=None),
        # `_` is a LIKE wildcard: an unescaped 'score_wave%' would count this one.
        CadenceJobRun(job="scoreXwave_9", status="ok", started_at=t1, finished_at=t1,
                      rows_affected=999, detail={"cost_usd": 99.0}),
        CadenceJobRun(job="perspective_snapshot", status="ok", started_at=t1, finished_at=t1,
                      rows_affected=500, detail={"cost_usd": 9.0}),
    )
    r = client.get("/scoring/waves/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_waves"] == 4
    assert body["servers_scored"] == 350
    assert body["avg_per_wave"] == pytest.approx(350 / 3)
    assert body["last_wave_at"] == t2.isoformat()
    assert body["error_count"] == 1
    assert body["cost_estimate_usd"] == pytest.approx(5.25)


def test_scoring_wave_summary_no_runs(client):
    r = client.get("/scoring/waves/summary")
    assert r.status_code == 200, r.text
    assert r.json() == {"total_waves": 0, "servers_scored": 0, "avg_per_wave": 0.0,
                        "last_wave_at": None, "error_count": 0, "cost_estimate_usd": None}


def test_freshness_never_scored_is_the_set_difference(client):
    _seed(
        McpServerRegistry(server_id="r1"),
        McpServerRegistry(server_id="r2"),
        McpServerRegistry(server_id="r3"),
        # r1 scored on two axes under two coexisting model versions (v3.0 + v3.4).
        # Explicit ids: sqlite only autoincrements INTEGER, and this PK is BigInteger.
        McpLlmAxisScore(id=1, server_id="r1", axis_name="auth", model_version="v3.0"),
        McpLlmAxisScore(id=2, server_id="r1", axis_name="exfil", model_version="v3.0"),
        McpLlmAxisScore(id=3, server_id="r1", axis_name="auth", model_version="v3.4"),
        McpLlmAxisScore(id=4, server_id="r1", axis_name="exfil", model_version="v3.4"),
        # an orphan score must not credit the registry
        McpLlmAxisScore(id=5, server_id="orphan", axis_name="auth", model_version="v3.0"),
    )
    scoring_freshness_surface._cache = None  # process-local TTL cache
    r = client.get("/freshness")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registry_rows"] == 3
    assert body["scores_rows"] == 5
    assert body["scored_servers"] == 2
    assert body["never_scored"] == 2
