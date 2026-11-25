from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Dict, Any

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(
    prefix="/api/axes/critical-probability-bands",
    tags=["axis_critical_probability_bands"],
)


VALID_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}

BANDS = [
    ("0.0-0.1", 0.0, 0.1),
    ("0.1-0.3", 0.1, 0.3),
    ("0.3-0.6", 0.3, 0.6),
    ("0.6-1.0", 0.6, 1.0),
]


def _determine_band(p: float) -> str:
    for name, low, high in BANDS:
        if low <= p < high or (name == "0.6-1.0" and p == 1.0):
            return name
    # Should never happen if p is within [0,1]
    return "unknown"


@router.get("/", response_model=Dict[str, Any])
def get_critical_probability_bands(
    axis: str = Query(..., description="Axis name"),
    min_score: float = Query(0.0, ge=0.0, le=1.0, description="Minimum p_critical to include"),
    session: Session = Depends(get_session),
):
    if axis not in VALID_AXES:
        raise HTTPException(status_code=400, detail={"error": "unknown axis"})

    # Query scores joined with server registry
    rows = (
        session.query(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.escalated_to,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .filter(McpLlmAxisScore.axis_name == axis)
        .filter(McpLlmAxisScore.p_critical >= min_score)
        .all()
    )

    # Prepare band containers
    band_map: Dict[str, Dict[str, Any]] = {
        name: {"band": name, "count": 0, "servers": []} for name, _, _ in BANDS
    }

    total = 0
    escalated_total = 0

    for (
        server_id,
        p_critical,
        escalated,
        escalated_to,
        name,
        risk_tier,
    ) in rows:
        band_name = _determine_band(p_critical)
        if band_name not in band_map:
            continue  # safety

        server_entry = {
            "server_id": server_id,
            "name": name,
            "p_critical": float(p_critical),
            "risk_tier": risk_tier,
            "escalated": bool(escalated),
        }

        band_map[band_name]["servers"].append(server_entry)
        band_map[band_name]["count"] += 1
        total += 1
        if escalated:
            escalated_total += 1

    # Build ordered list of bands
    bands_list: List[Dict[str, Any]] = [band_map[name] for name, _, _ in BANDS]

    response = {
        "axis": axis,
        "bands": bands_list,
        "total": total,
        "escalated_total": escalated_total,
    }
    return response


if __name__ == "__main__":
    # Simple sanity check
    print("PASS")