"""logic.py -- unauthenticated-access registry-gap computation over the real app DB."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


class UnauthAccessGapRow(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    unauth_access: Optional[bool] = None
    source: Optional[str] = None
    updated_at: Optional[datetime] = None
    risk_tier: Optional[str] = None


class UnauthAccessGapResponse(BaseModel):
    source: Optional[str] = None
    total: int
    with_registry_row: int
    missing_from_registry: int
    rows: list[UnauthAccessGapRow]


router = APIRouter(prefix="/api")


def get_unauth_access_gap(
    db: Session,
    source: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Return unauthenticated-access servers and identify absent registry rows."""
    limit = max(1, min(int(limit), 500))

    count_stmt = (
        select(
            func.count(),
            func.count(McpServerRegistry.server_id),
        )
        .select_from(McpUnauthAccess)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpUnauthAccess.server_id,
        )
    )
    if source is not None:
        count_stmt = count_stmt.where(McpUnauthAccess.source == source)

    total, with_registry_row = db.execute(count_stmt).one()
    total = int(total)
    with_registry_row = int(with_registry_row)

    row_stmt = (
        select(
            McpUnauthAccess.server_id,
            McpServerRegistry.name,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpUnauthAccess)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpUnauthAccess.server_id,
        )
        .order_by(McpUnauthAccess.server_id)
        .limit(limit)
    )
    if source is not None:
        row_stmt = row_stmt.where(McpUnauthAccess.source == source)

    rows = [
        UnauthAccessGapRow(
            server_id=row.server_id,
            name=row.name,
            url=row.url,
            unauth_access=row.unauth_access,
            source=row.source,
            updated_at=row.updated_at,
            risk_tier=row.risk_tier,
        )
        for row in db.execute(row_stmt).all()
    ]

    return {
        "source": source,
        "total": total,
        "with_registry_row": with_registry_row,
        "missing_from_registry": total - with_registry_row,
        "rows": rows,
    }


@router.get(
    "/registry/unauth-access-gap",
    response_model=UnauthAccessGapResponse,
)
def unauth_access_gap_endpoint(
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> dict:
    return get_unauth_access_gap(db, source=source, limit=limit)


app = FastAPI(title="unauth_access_registry_gap")
app.include_router(router)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpUnauthAccess.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    session.add(
        McpUnauthAccess(
            server_id="server-with-registry",
            source="test-source",
            url="https://example.test/with",
            unauth_access=True,
        )
    )
    session.add(
        McpUnauthAccess(
            server_id="server-without-registry",
            source="test-source",
            url="https://example.test/without",
            unauth_access=True,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="server-with-registry",
            name="Registered server",
            registry_source="test-source",
            risk_tier="medium",
            trust_score=0.5,
        )
    )
    session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield session

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    response = client.get(
        "/api/registry/unauth-access-gap",
        params={"source": "test-source", "limit": 50},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["missing_from_registry"] == 1
    assert {row["server_id"] for row in payload["rows"]} == {
        "server-with-registry",
        "server-without-registry",
    }
    print("PASS")


if __name__ == "__main__":
    _self_test()