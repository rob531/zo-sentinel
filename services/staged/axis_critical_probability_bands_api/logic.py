"""Critical probability-band computation over the real application data layer."""

from __future__ import annotations

from typing import Any, Dict, List, Union

from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry

LEGAL_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}

BAND_DEFINITIONS = (
    ("0.0-0.1", 0.0, 0.1),
    ("0.1-0.3", 0.1, 0.3),
    ("0.3-0.6", 0.3, 0.6),
    ("0.6-1.0", 0.6, 1.0),
)


def _band_for(p_critical: float) -> str:
    if p_critical < 0.1:
        return "0.0-0.1"
    if p_critical < 0.3:
        return "0.1-0.3"
    if p_critical < 0.6:
        return "0.3-0.6"
    return "0.6-1.0"


def get_critical_probability_bands(
    db: Session, axis_name: str
) -> Union[Dict[str, Any], JSONResponse]:
    """Return server counts grouped into fixed p_critical bands."""
    if axis_name not in LEGAL_AXES:
        return JSONResponse(status_code=400, content={"error": "unknown axis"})

    rows = db.execute(
        select(McpLlmAxisScore, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(
            McpLlmAxisScore.axis_name == axis_name,
            McpLlmAxisScore.p_critical.is_not(None),
        )
        .order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.id)
    ).all()

    bands: Dict[str, List[Dict[str, Any]]] = {
        band: [] for band, _lower, _upper in BAND_DEFINITIONS
    }
    escalated_total = 0

    for score, server in rows:
        p_critical = float(score.p_critical)
        if p_critical < 0.0 or p_critical > 1.0:
            continue

        escalated = bool(score.escalated)
        if escalated:
            escalated_total += 1

        bands[_band_for(p_critical)].append(
            {
                "server_id": str(score.server_id),
                "name": str(server.name or ""),
                "p_critical": p_critical,
                "risk_tier": server.risk_tier if server.risk_tier is not None else None,
                "escalated": escalated,
            }
        )

    band_results = [
        {
            "band": band,
            "count": len(bands[band]),
            "servers": bands[band],
        }
        for band, _lower, _upper in BAND_DEFINITIONS
    ]

    return {
        "axis": axis_name,
        "bands": band_results,
        "total": sum(item["count"] for item in band_results),
        "escalated_total": escalated_total,
    }


def get_axis_critical_probability_bands(
    db: Session, axis_name: str
) -> Union[Dict[str, Any], JSONResponse]:
    return get_critical_probability_bands(db, axis_name)


def list_critical_probability_bands(
    db: Session, axis_name: str
) -> Union[Dict[str, Any], JSONResponse]:
    return get_critical_probability_bands(db, axis_name)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        test_engine,
        tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__],
    )

    with Session(test_engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Low Risk Server",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Medium Risk Server",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="High Risk Server",
                    risk_tier="high",
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="auth_strength",
                    model_version="test-model",
                    p_critical=0.05,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-2",
                    axis_name="auth_strength",
                    model_version="test-model",
                    p_critical=0.4,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-3",
                    axis_name="auth_strength",
                    model_version="test-model",
                    p_critical=0.9,
                    escalated=True,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with Session(test_engine) as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/axes/critical-probability-bands")
    def critical_probability_bands(
        axis: str,
        db: Session = Depends(get_session),
    ):
        return get_critical_probability_bands(db, axis)

    client = TestClient(test_app)
    response = client.get(
        "/api/axes/critical-probability-bands",
        params={"axis": "auth_strength"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["escalated_total"] == 1
    high_band = next(
        band for band in payload["bands"] if band["band"] == "0.6-1.0"
    )
    assert high_band["count"] == 1

    invalid = client.get(
        "/api/axes/critical-probability-bands",
        params={"axis": "bogus_axis"},
    )
    assert invalid.status_code == 400
    assert invalid.json() == {"error": "unknown axis"}

    print("PASS")