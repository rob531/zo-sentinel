from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


router = APIRouter(prefix="/api")


class UnauthExposureRow(BaseModel):
    server_id: str
    name: Optional[str]
    url: str
    unauth_access: str
    source: str
    updated_at: Optional[str]
    risk_tier: Optional[str]
    verdict: Optional[str]


class UnauthExposureResponse(BaseModel):
    total: int
    offset: int
    limit: int
    rows: List[UnauthExposureRow]


def _serialize_timestamp(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def list_unauth_exposure(
    db: Session,
    source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[int, List[dict]]:
    """Return the filtered, paginated unauthenticated-exposure records."""
    count_query = select(func.count()).select_from(McpUnauthAccess)
    query = (
        select(
            McpUnauthAccess.server_id,
            McpServerRegistry.name,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
        )
        .select_from(McpUnauthAccess)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpUnauthAccess.server_id,
        )
    )

    if source is not None:
        count_query = count_query.where(McpUnauthAccess.source == source)
        query = query.where(McpUnauthAccess.source == source)

    total = int(db.execute(count_query).scalar_one())

    result = db.execute(
        query.order_by(
            McpUnauthAccess.updated_at.desc().nullslast(),
            McpUnauthAccess.server_id.asc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    rows = [
        {
            "server_id": row.server_id or "",
            "name": row.name,
            "url": row.url or "",
            "unauth_access": row.unauth_access or "",
            "source": row.source or "",
            "updated_at": _serialize_timestamp(row.updated_at),
            "risk_tier": row.risk_tier,
            "verdict": row.verdict,
        }
        for row in result
    ]
    return total, rows


@router.get(
    "/exposure/unauth",
    response_model=UnauthExposureResponse,
)
def get_unauth_exposure(
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=0),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> UnauthExposureResponse:
    total, rows = list_unauth_exposure(db, source=source, limit=limit, offset=offset)
    return UnauthExposureResponse(
        total=total,
        offset=offset,
        limit=limit,
        rows=rows,
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    McpUnauthAccess.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)

    with SessionLocal() as session:
        session.add_all(
            [
                McpUnauthAccess(
                    server_id="scanner-1",
                    url="https://scanner.example/mcp",
                    unauth_access="tools/list",
                    source="registry_scanner",
                    updated_at=datetime(2024, 1, 1, 12, 0, 0),
                ),
                McpUnauthAccess(
                    server_id="manual-1",
                    url="https://manual.example/mcp",
                    unauth_access="tools/call",
                    source="manual_review",
                    updated_at=datetime(2024, 1, 2, 12, 0, 0),
                ),
                McpUnauthAccess(
                    server_id="registry-1",
                    url="https://registry.example/mcp",
                    unauth_access="resources/read",
                    source="registry_scanner",
                    updated_at=datetime(2024, 1, 3, 12, 0, 0),
                ),
            ]
        )
        session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-1",
                    name="Registry server",
                    risk_tier="high",
                    verdict="exposed",
                    trust_score=25,
                ),
                McpServerRegistry(
                    server_id="unrelated-1",
                    name="Unrelated server",
                    risk_tier="low",
                    verdict="trusted",
                    trust_score=90,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/exposure/unauth")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 3
        assert payload["rows"][0]["server_id"] == "registry-1"

        filtered = client.get("/api/exposure/unauth", params={"source": "manual_review"})
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] == 1

        for row in payload["rows"]:
            assert {"server_id", "url", "unauth_access", "source"}.issubset(row)

    print("PASS")