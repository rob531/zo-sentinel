"""logic.py -- unauthenticated exposure summary computation."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpUnauthAccess


def get_unauth_exposure_summary(db: Session) -> Dict[str, Any]:
    """Summarize unauthenticated access records by value and source."""
    total_rows = int(
        db.execute(select(func.count()).select_from(McpUnauthAccess)).scalar() or 0
    )
    distinct_servers = int(
        db.execute(
            select(func.count(func.distinct(McpUnauthAccess.server_id)))
        ).scalar()
        or 0
    )

    grouped = db.execute(
        select(
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            func.count(func.distinct(McpUnauthAccess.server_id)),
        )
        .group_by(McpUnauthAccess.unauth_access, McpUnauthAccess.source)
    ).all()

    examples = db.execute(
        select(
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.server_id,
        ).distinct()
    ).all()

    examples_by_bucket: Dict[Any, List[str]] = {}
    for unauth_access, source, server_id in examples:
        key = (unauth_access, source)
        examples_by_bucket.setdefault(key, []).append(str(server_id))

    buckets = []
    for unauth_access, source, server_count in sorted(
        grouped,
        key=lambda row: (
            "" if row[0] is None else str(row[0]),
            "" if row[1] is None else str(row[1]),
        ),
    ):
        key = (unauth_access, source)
        buckets.append(
            {
                "unauth_access": unauth_access,
                "source": source,
                "server_count": int(server_count),
                "example_server_ids": sorted(examples_by_bucket.get(key, [])),
            }
        )

    return {
        "total_rows": total_rows,
        "distinct_servers": distinct_servers,
        "buckets": buckets,
    }


def unauth_exposure_summary(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the summary computation."""
    return get_unauth_exposure_summary(db)


def get_summary(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the summary computation."""
    return get_unauth_exposure_summary(db)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpUnauthAccess.__table__.create(engine)
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpUnauthAccess(
                    server_id="srv-001",
                    url="https://one.example",
                    unauth_access="public",
                    source="probe",
                ),
                McpUnauthAccess(
                    server_id="srv-002",
                    url="https://two.example",
                    unauth_access="public",
                    source="probe",
                ),
                McpUnauthAccess(
                    server_id="srv-003",
                    url="https://three.example",
                    unauth_access="protected",
                    source="probe",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/security/unauth-exposure/summary")
    def unauth_exposure_summary_route(db: Session = Depends(get_session)):
        return {
            **get_unauth_exposure_summary(db),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/security/unauth-exposure/summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_rows"] == 3
        assert payload["distinct_servers"] == 3
        assert sum(bucket["server_count"] for bucket in payload["buckets"]) == payload[
            "distinct_servers"
        ]
        assert {bucket["unauth_access"] for bucket in payload["buckets"]} == {
            "public",
            "protected",
        }

    print("PASS")