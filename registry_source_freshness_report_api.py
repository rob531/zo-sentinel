"""GET /registry/freshness-report: scan staleness per registry source.

Reads Fly Postgres through the injected session. Until FU-667 (#6985) this
POSTed SQL to http://127.0.0.1:8772/query -- the factory DuckDB write_service,
which runs on the ZoComputer host and is not in the Fly image -- so on Fly the
route could only 500 (Errno 111), from its 2026-09-28 mount (#5689) on. The data
was in Postgres the whole time; the handler already took a session.

FU-667 (2026-10-19): the Postgres version still pulled EVERY scanned row
(registry_source, last_scanned) over the 560k-row registry into Python and
aggregated per-source there -- ~70s on the 1-vCPU moat PG, past every client
timeout. Now the per-source aggregation is pushed INTO SQL (one GROUP BY scan,
no row materialisation) AND the default-threshold view is served from the shared
freshness_stats snapshot (computed by one elected worker off the request path),
so the route reads one row O(1). Custom thresholds fall through to the bounded
SQL compute.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session
from app.models import McpServerRegistry
from app.read_surface_guard import SharedSnapshot, arm_statement_timeout, timeout_as_503

router = APIRouter()

# The public/watcher shape uses the default 30-day stale threshold; that is the
# view the shared snapshot precomputes.
_DEFAULT_STALE_THRESHOLD_DAYS = 30
CACHE_TTL_SECONDS = 3600


class SourceFreshness(BaseModel):
    source: str
    total_servers: int
    avg_days_since_last_scan: float
    max_days_since_last_scan: int
    stale_servers_pct: float
    oldest_server_seen_at: str


class FreshnessReport(BaseModel):
    sources: List[SourceFreshness]
    overall_stale_pct: float
    alerts: List[str]


def get_stale_threshold_days(stale_threshold_days: Optional[int] = Query(30)) -> int:
    return stale_threshold_days


def _as_utc(ts: datetime) -> datetime:
    # timestamptz comes back aware from Postgres, naive from sqlite.
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _compute(session: Session, stale_threshold_days: int) -> dict:
    """Per-source staleness, aggregated IN SQL (one GROUP BY scan, no row
    materialisation). days_since_last_scan is computed server-side; on Postgres
    via date arithmetic, on sqlite (CI) via julianday. Returns the JSON payload
    the route serves."""
    now = datetime.now(timezone.utc)
    scanned = McpServerRegistry.last_scanned
    is_pg = session.get_bind().dialect.name == "postgresql"

    # age in days since last_scanned, portable across pg + sqlite.
    if is_pg:
        age_days = func.extract("epoch", func.now() - scanned) / 86400.0
    else:
        age_days = func.julianday("now") - func.julianday(scanned)

    source_col = func.coalesce(McpServerRegistry.registry_source, "unknown").label("source")
    rows = session.execute(
        select(
            source_col,
            func.count().label("total_servers"),
            func.avg(age_days).label("avg_days"),
            func.max(age_days).label("max_days"),
            func.min(scanned).label("oldest_seen_at"),
            func.coalesce(
                func.sum(case((age_days > stale_threshold_days, 1), else_=0)), 0
            ).label("stale_servers"),
        )
        .where(scanned.isnot(None))
        .group_by(source_col)
    ).all()

    report_sources = []
    alerts = []
    total_servers = 0
    total_stale = 0
    for r in rows:
        total = int(r.total_servers or 0)
        stale = int(r.stale_servers or 0)
        total_servers += total
        total_stale += stale
        oldest = _as_utc(r.oldest_seen_at) if r.oldest_seen_at is not None else now
        stale_pct = (stale / total) * 100 if total else 0.0
        report_sources.append(SourceFreshness(
            source=r.source,
            total_servers=total,
            avg_days_since_last_scan=float(r.avg_days or 0.0),
            max_days_since_last_scan=int(r.max_days or 0),
            stale_servers_pct=stale_pct,
            oldest_server_seen_at=oldest.isoformat(),
        ).model_dump())
        if stale_pct > 0:
            alerts.append(
                f"Source '{r.source}' has {stale} stale servers "
                f"({stale_pct:.1f}%) - oldest scan was {oldest.isoformat()}"
            )

    overall_stale_pct = (total_stale / total_servers) * 100 if total_servers > 0 else 0.0
    return {
        "sources": report_sources,
        "overall_stale_pct": overall_stale_pct,
        "alerts": alerts,
    }


def _compute_default(session: Session) -> dict:
    return _compute(session, _DEFAULT_STALE_THRESHOLD_DAYS)


# FU-667: the default-threshold report is served from the shared snapshot (one
# elected worker computes it off the request path and writes freshness_stats);
# the route reads that one row O(1). Custom thresholds fall through to a bounded
# request-time SQL compute. advisory_lock_key in the FU-667 667_0xx band.
_snapshot = SharedSnapshot(
    "registry_source_freshness_report", _compute_default, SessionLocal,
    advisory_lock_key=667003,
    ttl_seconds=CACHE_TTL_SECONDS, refresh_interval_seconds=1800)


@router.get("/registry/freshness-report", response_model=FreshnessReport)
def get_freshness_report(
    stale_threshold_days: int = Depends(get_stale_threshold_days),
    session: Session = Depends(get_session),
):
    """Per-source scan staleness. The default 30-day view is served O(1) from the
    shared freshness_stats snapshot (FU-667); other thresholds compute in SQL,
    bounded by a statement_timeout so a slow pass is an honest 503, not a hang."""
    if stale_threshold_days == _DEFAULT_STALE_THRESHOLD_DAYS:
        return FreshnessReport(**_snapshot.get(session))

    arm_statement_timeout(session)
    try:
        return FreshnessReport(**_compute(session, stale_threshold_days))
    except Exception as exc:  # noqa: BLE001 -- timeout -> 503, else re-raise
        raise timeout_as_503(exc)


if __name__ == "__main__":
    import os
    os.environ.setdefault("APP_JWT_SECRET", "test-secret-at-least-32-bytes-long-xx")
    os.environ.setdefault("APP_ENV", "dev")
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, future=True)
    now = datetime.now(timezone.utc)
    s = TS()
    # npm: 2 servers, one fresh one very stale; pypi: 1 stale; a never-scanned row ignored.
    s.add_all([
        McpServerRegistry(server_id="n1", registry_source="npm", url="u",
                          last_scanned=now - timedelta(days=2)),
        McpServerRegistry(server_id="n2", registry_source="npm", url="u",
                          last_scanned=now - timedelta(days=90)),
        McpServerRegistry(server_id="p1", registry_source="pypi", url="u",
                          last_scanned=now - timedelta(days=60)),
        McpServerRegistry(server_id="x1", registry_source="npm", url="u"),  # never scanned
    ])
    s.commit(); s.close()

    # Direct SQL compute (default 30-day threshold).
    out = _compute(TS(), 30)
    by = {x["source"]: x for x in out["sources"]}
    assert by["npm"]["total_servers"] == 2, out
    assert by["pypi"]["total_servers"] == 1, out
    assert by["npm"]["stale_servers_pct"] == 50.0, out      # n2 stale, n1 fresh
    assert by["pypi"]["stale_servers_pct"] == 100.0, out    # p1 at 60d > 30d
    assert out["overall_stale_pct"] == (2 / 3) * 100, out   # 2 of 3 scanned stale
    assert any("npm" in a for a in out["alerts"]), out

    # Route via the snapshot (default threshold): 503 before warm, 200 after.
    app = FastAPI(); app.include_router(router)

    def _ov():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _ov
    c = TestClient(app)
    _snapshot._session_factory = TS  # compute against the seeded test DB
    assert _snapshot.refresh_if_stale(force=True) is True
    r = c.get("/registry/freshness-report")
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["sources"]) == 2, j

    # Custom threshold bypasses the snapshot -> direct SQL compute, still 200.
    r2 = c.get("/registry/freshness-report?stale_threshold_days=120")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    # at 120d nothing is stale
    assert j2["overall_stale_pct"] == 0.0, j2

    print("PASS")
