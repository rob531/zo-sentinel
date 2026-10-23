"""
services.staged.cadence_runtime_trend.contract

Mirrors the exemplar contract implementation. Provides a minimal FastAPI
router exposing a `/trend` endpoint that reads from the real
`CadenceJobRun` model via the application’s `get_session` dependency.

The module can be executed directly (`python -m services.staged.cadence_runtime_trend.contract`)
which runs a self‑test using an in‑memory SQLite database (StaticPool) and
prints ``PASS`` on success.
"""

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from typing import List, Dict, Any

# Real data layer imports – never mocked or replaced here.
from app.db import get_session, Base  # Base is the declarative base for the models.
from app.models import CadenceJobRun

router = APIRouter()


def _serialize(obj: Any) -> Dict[str, Any]:
    """Convert a SQLAlchemy model instance to a plain dict, stripping internal attrs."""
    return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}


@router.get("/trend", response_model=List[Dict[str, Any]])
def get_trend(db: Session = Depends(get_session)):
    """
    Return a list of cadence job runs. The real service would aggregate
    runtime statistics; for contract purposes we simply return the raw rows.
    """
    try:
        runs = db.query(CadenceJobRun).all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return [_serialize(r) for r in runs]


# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build an isolated SQLite engine (StaticPool) and create all tables.
    # ------------------------------------------------------------------- #
    sqlite_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=sqlite_engine)

    # Session factory bound to the SQLite engine.
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sqlite_engine)

    # Dependency override that yields a session from the test engine.
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Assemble a FastAPI app with the router and the overridden dependency.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Execute a simple request against the `/trend` endpoint.
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/trend")
    if response.status_code == 200:
        print("PASS")
        sys.exit(0)
    else:
        sys.exit(1)