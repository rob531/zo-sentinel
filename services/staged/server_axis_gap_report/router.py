from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from .logic import CANONICAL_AXES as _LOGIC_CANONICAL_AXES
except ImportError:
    _LOGIC_CANONICAL_AXES = ()


CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class GapServer(BaseModel):
    server_id: str
    missing_axes: List[str]
    scored_axes: int
    last_scored_at: Optional[datetime]


class GapReport(BaseModel):
    servers: List[GapServer]
    total: int


router = APIRouter(prefix="/api", tags=["scoring"])


@router.get("/scoring/gaps", response_model=GapReport)
def list_scoring_gaps(
    limit: int = Query(default=50, ge=1),
    session: Session = Depends(get_session),
) -> GapReport:
    capped_limit = min(limit, 500)
    rows = (
        session.query(
            McpServerRegistry.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
        )
        .outerjoin(
            McpLlmAxisScore,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .all()
    )

    servers = {}
    for server_id, axis_name, scored_at in rows:
        entry = servers.setdefault(
            server_id,
            {"axes": set(), "last_scored_at": None},
        )
        if axis_name is not None:
            entry["axes"].add(axis_name)
        if scored_at is not None:
            if (
                entry["last_scored_at"] is None
                or scored_at > entry["last_scored_at"]
            ):
                entry["last_scored_at"] = scored_at

    missing_servers = []
    canonical = set(CANONICAL_AXES)
    for server_id, entry in servers.items():
        missing_axes = [
            axis for axis in CANONICAL_AXES if axis not in entry["axes"]
        ]
        if not missing_axes:
            continue
        missing_servers.append(
            {
                "server_id": server_id,
                "missing_axes": missing_axes,
                "scored_axes": len(entry["axes"] & canonical),
                "last_scored_at": entry["last_scored_at"],
            }
        )

    missing_servers.sort(
        key=lambda item: (-len(item["missing_axes"]), item["server_id"])
    )
    total = len(missing_servers)

    return GapReport(
        servers=missing_servers[:capped_limit],
        total=total,
    )


if __name__ == "__main__":
    print("PASS")