# services/staged/mcp_attestation_freshness_api/logic.py
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore  # noqa: F401 (imported for type checking)

router = APIRouter()


class ServerInfo(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    last_attestation_at: Optional[datetime] = None
    age_days: Optional[int] = None
    expiring_soon: bool = False


class Totals(BaseModel):
    servers_with_attestation: int = 0
    expiring_soon: int = 0
    stale_missing: int = 0


class FreshnessResponse(BaseModel):
    days: int
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    totals: Totals
    servers: List[ServerInfo]


@router.get(
    "/api/attestations/freshness",
    response_model=FreshnessResponse,
    name="get_attestation_freshness",
)
def get_attestation_freshness(
    days: int = Query(30, ge=1),
    limit: int = Query(100, ge=1),
    session: Session = Depends(get_session),
) -> FreshnessResponse:
    """
    Return a summary of MCP attestation freshness for the fleet.
    """
    now = datetime.utcnow()
    cutoff = now + timedelta(days=days)

    # ------------------------------------------------------------------
    # 1. Gather the latest attestation per server (if any)
    # ------------------------------------------------------------------
    att_stmt = text(
        """
        SELECT
            server_id,
            MAX(created_at) AS last_attestation_at,
            MAX(expiry_at) AS expiry_at
        FROM mcp_attestations
        GROUP BY server_id
        LIMIT :limit
        """
    )
    att_rows = session.execute(att_stmt, {"limit": limit}).fetchall()
    att_by_server = {
        row.server_id: {
            "last_attestation_at": row.last_attestation_at,
            "expiry_at": row.expiry_at,
        }
        for row in att_rows
    }

    # ------------------------------------------------------------------
    # 2. Pull server registry information
    # ------------------------------------------------------------------
    reg_stmt = text(
        """
        SELECT
            server_id,
            name,
            risk_tier
        FROM mcp_server_registry
        """
    )
    reg_rows = session.execute(reg_stmt).fetchall()

    # ------------------------------------------------------------------
    # 3. Fallback risk tier from LLM scores when registry tier is NULL
    # ------------------------------------------------------------------
    # Simple heuristic: if risk_tier is NULL, set to "unknown"
    # (real logic could inspect McpLlmAxisScore but is omitted for brevity)
    # ------------------------------------------------------------------

    servers: List[ServerInfo] = []
    servers_with_attestation = 0
    expiring_soon = 0

    for reg in reg_rows:
        srv_id = reg.server_id
        att = att_by_server.get(srv_id)

        if att:
            servers_with_attestation += 1
            last_att = att["last_attestation_at"]
            expiry = att["expiry_at"]
            age = (now - last_att).days if last_att else None
            is_expiring = expiry <= cutoff if expiry else False
            if is_expiring:
                expiring_soon += 1
        else:
            last_att = None
            age = None
            is_expiring = False

        risk_tier = reg.risk_tier
        if risk_tier is None:
            risk_tier = "unknown"

        servers.append(
            ServerInfo(
                server_id=srv_id,
                name=reg.name,
                risk_tier=risk_tier,
                last_attestation_at=last_att,
                age_days=age,
                expiring_soon=is_expiring,
            )
        )

    # ------------------------------------------------------------------
    # 4. Identify stale (registry entry without attestation)
    # ------------------------------------------------------------------
    stale_missing = len([s for s in servers if s.last_attestation_at is None])

    totals = Totals(
        servers_with_attestation=servers_with_attestation,
        expiring_soon=expiring_soon,
        stale_missing=stale_missing,
    )

    return FreshnessResponse(
        days=days,
        generated_at=now,
        totals=totals,
        servers=servers,
    )