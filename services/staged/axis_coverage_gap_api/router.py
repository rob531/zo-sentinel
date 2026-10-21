from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from .logic import AXIS_NAMES as _LOGIC_AXIS_NAMES
except (ImportError, AttributeError):
    _LOGIC_AXIS_NAMES = None


_DEFAULT_AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

if _LOGIC_AXIS_NAMES is not None and set(_LOGIC_AXIS_NAMES) == set(_DEFAULT_AXIS_NAMES):
    AXIS_NAMES = tuple(_DEFAULT_AXIS_NAMES)
else:
    AXIS_NAMES = _DEFAULT_AXIS_NAMES


class AxisCount(BaseModel):
    scored: int
    missing: int


class TopGap(BaseModel):
    server_id: str
    risk_tier: Optional[str]
    missing_axes: List[str]
    last_scored: Optional[datetime]


class CoverageGapsResponse(BaseModel):
    total_servers: int
    servers_with_all_axes: int
    axis_counts: Dict[str, AxisCount]
    top_gaps: List[TopGap]


router = APIRouter(prefix="/api")


@router.get(
    "/axis/coverage-gaps",
    response_model=CoverageGapsResponse,
)
def get_axis_coverage_gaps(
    limit: int = Query(default=50, ge=1),
    tier: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> CoverageGapsResponse:
    limit = min(limit, 200)

    query = text(
        """
        SELECT
            r.server_id AS server_id,
            r.risk_tier AS risk_tier,
            s.axis_name AS axis_name,
            s.scored_at AS scored_at
        FROM mcp_server_registry AS r
        LEFT JOIN mcp_llm_axis_scores AS s
            ON s.server_id = r.server_id
        """
    )

    if tier is None:
        rows = session.execute(query).mappings().all()
    else:
        rows = session.execute(
            query + text(" WHERE r.risk_tier = :tier"),
            {"tier": tier},
        ).mappings().all()

    servers = {}
    for row in rows:
        server_id = str(row["server_id"])
        server = servers.setdefault(
            server_id,
            {
                "server_id": server_id,
                "risk_tier": row["risk_tier"],
                "axes": set(),
                "last_scored": None,
            },
        )

        axis_name = row["axis_name"]
        if axis_name in AXIS_NAMES:
            server["axes"].add(axis_name)

        scored_at = row["scored_at"]
        if scored_at is not None:
            if server["last_scored"] is None or scored_at > server["last_scored"]:
                server["last_scored"] = scored_at

    axis_counts = {
        axis_name: {"scored": 0, "missing": 0}
        for axis_name in AXIS_NAMES
    }

    servers_with_all_axes = 0
    gaps = []

    for server in servers.values():
        scored_axes = server["axes"]
        for axis_name in AXIS_NAMES:
            if axis_name in scored_axes:
                axis_counts[axis_name]["scored"] += 1
            else:
                axis_counts[axis_name]["missing"] += 1

        if len(scored_axes.intersection(AXIS_NAMES)) == len(AXIS_NAMES):
            servers_with_all_axes += 1
        else:
            missing_axes = [
                axis_name
                for axis_name in AXIS_NAMES
                if axis_name not in scored_axes
            ]
            gaps.append(
                {
                    "server_id": server["server_id"],
                    "risk_tier": server["risk_tier"],
                    "missing_axes": missing_axes,
                    "last_scored": server["last_scored"],
                    "missing_count": len(missing_axes),
                }
            )

    gaps.sort(key=lambda item: (-item["missing_count"], item["server_id"]))
    gaps = gaps[:limit]

    for gap in gaps:
        gap.pop("missing_count", None)

    return CoverageGapsResponse(
        total_servers=len(servers),
        servers_with_all_axes=servers_with_all_axes,
        axis_counts=axis_counts,
        top_gaps=gaps,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as test_session:
        test_session.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    risk_tier VARCHAR,
                    last_assessed TIMESTAMP
                )
                """
            )
        )
        test_session.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    scored_at TIMESTAMP
                )
                """
            )
        )

        test_session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, risk_tier, last_assessed)
                VALUES
                    ('server-complete', 'TIER_A', CURRENT_TIMESTAMP),
                    ('server-partial', 'TIER_A', CURRENT_TIMESTAMP),
                    ('server-unscored', 'TIER_A', CURRENT_TIMESTAMP)
                """
            )
        )

        for index, axis_name in enumerate(AXIS_NAMES):
            test_session.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (server_id, axis_name, scored_at)
                    VALUES (:server_id, :axis_name, CURRENT_TIMESTAMP)
                    """
                ),
                {"server_id": "server-complete", "axis_name": axis_name},
            )

        for axis_name in AXIS_NAMES[:3]:
            test_session.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (server_id, axis_name, scored_at)
                    VALUES (:server_id, :axis_name, CURRENT_TIMESTAMP)
                    """
                ),
                {"server_id": "server-partial", "axis_name": axis_name},
            )

        test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/coverage-gaps")
        assert response.status_code == 200
        payload = response.json()
        assert set(payload["axis_counts"]) == set(AXIS_NAMES)
        assert len(payload["axis_counts"]) == 7
        unscored = next(
            gap
            for gap in payload["top_gaps"]
            if gap["server_id"] == "server-unscored"
        )
        assert len(unscored["missing_axes"]) == 7

    print("PASS")