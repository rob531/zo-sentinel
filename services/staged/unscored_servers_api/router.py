# services/staged/unscored_servers_api/logic.py
from typing import List, Optional
from pydantic import BaseModel
import requests


class UnscoredServer(BaseModel):
    server_id: str
    name: str
    registry_source: str
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


class UnscoredServersResponse(BaseModel):
    total: int
    servers: List[UnscoredServer]


def get_unscored_servers() -> UnscoredServersResponse:
    """Query servers in registry that have no overall_risk axis score."""
    sql = """
        SELECT 
            r.server_id,
            r.name,
            r.registry_source,
            r.first_seen,
            r.last_seen
        FROM mcp_server_registry r
        LEFT JOIN mcp_llm_axis_scores s 
            ON r.server_id = s.server_id 
            AND s.axis_name = %s
        WHERE s.id IS NULL
        ORDER BY r.first_seen DESC
    """
    
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": ["overall_risk"]},
        timeout=30
    )
    response.raise_for_status()
    result = response.json()
    
    servers = [UnscoredServer(**row) for row in result.get("rows", [])]
    
    return UnscoredServersResponse(total=len(servers), servers=servers)