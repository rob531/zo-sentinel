from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter()


class ServerInfo(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    last_assessed: datetime | None = None

    class Config:
        orm_mode = True


class AlertResponse(BaseModel):
    servers: List[ServerInfo]
    count: int


@router.get("/api/alert/critical", response_model=AlertResponse)
def get_critical_alerts(db: Session = Depends(get_session)):
    records = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.risk_tier == "HIGH_RISK_ISOLATED")
        .order_by(McpServerRegistry.last_assessed.desc())
        .all()
    )
    servers = [
        ServerInfo(
            server_id=r.server_id,
            name=r.name,
            risk_tier=r.risk_tier,
            last_assessed=r.last_assessed,
        )
        for r in records
    ]
    return AlertResponse(servers=servers, count=len(servers))


if __name__ == "__main__":
    # ---- Test setup ---------------------------------------------------------
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)

    def get_test_session() -> Session:
        return TestSessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with TestSessionLocal() as sess:
        critical = McpServerRegistry(
            server_id="srv-1",
            name="CriticalServer",
            risk_tier="HIGH_RISK_ISOLATED",
            last_assessed=datetime.utcnow(),
        )
        sess.add(critical)
        sess.commit()

    client = TestClient(app)
    resp = client.get("/api/alert/critical")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert any(s["server_id"] == "srv-1" for s in data["servers"])
    print("PASS")