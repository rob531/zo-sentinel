"""Logic for registry facet counts using the real application data layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _facet_rows(
    db: Session,
    column: Any,
    null_value: str,
) -> List[Dict[str, Any]]:
    value = func.coalesce(func.nullif(column, ""), null_value)
    count = func.count()

    rows = db.execute(
        select(value, count)
        .group_by(value)
        .order_by(count.desc(), value.asc())
    ).all()

    return [{"value": str(row_value), "count": int(count_value)} for row_value, count_value in rows]


def get_registry_facets(db: Session) -> Dict[str, Any]:
    """Compute registry facet counts from the authoritative registry table."""
    total = int(db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0)

    return {
        "total": total,
        "by_registry_source": _facet_rows(
            db,
            McpServerRegistry.registry_source,
            "unknown",
        ),
        "by_risk_tier": _facet_rows(
            db,
            McpServerRegistry.risk_tier,
            "unscored",
        ),
        "by_verdict": _facet_rows(
            db,
            McpServerRegistry.verdict,
            "unknown",
        ),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_registry_facets(db: Session) -> Dict[str, Any]:
    """Compatibility name for callers that use the computation naming convention."""
    return get_registry_facets(db)


def compute_facets(db: Session) -> Dict[str, Any]:
    """Compatibility name for callers that use the generic facet naming convention."""
    return get_registry_facets(db)


def get_facets(db: Session) -> Dict[str, Any]:
    """Compatibility name for callers that use the generic facet naming convention."""
    return get_registry_facets(db)


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
    TestSession = sessionmaker(bind=test_engine)

    McpServerRegistry.__table__.create(test_engine)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-1",
                    registry_source="source-a",
                    risk_tier="high",
                    verdict="pass",
                ),
                McpServerRegistry(
                    server_id="registry-2",
                    registry_source="source-a",
                    risk_tier="medium",
                    verdict="fail",
                ),
                McpServerRegistry(
                    server_id="registry-3",
                    registry_source="source-a",
                    risk_tier="low",
                    verdict="pass",
                ),
                McpServerRegistry(
                    server_id="registry-4",
                    registry_source="source-b",
                    risk_tier="high",
                    verdict="fail",
                ),
                McpServerRegistry(
                    server_id="registry-5",
                    registry_source="source-b",
                    risk_tier="medium",
                    verdict="pass",
                ),
                McpServerRegistry(
                    server_id="registry-6",
                    registry_source="",
                    risk_tier=None,
                    verdict="pass",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/facets")
    def registry_facets(db: Session = Depends(get_session)):
        return get_registry_facets(db)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/facets")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 6
    assert any(
        facet["value"] == "unscored" and facet["count"] == 1
        for facet in payload["by_risk_tier"]
    )
    assert all(facet["value"] for facet in payload["by_registry_source"])
    assert sum(facet["count"] for facet in payload["by_registry_source"]) == payload["total"]
    assert sum(facet["count"] for facet in payload["by_risk_tier"]) == payload["total"]
    assert sum(facet["count"] for facet in payload["by_verdict"]) == payload["total"]
    print("PASS")