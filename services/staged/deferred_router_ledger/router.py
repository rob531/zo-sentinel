from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_deferred_routers, DeferredRouterResponse  # type: ignore

router = APIRouter(prefix="/api")


@router.get(
    "/deferred/routers",
    response_model=DeferredRouterResponse,
    summary="List deferred routers",
)
def list_deferred_routers(session: Session = Depends(get_session)):
    """Thin wrapper that forwards to the business logic."""
    return get_deferred_routers(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base  # declarative base used by the real models
    from app.models import ServiceHealth, CodeNode  # real ORM models

    # ------------------------------------------------------------------- #
    # Build a temporary in‑memory SQLite DB and override the session dep.
    # ------------------------------------------------------------------- #
    SQLITE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def _override_get_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed the DB with two routers having distinct ages.
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    router1 = ServiceHealth(
        name="router_one",
        status="running",
        last_heartbeat=now - timedelta(seconds=150),
    )
    router2 = ServiceHealth(
        name="router_two",
        status="running",
        last_heartbeat=now - timedelta(seconds=30),
    )
    node1 = CodeNode(name="router_one", handler="build_service")
    node2 = CodeNode(name="router_two", handler="build_service")

    with TestSessionLocal() as sess:
        sess.add_all([router1, router2, node1, node2])
        sess.commit()

    # ------------------------------------------------------------------- #
    # Assemble the FastAPI app, inject the override, and run the test.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    resp = client.get("/api/deferred/routers")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "routers" in data, "Missing 'routers' key in response"
    routers = data["routers"]
    assert len(routers) >= 2, "Expected at least two routers"

    # Verify that at least one router reports an age > 100 seconds
    ages = [r.get("age_seconds", 0) for r in routers]
    assert any(age > 100 for age in ages), "No router older than 100 seconds"

    print("PASS")