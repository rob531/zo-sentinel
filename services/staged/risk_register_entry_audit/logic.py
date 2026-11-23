"""Read-only audit view over the append-only ``mcp_risk_register`` table."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RiskRegisterEntry(BaseModel):
    server_id: str
    computed_at: datetime
    risk_value: Optional[float]
    verdict: Optional[str]
    source: Optional[str]


class RiskRegisterEntriesResponse(BaseModel):
    count: int
    entries: list[RiskRegisterEntry]


router = APIRouter(prefix="/api")
app = FastAPI(title="Risk Register Entry Audit")
app.include_router(router)


def _risk_register_table(db: Session) -> Table:
    """Reflect the real audit table so no duplicate or inline model is required."""
    return Table(
        "mcp_risk_register",
        MetaData(),
        autoload_with=db.get_bind(),
    )


def list_risk_register_entries(
    db: Session,
    limit: int,
    server_id: Optional[str],
) -> RiskRegisterEntriesResponse:
    """Return matching audit entries, newest first."""
    table = _risk_register_table(db)
    statement = select(
        table.c.server_id,
        table.c.computed_at,
        table.c.risk_value,
        table.c.verdict,
        table.c.source,
    )
    count_statement = select(func.count()).select_from(table)

    if server_id is not None:
        statement = statement.where(table.c.server_id == server_id)
        count_statement = count_statement.where(table.c.server_id == server_id)

    count = int(db.execute(count_statement).scalar_one())
    rows = db.execute(
        statement.order_by(table.c.computed_at.desc()).limit(limit)
    ).all()

    return RiskRegisterEntriesResponse(
        count=count,
        entries=[
            RiskRegisterEntry(
                server_id=row.server_id,
                computed_at=row.computed_at,
                risk_value=row.risk_value,
                verdict=row.verdict,
                source=row.source,
            )
            for row in rows
        ],
    )


@router.get("/risk-register/entries", response_model=RiskRegisterEntriesResponse)
def risk_register_entries(
    limit: int = Query(default=100),
    server_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> RiskRegisterEntriesResponse:
    clamped_limit = max(1, min(int(limit), 500))
    return list_risk_register_entries(db, clamped_limit, server_id)


if __name__ == "__main__":
    from datetime import timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table
    from sqlalchemy.orm import registry, sessionmaker
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_metadata = MetaData()
    test_table = Table(
        "mcp_risk_register",
        test_metadata,
        Column("id", Integer, primary_key=True),
        Column("server_id", String, nullable=False),
        Column("computed_at", DateTime, nullable=False),
        Column("risk_value", Float),
        Column("verdict", String),
        Column("source", String),
    )
    test_metadata.create_all(test_engine)

    test_registry = registry()

    class RiskRegisterRow:
        pass

    test_registry.map_imperatively(RiskRegisterRow, test_table)
    test_session_factory = sessionmaker(bind=test_engine)

    with test_session_factory() as db:
        db.add_all(
            [
                RiskRegisterRow(
                    server_id="server-a",
                    computed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    risk_value=10.0,
                    verdict="low",
                    source="contract",
                ),
                RiskRegisterRow(
                    server_id="server-b",
                    computed_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    risk_value=30.0,
                    verdict="medium",
                    source="contract",
                ),
                RiskRegisterRow(
                    server_id="server-c",
                    computed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    risk_value=20.0,
                    verdict="low",
                    source="contract",
                ),
            ]
        )
        db.commit()

    def override_session():
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_session

    with TestClient(that_app) as client:
        response = client.get("/api/risk-register/entries")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 3
        assert len(payload["entries"]) == 3
        assert payload["entries"][0]["server_id"] == "server-b"
        assert payload["entries"][1]["server_id"] == "server-c"
        assert payload["entries"][2]["server_id"] == "server-a"

        filtered = client.get(
            "/api/risk-register/entries",
            params={"server_id": "server-b"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["count"] == 1
        assert len(filtered_payload["entries"]) == 1
        assert filtered_payload["entries"][0]["server_id"] == "server-b"

    print("PASS")