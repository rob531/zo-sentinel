"""Data and computation layer for the unauthenticated access surface service."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpUnauthAccess


def _filtered_rows(
    db: Session,
    server_id: Optional[str] = None,
    source: Optional[str] = None,
) -> List[McpUnauthAccess]:
    stmt = select(McpUnauthAccess)
    if server_id is not None:
        stmt = stmt.where(McpUnauthAccess.server_id == server_id)
    if source is not None:
        stmt = stmt.where(McpUnauthAccess.source == source)
    return list(
        db.execute(
            stmt.order_by(
                McpUnauthAccess.server_id,
                McpUnauthAccess.url,
                McpUnauthAccess.source,
            )
        ).scalars()
    )


def _exposed_filter():
    value = func.lower(McpUnauthAccess.unauth_access)
    return or_(
        value.like("%unauth%"),
        value.like("%none%"),
        value.like("%public%"),
    )


def is_exposed_unauth_access(value: Optional[str]) -> bool:
    """Return whether an access classification represents an exposed endpoint."""
    if value is None:
        return False
    normalized = value.casefold()
    return "unauth" in normalized or "none" in normalized or "public" in normalized


def get_all_unauth_access(
    db: Session,
    server_id: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return all unauthenticated-access records matching the optional filters."""
    return [
        {
            "server_id": row.server_id,
            "url": row.url,
            "unauth_access": row.unauth_access,
            "source": row.source,
            "updated_at": row.updated_at,
        }
        for row in _filtered_rows(db, server_id, source)
    ]


def get_exposed_unauth_access(
    db: Session,
    server_id: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return records whose access classification indicates an exposed endpoint."""
    stmt = select(McpUnauthAccess).where(_exposed_filter())
    if server_id is not None:
        stmt = stmt.where(McpUnauthAccess.server_id == server_id)
    if source is not None:
        stmt = stmt.where(McpUnauthAccess.source == source)

    rows = db.execute(
        stmt.order_by(
            McpUnauthAccess.server_id,
            McpUnauthAccess.url,
            McpUnauthAccess.source,
        )
    ).scalars()

    return [
        {
            "server_id": row.server_id,
            "url": row.url,
            "unauth_access": row.unauth_access,
            "source": row.source,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


def count_unauth_access_rows(
    db: Session,
    server_id: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    """Count all records matching the optional filters."""
    stmt = select(func.count()).select_from(McpUnauthAccess)
    if server_id is not None:
        stmt = stmt.where(McpUnauthAccess.server_id == server_id)
    if source is not None:
        stmt = stmt.where(McpUnauthAccess.source == source)
    return int(db.execute(stmt).scalar_one())


def count_exposed_unauth_access(
    db: Session,
    server_id: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    """Count records indicating an exposed endpoint."""
    stmt = select(func.count()).select_from(McpUnauthAccess).where(_exposed_filter())
    if server_id is not None:
        stmt = stmt.where(McpUnauthAccess.server_id == server_id)
    if source is not None:
        stmt = stmt.where(McpUnauthAccess.source == source)
    return int(db.execute(stmt).scalar_one())


def get_unauth_access_summary(
    db: Session,
    server_id: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the endpoint summary over the real application database."""
    return {
        "total": count_unauth_access_rows(db, server_id, source),
        "exposed": count_exposed_unauth_access(db, server_id, source),
        "items": get_exposed_unauth_access(db, server_id, source),
    }


def list_unauth_access(
    db: Session,
    server_id: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List exposed unauthenticated-access records."""
    return get_exposed_unauth_access(db, server_id, source)


def get_unauth_access_rows(
    db: Session,
    server_id: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Alias for listing exposed unauthenticated-access records."""
    return get_exposed_unauth_access(db, server_id, source)


def get_unauth_access(
    db: Session,
    server_id: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Alias for listing exposed unauthenticated-access records."""
    return get_exposed_unauth_access(db, server_id, source)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpUnauthAccess.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        db.add_all(
            [
                McpUnauthAccess(
                    server_id="srv-exposed-1",
                    url="https://one.example/health",
                    unauth_access="UNAUTH",
                    source="probe-a",
                ),
                McpUnauthAccess(
                    server_id="srv-exposed-2",
                    url="https://two.example/metrics",
                    unauth_access="public endpoint",
                    source="probe-b",
                ),
                McpUnauthAccess(
                    server_id="srv-private-1",
                    url="https://three.example/admin",
                    unauth_access="authenticated",
                    source="probe-a",
                ),
                McpUnauthAccess(
                    server_id="srv-private-2",
                    url="https://four.example/internal",
                    unauth_access="internal only",
                    source="probe-b",
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/security/unauth-access")
    def unauth_access_endpoint(
        server_id: Optional[str] = None,
        source: Optional[str] = None,
        db: Session = Depends(get_session),
    ):
        return get_unauth_access_summary(db, server_id, source)

    client = TestClient(test_app)
    response = client.get("/api/security/unauth-access")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["exposed"] == 2
    assert len(payload["items"]) == 2

    filtered = client.get(
        "/api/security/unauth-access",
        params={"server_id": "srv-exposed-1"},
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["total"] == 1
    assert filtered_payload["exposed"] == 1
    assert len(filtered_payload["items"]) == 1
    assert filtered_payload["items"][0]["server_id"] == "srv-exposed-1"

    print("PASS")