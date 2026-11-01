from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")

AXIS_NAMES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


@router.get("/axis/matrix")
def get_axis_matrix(session: Session = Depends(get_session)):
    # Load all servers
    servers = session.query(McpServerRegistry).all()
    server_map = {
        s.server_id: {
            "server_id": str(s.server_id),
            "name": s.name,
            "risk_tier": s.risk_tier,
            "axes": {},
        }
        for s in servers
    }

    # Load all axis scores for the defined axes
    scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))
        .all()
    )
    for score in scores:
        sid = score.server_id
        if sid not in server_map:
            continue
        server_map[sid]["axes"][score.axis_name] = {
            "label": score.label,
            "p_top": float(score.p_top) if score.p_top is not None else None,
            "escalated": bool(score.escalated) if score.escalated is not None else False,
            "escalated_to": score.escalated_to,
        }

    missing_axis_servers = sum(
        1 for srv in server_map.values() if len(srv["axes"]) != len(AXIS_NAMES)
    )

    return {
        "servers": list(server_map.values()),
        "axis_names": AXIS_NAMES,
        "missing_axis_servers": missing_axis_servers,
    }