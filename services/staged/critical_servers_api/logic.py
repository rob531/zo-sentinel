from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends
from sqlalchemy import text, desc, case
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

app = FastAPI(title="critical_servers_api")

RISK_TIER_SEVERITY = {
    "CRITICAL": 1,
    "HIGH_RISK_ISOLATED": 2,
    "CAUTION_LIMITED": 3,
    "KNOWN_THREAT": 4,
    "STANDARD": 5,
    "TRUSTED": 6,
}

HIGH_RISK_TIERS = {"HIGH_RISK_ISOLATED", "CAUTION_LIMITED", "KNOWN_THREAT"}


class AxisScore(BaseModel):
    axis_name: str
    p_critical: float
    scored_at: str


class CriticalServerResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    max_p_critical: float
    axes: list[AxisScore]
    scored_at: str


class CriticalServersList(BaseModel):
    servers: list[CriticalServerResponse]
    total: int


def get_critical_servers(
    session: Session,
    limit: int = 20,
) -> list[dict]:
    severity_order = case(
        (McpServerRegistry.risk_tier == "CRITICAL", 1),
        (McpServerRegistry.risk_tier == "HIGH_RISK_ISOLATED", 2),
        (McpServerRegistry.risk_tier == "CAUTION_LIMITED", 3),
        (McpServerRegistry.risk_tier == "KNOWN_THREAT", 4),
        (McpServerRegistry.risk_tier == "STANDARD", 5),
        (McpServerRegistry.risk_tier == "TRUSTED", 6),
        else_=7,
    )

    sql = text("""
        WITH server_max_p AS (
            SELECT
                ax.server_id,
                MAX(ax.p_critical) as max_p_critical
            FROM mcp_llm_axis_scores ax
            GROUP BY ax.server_id
            HAVING MAX(ax.p_critical) >= :p_threshold
        ),
        tier_filtered AS (
            SELECT sr.server_id, sr.name, sr.risk_tier, sm.max_p_critical
            FROM mcp_server_registry sr
            INNER JOIN server_max_p sm ON sr.server_id = sm.server_id
            WHERE sr.risk_tier IN (:high_risk_isolated, :caution_limited, :known_threat)
        ),
        p_filtered AS (
            SELECT sr.server_id, sr.name, sr.risk_tier, sm.max_p_critical
            FROM mcp_server_registry sr
            INNER JOIN server_max_p sm ON sr.server_id = sm.server_id
            WHERE sr.risk_tier NOT IN (:high_risk_isolated, :caution_limited, :known_threat)
        ),
        combined AS (
            SELECT server_id, name, risk_tier, max_p_critical FROM tier_filtered
            UNION ALL
            SELECT server_id, name, risk_tier, max_p_critical FROM p_filtered
        )
        SELECT
            c.server_id,
            c.name,
            c.risk_tier,
            c.max_p_critical,
            CASE c.risk_tier
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH_RISK_ISOLATED' THEN 2
                WHEN 'CAUTION_LIMITED' THEN 3
                WHEN 'KNOWN_THREAT' THEN 4
                WHEN 'STANDARD' THEN 5
                WHEN 'TRUSTED' THEN 6
                ELSE 7
            END as tier_severity
        FROM combined c
        ORDER BY c.max_p_critical DESC, tier_severity ASC
        LIMIT :limit
    """)
    
    result = session.execute(sql, {
        "p_threshold": 0.7,
        "high_risk_isolated": "HIGH_RISK_ISOLATED",
        "caution_limited": "CAUTION_LIMITED",
        "known_threat": "KNOWN_THREAT",
        "limit": limit,
    })
    
    servers = []
    for row in result:
        servers.append({
            "server_id": row[0],
            "name": row[1],
            "risk_tier": row[2],
            "max_p_critical": float(row[3]),
        })
    
    return servers


def get_server_axes(
    session: Session,
    server_ids: list[str],
) -> dict[str, list[dict]]:
    if not server_ids:
        return {}
    
    sql = text("""
        SELECT
            ax.server_id,
            ax.axis_name,
            ax.p_critical,
            ax.scored_at
        FROM mcp_llm_axis_scores ax
        WHERE ax.server_id = ANY(:server_ids)
        ORDER BY ax.server_id, ax.axis_name
    """)
    
    result = session.execute(sql, {"server_ids": server_ids})
    
    axes_by_server: dict[str, list[dict]] = {}
    for row in result:
        sid = row[0]
        if sid not in axes_by_server:
            axes_by_server[sid] = []
        axes_by_server[sid].append({
            "axis_name": row[1],
            "p_critical": float(row[2]),
            "scored_at": row[3].isoformat() if row[3] else None,
        })
    
    return axes_by_server


@app.get("/api/servers/critical", response_model=CriticalServersList)
def get_critical_servers_endpoint(
    limit: int = 20,
    session: Session = Depends(get_session),
) -> CriticalServersList:
    servers = get_critical_servers(session, limit=limit)
    
    if not servers:
        return CriticalServersList(servers=[], total=0)
    
    server_ids = [s["server_id"] for s in servers]
    axes_by_server = get_server_axes(session, server_ids)
    
    scored_at_map: dict[str, Optional[str]] = {}
    for server_id, axes in axes_by_server.items():
        if axes:
            scored_at_map[server_id] = axes[0]["scored_at"]
        else:
            scored_at_map[server_id] = None
    
    response_servers = []
    for srv in servers:
        axes = axes_by_server.get(srv["server_id"], [])
        response_servers.append(CriticalServerResponse(
            server_id=srv["server_id"],
            name=srv["name"],
            risk_tier=srv["risk_tier"],
            max_p_critical=srv["max_p_critical"],
            axes=[AxisScore(**a) for a in axes],
            scored_at=scored_at_map.get(srv["server_id"], ""),
        ))
    
    return CriticalServersList(servers=response_servers, total=len(response_servers))


def endpoint(session: Session, limit: int = 20) -> CriticalServersList:
    return get_critical_servers_endpoint(limit=limit, session=session)


if __name__ == "__main__":
    import in_memory_store as store
    
    that_app = FastAPI()
    that_app.include_router(app.router)
    that_app.dependency_overrides[get_session] = store.get_session
    
    with that_app.test_client() as client:
        response = client.get("/api/servers/critical?limit=20")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        servers = data.get("servers", [])
        
        assert len(servers) >= 1, f"Expected at least 1 server, got {len(servers)}"
        
        max_p = -1
        max_server = None
        for s in servers:
            if s["max_p_critical"] > max_p:
                max_p = s["max_p_critical"]
                max_server = s
        
        assert servers[0]["server_id"] == max_server["server_id"], \
            f"Expected server {max_server['server_id']} first, got {servers[0]['server_id']}"
        
        print("PASS")