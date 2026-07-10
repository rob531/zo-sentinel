from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import create_engine, select, table, column, insert, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import MCPExemption

router = APIRouter(prefix="/servers", tags=["exemptions"])


class ExemptionHistoryItem(BaseModel):
    exemption_id: int
    reason: Optional[str]
    granted_by: Optional[str]
    risk_tier_at_exemption: Optional[str]
    expires_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


@router.get(
    "/servers/{server_id}/exemptions/history",
    response_model=list[ExemptionHistoryItem],
)
def get_server_exemption_history(
    server_id: str,
    session: Session = Depends(get_session),
) -> list[ExemptionHistoryItem]:
    stmt = (
        select(
            MCPExemption.exemption_id,
            MCPExemption.reason,
            MCPExemption.granted_by,
            MCPExemption.risk_tier_at_exemption,
            MCPExemption.expires_at,
            MCPExemption.created_at,
        )
        .where(MCPExemption.server_id == server_id)
        .order_by(MCPExemption.created_at.desc())
    )
    results = session.execute(stmt).fetchall()
    return [
        ExemptionHistoryItem(
            exemption_id=row.exemption_id,
            reason=row.reason,
            granted_by=row.granted_by,
            risk_tier_at_exemption=row.risk_tier_at_exemption,
            expires_at=row.expires_at,
            created_at=row.created_at,
        )
        for row in results
    ]


@router.get(
    "/servers/exemptions/history",
    response_model=list[ExemptionHistoryItem],
)
def get_all_exemptions_history(
    session: Session = Depends(get_session),
) -> list[ExemptionHistoryItem]:
    stmt = select(
        MCPExemption.exemption_id,
        MCPExemption.reason,
        MCPExemption.granted_by,
        MCPExemption.risk_tier_at_exemption,
        MCPExemption.expires_at,
        MCPExemption.created_at,
    ).order_by(MCPExemption.created_at.desc())
    results = session.execute(stmt).fetchall()
    return [
        ExemptionHistoryItem(
            exemption_id=row.exemption_id,
            reason=row.reason,
            granted_by=row.granted_by,
            risk_tier_at_exemption=row.risk_tier_at_exemption,
            expires_at=row.expires_at,
            created_at=row.created_at,
        )
        for row in results
    ]


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine("sqlite:///:memory:", echo=False)
    metadata = __import__("sqlalchemy").MetaData()
    mcp_exemptions = __import__("sqlalchemy").Table(
        "mcp_exemptions",
        metadata,
        column("exemption_id", __import__("sqlalchemy").Integer, primary_key=True),
        column("server_id", __import__("sqlalchemy").String),
        column("reason", __import__("sqlalchemy").String),
        column("granted_by", __import__("sqlalchemy").String),
        column("risk_tier_at_exemption", __import__("sqlalchemy").String),
        column("expires_at", __import__("sqlalchemy").DateTime),
        column("created_at", __import__("sqlalchemy").DateTime),
    )
    metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)

    with engine.connect() as conn:
        conn.execute(
            insert(mcp_exemptions).values(
                exemption_id=1,
                server_id="test-id",
                reason="Low risk environment",
                granted_by="admin@example.com",
                risk_tier_at_exemption="low",
                expires_at=datetime(2025, 12, 31, 23, 59, 59),
                created_at=datetime(2024, 1, 15, 10, 30, 0),
            )
        )
        conn.commit()

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/servers/test-id/exemptions/history")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert isinstance(data, list), "Response should be a list"
    assert len(data) >= 1, "Expected at least one exemption record"
    record = data[0]
    expected_fields = {
        "exemption_id",
        "reason",
        "granted_by",
        "risk_tier_at_exemption",
        "expires_at",
        "created_at",
    }
    assert expected_fields.issubset(record.keys()), (
        f"Missing fields. Expected {expected_fields}, got {set(record.keys())}"
    )
    assert record["exemption_id"] == 1
    assert record["reason"] == "Low risk environment"
    assert record["granted_by"] == "admin@example.com"
    assert record["risk_tier_at_exemption"] == "low"

    print("PASS")