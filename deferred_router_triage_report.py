from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter(prefix="/routers/deferred", tags=["routers"])


class DeferredRouterTriageItem(BaseModel):
    router_id: str
    router_name: Optional[str]
    status: str
    last_checked: Optional[datetime]
    deferral_reason: Optional[str]


class DeferredRouterTriageReport(BaseModel):
    triage_report: List[DeferredRouterTriageItem]
    total_count: int


@router.get("/triage", response_model=DeferredRouterTriageReport)
def get_deferred_router_triage(session: Session = Depends(get_session)):
    deferred_routers = (
        session.query(MCPServerRegistry)
        .filter(MCPServerRegistry.status == "deferred")
        .order_by(MCPServerRegistry.last_checked.desc())
        .all()
    )

    triage_items = [
        DeferredRouterTriageItem(
            router_id=str(router.id),
            router_name=router.name,
            status=router.status,
            last_checked=router.last_checked,
            deferral_reason=router.deferral_reason,
        )
        for router in deferred_routers
    ]

    return DeferredRouterTriageReport(
        triage_report=triage_items,
        total_count=len(triage_items),
    )


app = router


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", echo=False)
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()

    r1 = MCPServerRegistry(
        id="router-test-001",
        name="Alpha Router",
        status="deferred",
        last_checked=datetime(2024, 1, 15, 10, 0),
        deferral_reason="resource_constraint",
    )
    r2 = MCPServerRegistry(
        id="router-test-002",
        name="Beta Router",
        status="deferred",
        last_checked=datetime(2024, 1, 15, 9, 0),
        deferral_reason="config_pending",
    )
    r3 = MCPServerRegistry(
        id="router-test-003",
        name="Gamma Router",
        status="active",
        last_checked=datetime(2024, 1, 15, 8, 0),
        deferral_reason=None,
    )
    test_session.add_all([r1, r2, r3])
    test_session.commit()

    app.dependency_overrides[get_session] = lambda: test_session

    client = TestClient(app)
    response = client.get("/routers/deferred/triage")

    data = response.json()
    assert data["total_count"] >= 1, f"Expected non-empty deferred router list, got: {data}"

    print("PASS")