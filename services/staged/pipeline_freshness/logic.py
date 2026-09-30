"""Logic for the pipeline_freshness service.

Provides a single function `get_freshness` that aggregates information from the
application database tables `mcp_server_registry` and `cadence_job_runs`.
"""

from datetime import datetime, timedelta

from fastapi import Depends
from sqlalchemy import func, select, desc
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import CadenceJobRun, McpServerRegistry


def get_freshness(session: Session = Depends(get_session)):
    """Return freshness metrics for the pipeline.

    The returned dictionary contains:
        - servers_total
        - servers_never_scanned
        - servers_stale_24h
        - last_ingest_job
        - ingest_rows_24h
        - scanner_cycle_age_seconds
        - registry_growth_24h
    """
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)

    # Total servers
    servers_total = session.scalar(
        select(func.count()).select_from(McpServerRegistry)
    )

    # Servers that have never been scanned (last_scanned IS NULL)
    servers_never_scanned = session.scalar(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(McpServerRegistry.last_scanned.is_(None))
    )

    # Servers whose last scan is older than 24 hours
    servers_stale_24h = session.scalar(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(McpServerRegistry.last_scanned.is_not(None))
        .where(McpServerRegistry.last_scanned < cutoff_24h)
    )

    # Most recent ingest job (job name = 'ingest')
    last_ingest_job_row = session.execute(
        select(CadenceJobRun)
        .where(CadenceJobRun.job == "ingest")
        .order_by(desc(CadenceJobRun.started_at))
        .limit(1)
    ).scalar_one_or_none()
    last_ingest_job = last_ingest_job_row.job if last_ingest_job_row else None

    # Rows affected by ingest jobs in the last 24 hours
    ingest_rows_24h = session.scalar(
        select(func.coalesce(func.sum(CadenceJobRun.rows_affected), 0))
        .where(CadenceJobRun.job == "ingest")
        .where(CadenceJobRun.started_at >= cutoff_24h)
    )

    # Age of the scanner cycle (seconds since the most recent scan)
    max_last_scanned = session.scalar(
        select(func.max(McpServerRegistry.last_scanned))
    )
    scanner_cycle_age_seconds = (
        (now - max_last_scanned).total_seconds()
        if max_last_scanned is not None
        else None
    )

    # Number of servers added in the last 24 hours
    registry_growth_24h = session.scalar(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(McpServerRegistry.first_seen >= cutoff_24h)
    )

    return {
        "servers_total": servers_total,
        "servers_never_scanned": servers_never_scanned,
        "servers_stale_24h": servers_stale_24h,
        "last_ingest_job": last_ingest_job,
        "ingest_rows_24h": ingest_rows_24h,
        "scanner_cycle_age_seconds": scanner_cycle_age_seconds,
        "registry_growth_24h": registry_growth_24h,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True)

    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)

    # Populate test data
    with TestSession() as session:
        # Servers
        s_never = McpServerRegistry(
            server_id=1,
            name="never_scanned",
            first_seen=now - timedelta(days=2),
            last_scanned=None,
            scan_count=0,
        )
        s_stale = McpServerRegistry(
            server_id=2,
            name="stale_server",
            first_seen=now - timedelta(days=2),
            last_scanned=now - timedelta(hours=30),
            scan_count=1,
        )
        s_fresh = McpServerRegistry(
            server_id=3,
            name="fresh_server",
            first_seen=now - timedelta(hours=1),
            last_scanned=now - timedelta(hours=1),
            scan_count=1,
        )
        session.add_all([s_never, s_stale, s_fresh])

        # Cadence job runs
        ingest_recent = CadenceJobRun(
            id=1,
            job="ingest",
            status="success",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=55),
            rows_affected=123,
            detail="recent ingest",
        )
        other_old = CadenceJobRun(
            id=2,
            job="other",
            status="success",
            started_at=now - timedelta(hours=25),
            finished_at=now - timedelta(hours=24, minutes=55),
            rows_affected=45,
            detail="old non‑ingest",
        )
        session.add_all([ingest_recent, other_old])
        session.commit()

        # Run the logic
        result = get_freshness(session)

        # Assertions required by the acceptance test
        assert result["servers_never_scanned"] == 1, "servers_never_scanned mismatch"
        assert result["servers_stale_24h"] == 1, "servers_stale_24h mismatch"

        print("PASS")