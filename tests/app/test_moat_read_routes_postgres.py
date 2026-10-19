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
    import registry_source_freshness_report_api
    registry_source_freshness_report_api._snapshot.refresh_if_stale(force=True)  # warm default-threshold snapshot
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
    import registry_source_freshness_report_api
    registry_source_freshness_report_api._snapshot.refresh_if_stale(force=True)  # warm (empty) snapshot
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
    # /freshness serves a background snapshot; drive one synchronous refresh so
    # the GET is deterministic (the daemon would otherwise race the assert).
    scoring_freshness_surface._snapshot.refresh_if_stale(force=True)
    r = client.get("/freshness")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registry_rows"] == 3
    assert body["scores_rows"] == 5
    assert body["scored_servers"] == 2
    assert body["never_scored"] == 2


def test_registry_freshness_aggregates_in_sql(client):
    """FU-667: /registry/freshness no longer materialises every scanned row into
    Python; it computes the counts/buckets in SQL. Pins the numbers end-to-end
    so the SQL ladder stays equivalent to the old per-row loop."""
    import registry_freshness_api
    registry_freshness_api._cache.clear()
    _seed(
        # fresh (<24h)
        McpServerRegistry(server_id="f1", last_scanned=NOW - timedelta(hours=2)),
        # fresh (<7d, >24h)
        McpServerRegistry(server_id="f2", last_scanned=NOW - timedelta(days=3)),
        # stale 7d..30d
        McpServerRegistry(server_id="s1", last_scanned=NOW - timedelta(days=14)),
        # very stale >30d
        McpServerRegistry(server_id="v1", last_scanned=NOW - timedelta(days=45)),
        # never scanned
        McpServerRegistry(server_id="n1"),
        McpServerRegistry(server_id="n2"),
        # f1 is the only assessed server
        McpLlmAxisScore(id=1, server_id="f1", axis_name="auth", model_version="v3.0"),
    )
    registry_freshness_api._snapshot.refresh_if_stale(force=True)  # warm the default-params snapshot
    r = client.get("/registry/freshness")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total_servers"] == 6, j
    assert j["scanned_servers"] == 4, j
    assert j["never_scanned"] == 2, j
    assert j["stale_servers"] == 2, j  # s1 + v1
    assert j["freshness_buckets"]["fresh_24h"] == 1, j  # f1
    assert j["freshness_buckets"]["fresh_7d"] == 2, j   # f1 + f2
    assert j["freshness_buckets"]["stale_7d_30d"] == 1, j  # s1
    assert j["freshness_buckets"]["stale_over_30d"] == 1, j  # v1
    assert j["assessed_servers"] == 1, j
    assert j["oldest_scan"]["server_id"] == "v1", j


def test_registry_freshness_caps_never_scanned_list(client):
    """The never-scanned preview is a dashboard hint, not a bulk export (THE
    LINE): the LIST is capped while the never_scanned COUNT stays exact. This is
    also what stops the route re-materialising the whole never-scanned corpus."""
    import registry_freshness_api
    registry_freshness_api._cache.clear()
    cap = registry_freshness_api._NEVER_SCANNED_LIST_CAP
    _seed(*[McpServerRegistry(server_id=f"never{i}") for i in range(cap + 25)])
    registry_freshness_api._snapshot.refresh_if_stale(force=True)  # warm the default-params snapshot
    r = client.get("/registry/freshness")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["never_scanned"] == cap + 25, "COUNT must be exact"
    assert len(j["never_scanned_servers"]) == cap, "LIST must be capped"


def test_shared_snapshot_computes_once_per_cadence(client):
    """FU-667 core invariant: the heavy compute runs ONCE per cadence, not once
    per worker/request. A fresh row short-circuits refresh_if_stale (no recompute),
    and concurrent refreshers do not each recompute -- this is what stops the
    per-worker pile-up that saturated the PG and forced the v122->v121 rollback."""
    import threading as _threading
    from app.read_surface_guard import SharedSnapshot
    from app.db import SessionLocal

    calls = {"n": 0}
    barrier_calls = _threading.Lock()

    def _compute(db):
        with barrier_calls:
            calls["n"] += 1
        return {"scores_rows": 1}

    snap = SharedSnapshot("test_once", _compute, SessionLocal, advisory_lock_key=667099,
                          ttl_seconds=3600, refresh_interval_seconds=1800)
    # First refresh computes and persists.
    assert snap.refresh_if_stale(force=True) is True
    assert calls["n"] == 1
    # A row fresher than the interval short-circuits: no recompute.
    assert snap.refresh_if_stale() is False
    assert calls["n"] == 1, "a fresh row must not trigger a recompute"
    # Many concurrent refreshers against a fresh row: still no extra compute.
    threads = [_threading.Thread(target=snap.refresh_if_stale) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1, f"concurrent refreshers must not each recompute (got {calls['n']})"
