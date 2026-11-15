"""services/staged/verdict_reasoning_completeness/logic.py

Data/computation layer for the verdict_reasoning_completeness service.
Provides a single pure function that, given a SQLAlchemy Session, returns
the completeness metrics required by the FastAPI endpoint.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def get_verdict_completeness(db: Session) -> Dict[str, Any]:
    """Return verdict‑completeness statistics for the whole registry.

    The result matches the shape expected by the router/pydantic model:

    {
        "total_servers": int,
        "totals": {
            "with_verdict": int,
            "with_reasoning": int,
            "with_confidence": int,
            "with_risk_tier": int,
            "never_assessed": int,
        },
        "by_source": [
            {
                "registry_source": str,
                "total_servers": int,
                "with_verdict": int,
                "with_reasoning": int,
                "with_confidence": int,
                "with_risk_tier": int,
            },
            ...
        ],
        "missing_reasoning_examples": [
            {
                "server_id": str,
                "verdict": str | None,
                "registry_source": str | None,
                "last_assessed": str | None,
            },
            ...
        ],
    }
    """
    # ------------------------------------------------------------------ #
    # Overall aggregates
    # ------------------------------------------------------------------ #
    overall_stmt = (
        select(
            func.count(McpServerRegistry.server_id).label("total_servers"),
            func.count(
                case(
                    (
                        (McpServerRegistry.verdict != None)
                        & (McpServerRegistry.verdict != ""),
                        1,
                    ),
                    else_=None,
                )
            ).label("with_verdict"),
            func.count(
                case(
                    (
                        (McpServerRegistry.verdict_reasoning != None)
                        & (func.trim(McpServerRegistry.verdict_reasoning) != ""),
                        1,
                    ),
                    else_=None,
                )
            ).label("with_reasoning"),
            func.count(
                case(((McpServerRegistry.confidence != None), 1), else_=None)
            ).label("with_confidence"),
            func.count(
                case(((McpServerRegistry.risk_tier != None), 1), else_=None)
            ).label("with_risk_tier"),
            func.count(
                case(((McpServerRegistry.last_assessed == None), 1), else_=None)
            ).label("never_assessed"),
        )
        .select_from(McpServerRegistry)
        .limit(1)
    )
    overall_row = db.execute(overall_stmt).one()

    # ------------------------------------------------------------------ #
    # Per‑source aggregates
    # ------------------------------------------------------------------ #
    source_stmt = (
        select(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id).label("total_servers"),
            func.count(
                case(
                    (
                        (McpServerRegistry.verdict != None)
                        & (McpServerRegistry.verdict != ""),
                        1,
                    ),
                    else_=None,
                )
            ).label("with_verdict"),
            func.count(
                case(
                    (
                        (McpServerRegistry.verdict_reasoning != None)
                        & (func.trim(McpServerRegistry.verdict_reasoning) != ""),
                        1,
                    ),
                    else_=None,
                )
            ).label("with_reasoning"),
            func.count(
                case(((McpServerRegistry.confidence != None), 1), else_=None)
            ).label("with_confidence"),
            func.count(
                case(((McpServerRegistry.risk_tier != None), 1), else_=None)
            ).label("with_risk_tier"),
        )
        .group_by(McpServerRegistry.registry_source)
    )
    source_rows = db.execute(source_stmt).all()

    # ------------------------------------------------------------------ #
    # Missing‑reasoning examples (most recent 20)
    # ------------------------------------------------------------------ #
    missing_stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_assessed,
        )
        .where(
            (McpServerRegistry.verdict_reasoning == None)
            | (func.trim(McpServerRegistry.verdict_reasoning) == "")
        )
        .order_by(McpServerRegistry.last_assessed.desc().nulls_last())
        .limit(20)
    )
    missing_rows = db.execute(missing_stmt).all()

    # ------------------------------------------------------------------ #
    # Build the response dict
    # ------------------------------------------------------------------ #
    result: Dict[str, Any] = {
        "total_servers": int(overall_row.total_servers),
        "totals": {
            "with_verdict": int(overall_row.with_verdict),
            "with_reasoning": int(overall_row.with_reasoning),
            "with_confidence": int(overall_row.with_confidence),
            "with_risk_tier": int(overall_row.with_risk_tier),
            "never_assessed": int(overall_row.never_assessed),
        },
        "by_source": [],
        "missing_reasoning_examples": [],
    }

    for row in source_rows:
        result["by_source"].append(
            {
                "registry_source": row.registry_source,
                "total_servers": int(row.total_servers),
                "with_verdict": int(row.with_verdict),
                "with_reasoning": int(row.with_reasoning),
                "with_confidence": int(row.with_confidence),
                "with_risk_tier": int(row.with_risk_tier),
            }
        )

    for row in missing_rows:
        result["missing_reasoning_examples"].append(
            {
                "server_id": row.server_id,
                "verdict": row.verdict,
                "registry_source": row.registry_source,
                "last_assessed": (
                    row.last_assessed.isoformat()
                    if row.last_assessed is not None
                    else None
                ),
            }
        )

    return result


# ---------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# ---------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import datetime

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session  # original dependency (will be overridden)

    # ------------------------------------------------------------------ #
    # Test session factory bound to an in‑memory SQLite DB
    # ------------------------------------------------------------------ #
    TEST_ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=TEST_ENGINE)

    def get_test_session() -> Session:  # type: ignore
        """Yield a Session bound to the in‑memory test engine."""
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Create tables
    McpServerRegistry.__table__.metadata.create_all(TEST_ENGINE)

    # Seed test data
    with TestSessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="s1",
                    registry_source="src1",
                    verdict="good",
                    verdict_reasoning="all good",
                    confidence=0.9,
                    risk_tier="low",
                    last_assessed=datetime.datetime(2023, 1, 1, 12, 0, 0),
                ),
                McpServerRegistry(
                    server_id="s2",
                    registry_source="src1",
                    verdict="bad",
                    verdict_reasoning=None,
                    confidence=0.5,
                    risk_tier="high",
                    last_assessed=datetime.datetime(2023, 1, 2, 12, 0, 0),
                ),
                McpServerRegistry(
                    server_id="s3",
                    registry_source="src2",
                    verdict="unknown",
                    verdict_reasoning="needs review",
                    confidence=0.7,
                    risk_tier="medium",
                    last_assessed=None,
                ),
            ]
        )
        db.commit()

    # ------------------------------------------------------------------ #
    # Minimal FastAPI app exposing the endpoint
    # ------------------------------------------------------------------ #
    app = FastAPI()

    @app.get(
        "/api/registry/verdict-completeness",
        response_model=Dict[str, Any],
    )
    def endpoint(db: Session = Depends(get_session)):
        return get_verdict_completeness(db)

    # Override the real DB dependency with the test one
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/registry/verdict-completeness")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    assert data["total_servers"] == 3
    assert data["totals"]["with_verdict"] == 3
    assert data["totals"]["with_reasoning"] == 2
    assert data["totals"]["never_assessed"] == 1
    assert len(data["missing_reasoning_examples"]) == 1

    print("PASS")