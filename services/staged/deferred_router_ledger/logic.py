from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.db import get_session
from app.models import ServiceHealth, CodeNode, Base

router = APIRouter(prefix="/api")


class RouterInfo(BaseModel):
    name: str
    age_seconds: int
    last_heartbeat: datetime


class RoutersResponse(BaseModel):
    routers: List[RouterInfo]


@router.get("/deferred/routers", response_model=RoutersResponse)
def get_deferred_routers(db: Session = Depends(get_session)):
    """
    Return all routers that are currently running and have a code node
    with handler `build_service`.
    """
    now = datetime.utcnow()
    rows = (
        db.query(ServiceHealth, CodeNode)
        .join(CodeNode, ServiceHealth.name == CodeNode.name)
        .filter(
            and_(
                ServiceHealth.status == "running",
                CodeNode.handler == "build_service",
            )
        )
        .all()
    )

    routers = [
        RouterInfo(
            name=sh.name,
            age_seconds=int((now - sh.last_heartbeat).total_seconds()),
            last_heartbeat=sh.last_heartbeat,
        )
        for sh, cn in rows
    ]

    return RoutersResponse(routers=routers)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the app dependency
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed test data: two routers with different ages
    # ------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        now = datetime.utcnow()
        router1 = ServiceHealth(
            name="router_one",
            status="running",
            last_heartbeat=now,
        )
        router2 = ServiceHealth(
            name="router_two",
            status="running",
            last_heartbeat=now,
        )
        # router_one will appear older after we adjust its heartbeat
        router1.last_heartbeat = now.replace(microsecond=0)  # keep current time
        router2.last_heartbeat = now.replace(microsecond=0) - timedelta(seconds=200)

        code1 = CodeNode(name="router_one", handler="build_service")
        code2 = CodeNode(name="router_two", handler="build_service")

        db.add_all([router1, router2, code1, code2])
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/deferred/routers")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "routers" in data, "Missing routers key"
    assert len(data["routers"]) == 2, f"Expected 2 routers, got {len(data['routers'])}"
    ages = [r["age_seconds"] for r in data["routers"]]
    assert any(age > 100 for age in ages), "No router older than 100 seconds"
    print("PASS")