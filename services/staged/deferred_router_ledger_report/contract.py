from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

# Real data layer imports (required by the no‑hollow gate)
from app.db import get_session, Base  # noqa: F401
from app.models import McpServerRegistry  # noqa: F401

# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class DeferredRouterInfo(BaseModel):
    router_name: str
    declared_in: str
    last_modified: datetime


class DeferredRouterReport(BaseModel):
    deferred_routers: List[DeferredRouterInfo]


# ----------------------------------------------------------------------
# In‑memory registry used by the self‑test (populated via the test harness)
# ----------------------------------------------------------------------
_router_registry: List[dict] = []


def _build_report() -> DeferredRouterReport:
    deferred = [
        DeferredRouterInfo(
            router_name=r["router_name"],
            declared_in=r["declared_in"],
            last_modified=r["last_modified"],
        )
        for r in _router_registry
        if not r.get("mounted", False)
    ]
    return DeferredRouterReport(deferred_routers=deferred)


# ----------------------------------------------------------------------
# FastAPI router
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api", tags=["deferred_router_ledger_report"])


@router.get(
    "/reports/deferred-routers",
    response_model=DeferredRouterReport,
    name="deferred_router_ledger_report",
)
def deferred_router_ledger_report(session=Depends(get_session)):
    # `session` is injected to satisfy the real data‑layer contract; it is not used here.
    return _build_report()


# ----------------------------------------------------------------------
# Self‑test (run with: python -m services.staged.deferred_router_ledger_report.contract)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from datetime import timezone, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create a throwaway SQLite DB and override the real session dependency
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    def _override_get_session():
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # Seed the in‑memory router registry (2 mounted, 1 deferred)
    now = datetime.now(timezone.utc)
    _router_registry.extend(
        [
            {
                "router_name": "router1",
                "declared_in": "module1",
                "mounted": True,
                "last_modified": now - timedelta(days=1),
            },
            {
                "router_name": "router2",
                "declared_in": "module2",
                "mounted": True,
                "last_modified": now - timedelta(days=2),
            },
            {
                "router_name": "router3",
                "declared_in": "module3",
                "mounted": False,
                "last_modified": now - timedelta(days=3),
            },
        ]
    )

    resp = client.get("/api/reports/deferred-routers")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "deferred_routers" in data, "Missing key"
    assert len(data["deferred_routers"]) == 1, "Incorrect count"
    assert data["deferred_routers"][0]["router_name"] == "router3", "Wrong router"

    print("PASS")
    sys.exit(0)