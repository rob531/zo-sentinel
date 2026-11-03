"""logic.py -- unauthenticated-access exposure board data and API layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


router = APIRouter(prefix="/api")


class UnauthExposureItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    unauth_access: Optional[str] = None
    source: Optional[str] = None
    risk_tier: Optional[str] = None
    updated_at: Optional[datetime] = None


class UnauthExposureBoard(BaseModel):
    total: int
    by_access: Dict[str, int]
    items: List[UnauthExposureItem]


class UnauthExposureDetailRow(UnauthExposureItem):
    verdict: Optional[str] = None
    trust_score: Optional[float] = None


class UnauthExposureDetail(BaseModel):
    server_id: str
    rows: List[UnauthExposureDetailRow]


def _access_key(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value)


def _board_statement():
    return (
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpUnauthAccess)
        .outerjoin(
            McpServerRegistry,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
        )
        .order_by(McpUnauthAccess.updated_at.desc())
    )


def get_unauth_exposure_board(db: Session, limit: int = 100) -> UnauthExposureBoard:
    """Return the exposure board, including aggregate counts for all rows."""
    if limit < 1:
        limit = 1
    limit = min(limit, 500)

    total = int(
        db.execute(
            select(func.count()).select_from(McpUnauthAccess)
        ).scalar()
        or 0
    )

    grouped = db.execute(
        select(McpUnauthAccess.unauth_access, func.count())
        .select_from(McpUnauthAccess)
        .group_by(McpUnauthAccess.unauth_access)
    ).all()

    by_access: Dict[str, int] = {}
    for access, count in grouped:
        key = _access_key(access)
        by_access[key] = by_access.get(key, 0) + int(count or 0)

    rows = db.execute(_board_statement().limit(limit)).all()
    items = [
        UnauthExposureItem(
            server_id=row.server_id,
            name=row.name,
            url=row.url,
            unauth_access=(
                None if row.unauth_access is None else str(row.unauth_access)
            ),
            source=row.source,
            risk_tier=row.risk_tier,
            updated_at=row.updated_at,
        )
        for row in rows
    ]

    return UnauthExposureBoard(
        total=total,
        by_access=by_access,
        items=items,
    )


def get_unauth_exposure_detail(
    db: Session, server_id: str
) -> UnauthExposureDetail:
    """Return all unauthenticated-access rows for one registry server."""
    rows = db.execute(
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.trust_score,
        )
        .select_from(McpUnauthAccess)
        .outerjoin(
            McpServerRegistry,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
        )
        .where(McpUnauthAccess.server_id == server_id)
        .order_by(McpUnauthAccess.updated_at.desc())
    ).all()

    return UnauthExposureDetail(
        server_id=server_id,
        rows=[
            UnauthExposureDetailRow(
                server_id=row.server_id,
                name=row.name,
                url=row.url,
                unauth_access=(
                    None if row.unauth_access is None else str(row.unauth_access)
                ),
                source=row.source,
                risk_tier=row.risk_tier,
                updated_at=row.updated_at,
                verdict=row.verdict,
                trust_score=(
                    None
                    if row.trust_score is None
                    else float(row.trust_score)
                ),
            )
            for row in rows
        ],
    )


# Backwards-compatible descriptive aliases for callers that use either name.
list_unauth_exposure = get_unauth_exposure_board
get_unauth_exposure_server = get_unauth_exposure_detail


@router.get(
    "/exposure/unauth",
    response_model=UnauthExposureBoard,
    status_code=200,
)
def unauth_exposure_board(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> UnauthExposureBoard:
    return get_unauth_exposure_board(db, limit)


@router.get(
    "/exposure/unauth/{server_id}",
    response_model=UnauthExposureDetail,
    status_code=200,
)
def unauth_exposure_detail(
    server_id: str,
    db: Session = Depends(get_session),
) -> UnauthExposureDetail:
    return get_unauth_exposure_detail(db, server_id)


if __name__ == "__main__":
    from datetime import timezone

    from fastapi import FastAPI
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

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    seeded = [
        ("server-alpha", "Alpha", "public", "scanner", "https://alpha.example"),
        ("server-beta", "Beta", "public", "scanner", "https://beta.example"),
        ("server-gamma", "Gamma", "restricted", "scanner", "https://gamma.example"),
    ]

    session = TestingSession()
    try:
        for server_id, name, access, source, url in seeded:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    risk_tier="medium",
                    verdict="review",
                    trust_score=0.5,
                )
            )
            session.add(
                McpUnauthAccess(
                    server_id=server_id,
                    url=url,
                    unauth_access=access,
                    source=source,
                    updated_at=now,
                )
            )
        session.commit()
    finally:
        session.close()

    client = TestClient(that_app)
    board_response = client.get("/api/exposure/unauth")
    assert board_response.status_code == 200
    board = board_response.json()
    assert board["total"] == 3
    assert board["by_access"] == {"public": 2, "restricted": 1}
    assert any(item["server_id"] == "server-alpha" for item in board["items"])

    detail_response = client.get("/api/exposure/unauth/server-alpha")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["server_id"] == "server-alpha"
    assert len(detail["rows"]) == 1
    assert detail["rows"][0]["server_id"] == "server-alpha"

    print("PASS")