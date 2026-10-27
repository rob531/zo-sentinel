from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_registry_scan_health

router = APIRouter(prefix="/api")


@router.get("/registry/scan-health")
def registry_scan_health_endpoint(
    days: int = 30,
    session: Session = Depends(get_session),
):
    """
    Return registry scan health aggregates.

    Parameters
    ----------
    days: int
        Staleness threshold in days (default 30, must be between 1 and 3650).
    session: Session
        SQLAlchemy session injected by FastAPI dependency.
    """
    return get_registry_scan_health(session=session, days=days)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime as _dt
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base
    from app.models import McpServerRegistry

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and seed it with test data
    # ------------------------------------------------------------------- #
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine)

    def _get_test_session() -> Session:  # pragma: no cover
        return _SessionLocal()

    now = _dt.datetime.utcnow()
    rows = [
        McpServerRegistry(
            server_id="srv-1",
            name="Server One",
            registry_source="sourceA",
            first_seen=now - _dt.timedelta(days=60),
            last_seen=now,
            last_scanned=now - _dt.timedelta(days=3),
            last_assessed=now - _dt.timedelta(days=2),
            scan_count=5,
            risk_tier="high",
            verdict="clean",
            trust_score=0.9,
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="Server Two",
            registry_source="sourceA",
            first_seen=now - _dt.timedelta(days=70),
            last_seen=now,
            last_scanned=now - _dt.timedelta(days=5),
            last_assessed=now - _dt.timedelta(days=4),
            scan_count=3,
            risk_tier="medium",
            verdict="clean",
            trust_score=0.8,
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="Server Three",
            registry_source="sourceB",
            first_seen=now - _dt.timedelta(days=80),
            last_seen=now,
            last_scanned=None,
            last_assessed=now - _dt.timedelta(days=1),
            scan_count=0,
            risk_tier="low",
            verdict="clean",
            trust_score=0.7,
        ),
        McpServerRegistry(
            server_id="srv-4",
            name="Server Four",
            registry_source="sourceC",
            first_seen=now - _dt.timedelta(days=90),
            last_seen=now,
            last_scanned=now - _dt.timedelta(days=40),
            last_assessed=None,
            scan_count=2,
            risk_tier="high",
            verdict="dirty",
            trust_score=0.5,
        ),
        McpServerRegistry(
            server_id="srv-5",
            name="Server Five",
            registry_source="sourceC",
            first_seen=now - _dt.timedelta(days=100),
            last_seen=now,
            last_scanned=now - _dt.timedelta(days=10),
            last_assessed=None,
            scan_count=1,
            risk_tier="medium",
            verdict="clean",
            trust_score=0.6,
        ),
    ]

    with _SessionLocal() as sess:
        sess.add_all(rows)
        sess.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app, include router, and override the session dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute request and validate contract
    # ------------------------------------------------------------------- #
    resp = client.get("/api/registry/scan-health")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # days echo
    assert data["days"] == 30

    # sources length matches distinct registry_source values (3)
    assert len(data["sources"]) == 3, "Incorrect number of sources"

    # source with NULL last_scanned reports never_scanned == 1
    src_b = next(s for s in data["sources"] if s["registry_source"] == "sourceB")
    assert src_b["never_scanned"] == 1, "sourceB never_scanned mismatch"

    # totals
    totals = data["totals"]
    assert totals["servers"] == 5, "Total server count mismatch"
    assert isinstance(totals["avg_scan_count"], float), "avg_scan_count not float"
    assert totals["avg_scan_count"] >= 0, "avg_scan_count negative"

    print("PASS")