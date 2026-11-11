from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_registry_scan_lanes as _get_registry_scan_lanes
except ImportError:
    try:
        from .logic import get_scan_lanes as _get_registry_scan_lanes
    except ImportError:
        try:
            from .logic import get_scan_lane_summary as _get_registry_scan_lanes
        except ImportError:
            _get_registry_scan_lanes = None


router = APIRouter(prefix="/api", tags=["registry-scan-lanes"])


class RegistryScanLane(BaseModel):
    registry_source: Optional[str] = None
    servers: int = 0
    with_verdict: int = 0
    with_risk_tier: int = 0
    avg_confidence: float = 0.0
    oldest_first_seen: Optional[datetime] = None
    newest_last_seen: Optional[datetime] = None


class RegistryScanTotals(BaseModel):
    servers: int = 0
    sources: int = 0


class RegistryScanLanesResponse(BaseModel):
    lanes: List[RegistryScanLane] = []
    totals: RegistryScanTotals = RegistryScanTotals()


def _read_scan_lanes(session: Session) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if _get_registry_scan_lanes is not None:
        result = _get_registry_scan_lanes(session)
    else:
        rows = session.execute(
            text(
                """
                SELECT
                    registry_source,
                    COUNT(*) AS servers,
                    COUNT(DISTINCT CASE
                        WHEN verdict IS NOT NULL THEN server_id
                    END) AS with_verdict,
                    COUNT(DISTINCT CASE
                        WHEN risk_tier IS NOT NULL THEN server_id
                    END) AS with_risk_tier,
                    COALESCE(AVG(COALESCE(confidence, 0)), 0) AS avg_confidence,
                    MIN(first_seen) AS oldest_first_seen,
                    MAX(last_seen) AS newest_last_seen
                FROM mcp_server_registry
                GROUP BY registry_source
                ORDER BY registry_source
                """
            )
        ).mappings()
        result = [dict(row) for row in rows]

    if isinstance(result, dict):
        lanes = result.get("lanes", [])
        totals = result.get("totals")
    elif isinstance(result, tuple) and len(result) == 2:
        lanes, totals = result
    else:
        lanes, totals = result, None

    normalized_lanes: List[Dict[str, Any]] = []
    for lane in lanes or []:
        if isinstance(lane, dict):
            normalized_lanes.append(dict(lane))
        elif hasattr(lane, "model_dump"):
            normalized_lanes.append(lane.model_dump())
        elif hasattr(lane, "_asdict"):
            normalized_lanes.append(dict(lane._asdict()))
        elif hasattr(lane, "__dict__"):
            normalized_lanes.append(
                {
                    key: value
                    for key, value in vars(lane).items()
                    if not key.startswith("_")
                }
            )
        else:
            normalized_lanes.append(
                {
                    "registry_source": lane[0],
                    "servers": lane[1],
                    "with_verdict": lane[2],
                    "with_risk_tier": lane[3],
                    "avg_confidence": lane[4],
                    "oldest_first_seen": lane[5],
                    "newest_last_seen": lane[6],
                }
            )

    if isinstance(totals, dict):
        normalized_totals = dict(totals)
    else:
        normalized_totals = {}

    normalized_totals.setdefault(
        "servers",
        sum(int(lane.get("servers") or 0) for lane in normalized_lanes),
    )
    normalized_totals.setdefault("sources", len(normalized_lanes))

    return normalized_lanes, normalized_totals


@router.get(
    "/registry/scan-lanes",
    response_model=RegistryScanLanesResponse,
    summary="List registry scan-lane summaries",
)
def list_registry_scan_lanes(
    session: Session = Depends(get_session),
) -> RegistryScanLanesResponse:
    lanes, totals = _read_scan_lanes(session)
    return RegistryScanLanesResponse(
        lanes=[RegistryScanLane(**lane) for lane in lanes],
        totals=RegistryScanTotals(**totals),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    id INTEGER PRIMARY KEY,
                    confidence FLOAT,
                    description TEXT,
                    first_seen DATETIME,
                    last_assessed DATETIME,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    meta TEXT,
                    name TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    scan_count INTEGER,
                    server_id TEXT,
                    trust_score FLOAT,
                    url TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    confidence, description, first_seen, last_assessed,
                    last_scanned, last_seen, meta, name, registry_source,
                    risk_tier, scan_count, server_id, trust_score, url,
                    verdict, verdict_reasoning
                ) VALUES
                    (
                        0.90, 'first', '2024-01-01 00:00:00', '2024-01-02 00:00:00',
                        '2024-01-02 00:00:00', '2024-01-03 00:00:00', '{}', 'one',
                        'alpha', 'high', 2, 'server-1', 0.9, 'http://one',
                        'safe', 'ok'
                    ),
                    (
                        0.70, 'second', '2024-02-01 00:00:00', '2024-02-02 00:00:00',
                        '2024-02-02 00:00:00', '2024-02-03 00:00:00', '{}', 'two',
                        'alpha', NULL, 1, 'server-2', 0.7, 'http://two',
                        NULL, NULL
                    ),
                    (
                        0.80, 'third', '2024-03-01 00:00:00', '2024-03-02 00:00:00',
                        '2024-03-02 00:00:00', '2024-03-03 00:00:00', '{}', 'three',
                        'beta', 'low', 3, 'server-3', 0.8, 'http://three',
                        'safe', 'ok'
                    )
                """
            )
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/scan-lanes")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["lanes"]) == 2
    assert sum(lane["servers"] for lane in payload["lanes"]) == 3
    assert payload["totals"]["servers"] == 3
    assert payload["totals"]["sources"] == 2
    print("PASS")