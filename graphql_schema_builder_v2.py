#!/usr/bin/env python3
"""
ZO-SENTINEL GraphQL Schema Builder v2
Exposes /graphql endpoint for registry queries using Strawberry GraphQL.
This is a dormant endpoint per spec section 9 -- not wired to any daemon.
"""

import sys
import time
import signal
import logging
from typing import Optional, List, Dict, Any

import strawberry
from strawberry.fastapi import GraphQLRouter
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, '/home/workspace/zo_sentinel')

from sentinel_cli import ws_query, ws_write
from report_formatter import color_for_verdict

# =============================================================================
# Constants
# =============================================================================
SERVICE_NAME = "graphql_schema_builder"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
LOG = logging.getLogger(__name__)

# =============================================================================
# Strawberry GraphQL Types
# =============================================================================

@strawberry.type
class MCPServer:
    server_id: str
    name: str
    url: str
    description: Optional[str]
    trust_score: Optional[float]
    verdict: Optional[str]
    registry_source: Optional[str]
    scan_count: int

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> "MCPServer":
        return cls(
            server_id=row.get("server_id", ""),
            name=row.get("name", ""),
            url=row.get("url", ""),
            description=row.get("description"),
            trust_score=row.get("trust_score"),
            verdict=row.get("verdict"),
            registry_source=row.get("registry_source"),
            scan_count=row.get("scan_count", 0)
        )


@strawberry.type
class SignalScore:
    server_id: str
    signal_name: str
    score: float
    evidence: Optional[str]
    scored_at: str

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> "SignalScore":
        return cls(
            server_id=row.get("server_id", ""),
            signal_name=row.get("signal_name", ""),
            score=row.get("score", 0.0),
            evidence=row.get("evidence"),
            scored_at=row.get("scored_at", "")
        )


@strawberry.type
class ThreatAssociation:
    server_id: str
    threat_type: str
    severity: str
    evidence: Optional[str]
    reported_at: str

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> "ThreatAssociation":
        return cls(
            server_id=row.get("server_id", ""),
            threat_type=row.get("threat_type", ""),
            severity=row.get("severity", ""),
            evidence=row.get("evidence"),
            reported_at=row.get("reported_at", "")
        )


@strawberry.type
class RiskRegisterEntry:
    server_id: str
    risk_tier: str
    risk_rank: int
    threat_count: int
    computed_at: str

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> "RiskRegisterEntry":
        return cls(
            server_id=row.get("server_id", ""),
            risk_tier=row.get("risk_tier", ""),
            risk_rank=row.get("risk_rank", 0),
            threat_count=row.get("threat_count", 0),
            computed_at=row.get("computed_at", "")
        )


@strawberry.type
class RegistrySummary:
    total_servers: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    pending_verdict_count: int
    malicious_count: int

    @classmethod
    def from_query_result(cls, rows: List[Dict[str, Any]]) -> "RegistrySummary":
        total = len(rows)
        high = sum(1 for r in rows if r.get("risk_tier") == "critical" or r.get("risk_tier") == "high")
        medium = sum(1 for r in rows if r.get("risk_tier") == "medium")
        low = sum(1 for r in rows if r.get("risk_tier") == "low")
        pending = sum(1 for r in rows if not r.get("verdict"))
        malicious = sum(1 for r in rows if r.get("verdict") in ("malicious", "suspicious"))
        return cls(
            total_servers=total,
            high_risk_count=high,
            medium_risk_count=medium,
            low_risk_count=low,
            pending_verdict_count=pending,
            malicious_count=malicious
        )


# =============================================================================
# Query Resolvers
# =============================================================================

@strawberry.type
class Query:
    @strawberry.field
    def servers(
        self,
        limit: int = 100,
        offset: int = 0,
        risk_tier: Optional[str] = None,
        verdict: Optional[str] = None,
        registry_source: Optional[str] = None
    ) -> List[MCPServer]:
        conditions = []
        params = {}
        
        if risk_tier:
            conditions.append(f"r.risk_tier = :risk_tier")
            params["risk_tier"] = risk_tier
        
        if verdict:
            conditions.append(f"s.verdict = :verdict")
            params["verdict"] = verdict
        
        if registry_source:
            conditions.append(f"s.registry_source = :registry_source")
            params["registry_source"] = registry_source
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        sql = f"""
            SELECT s.server_id, s.name, s.url, s.description, 
                   s.trust_score, s.verdict, s.registry_source, s.scan_count
            FROM mcp_server_registry s
            LEFT JOIN mcp_risk_register r ON s.server_id = r.server_id
            WHERE {where_clause}
            ORDER BY s.scan_count DESC
            LIMIT :limit OFFSET :offset
        """
        params["limit"] = limit
        params["offset"] = offset
        
        result = ws_query(sql)
        if result and "rows" in result:
            return [MCPServer.from_db_row(r) for r in result["rows"]]
        return []

    @strawberry.field
    def server(self, server_id: str) -> Optional[MCPServer]:
        sql = """
            SELECT server_id, name, url, description, 
                   trust_score, verdict, registry_source, scan_count
            FROM mcp_server_registry
            WHERE server_id = :server_id
        """
        result = ws_query(sql, {"server_id": server_id})
        if result and "rows" in result and len(result["rows"]) > 0:
            return MCPServer.from_db_row(result["rows"][0])
        return None

    @strawberry.field
    def servers_by_name(self, name_pattern: str, limit: int = 50) -> List[MCPServer]:
        sql = """
            SELECT server_id, name, url, description,
                   trust_score, verdict, registry_source, scan_count
            FROM mcp_server_registry
            WHERE LOWER(name) LIKE LOWER(:pattern)
            ORDER BY trust_score DESC NULLS LAST
            LIMIT :limit
        """
        result = ws_query(sql, {"pattern": f"%{name_pattern}%", "limit": limit})
        if result and "rows" in result:
            return [MCPServer.from_db_row(r) for r in result["rows"]]
        return []

    @strawberry.field
    def signals(self, server_id: str) -> List[SignalScore]:
        sql = """
            SELECT server_id, signal_name, score, evidence, scored_at
            FROM mcp_signal_scores
            WHERE server_id = :server_id
            ORDER BY score DESC
        """
        result = ws_query(sql, {"server_id": server_id})
        if result and "rows" in result:
            return [SignalScore.from_db_row(r) for r in result["rows"]]
        return []

    @strawberry.field
    def threats(self, server_id: str) -> List[ThreatAssociation]:
        sql = """
            SELECT server_id, threat_type, severity, evidence, reported_at
            FROM mcp_threat_associations
            WHERE server_id = :server_id
            ORDER BY reported_at DESC
        """
        result = ws_query(sql, {"server_id": server_id})
        if result and "rows" in result:
            return [ThreatAssociation.from_db_row(r) for r in result["rows"]]
        return []

    @strawberry.field
    def risk_register(self, risk_tier: Optional[str] = None, limit: int = 100) -> List[RiskRegisterEntry]:
        where = f"WHERE risk_tier = :risk_tier" if risk_tier else ""
        sql = f"""
            SELECT server_id, risk_tier, risk_rank, threat_count, computed_at
            FROM mcp_risk_register
            {where}
            ORDER BY risk_rank ASC
            LIMIT :limit
        """
        params = {"limit": limit}
        if risk_tier:
            params["risk_tier"] = risk_tier
        
        result = ws_query(sql, params)
        if result and "rows" in result:
            return [RiskRegisterEntry.from_db_row(r) for r in result["rows"]]
        return []

    @strawberry.field
    def registry_summary(self) -> RegistrySummary:
        sql = """
            SELECT s.server_id, s.verdict, r.risk_tier
            FROM mcp_server_registry s
            LEFT JOIN mcp_risk_register r ON s.server_id = r.server_id
        """
        result = ws_query(sql)
        if result and "rows" in result:
            return RegistrySummary.from_query_result(result["rows"])
        return RegistrySummary(
            total_servers=0,
            high_risk_count=0,
            medium_risk_count=0,
            low_risk_count=0,
            pending_verdict_count=0,
            malicious_count=0
        )

    @strawberry.field
    def search_servers(
        self,
        query: str,
        limit: int = 50
    ) -> List[MCPServer]:
        sql = """
            SELECT server_id, name, url, description,
                   trust_score, verdict, registry_source, scan_count
            FROM mcp_server_registry
            WHERE LOWER(name) LIKE LOWER(:q)
               OR LOWER(url) LIKE LOWER(:q)
               OR LOWER(description) LIKE LOWER(:q)
            ORDER BY trust_score DESC NULLS LAST
            LIMIT :limit
        """
        result = ws_query(sql, {"q": f"%{query}%", "limit": limit})
        if result and "rows" in result:
            return [MCPServer.from_db_row(r) for r in result["rows"]]
        return []


# =============================================================================
# FastAPI App Setup
# =============================================================================

app = FastAPI(
    title="ZO-SENTINEL GraphQL API",
    description="GraphQL endpoint for MCP server registry queries (dormant per spec section 9)",
    version="2.0.0"
)

# Build Strawberry schema
schema = strawberry.Schema(query=Query)

# Create GraphQL router with context
graphql_app = GraphQLRouter(
    schema,
    graphiql=True,
    path="/graphql"
)

# Mount GraphQL router
app.include_router(graphql_app)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": "2.0.0",
        "endpoint": "/graphql"
    }


@app.get("/")
async def root():
    """Root endpoint with GraphQL info."""
    return {
        "service": SERVICE_NAME,
        "graphql_endpoint": "/graphql",
        "graphiql": "/graphql",
        "docs": "Visit /graphql for GraphiQL interface",
        "status": "dormant"
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    LOG.error(f"GraphQL error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": str(exc)}
    )


# =============================================================================
# Daemon Functions
# =============================================================================

def check_single_instance():
    """Ensure only one instance of this service is running."""
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            LOG.error(f"Instance already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    """Remove PID file on shutdown."""
    import os
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    LOG.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    """Send service heartbeat to write_service."""
    try:
        ws_write("service_health", {
            "service": SERVICE_NAME,
            "last_heartbeat": int(time.time()),
            "port": PORT
        })
    except Exception as e:
        LOG.warning(f"Heartbeat failed: {e}")


def run():
    """Run the GraphQL server."""
    import uvicorn
    
    LOG.info(f"Starting {SERVICE_NAME} v2.0.0 on port {PORT}")
    LOG.info("GraphQL endpoint: /graphql (dormant per spec section 9)")
    
    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Check single instance
    check_single_instance()
    
    # Send initial heartbeat
    send_heartbeat()
    
    LOG.info(f"GraphiQL playground available at http://127.0.0.1:{PORT}/graphql")
    
    # Run uvicorn
    uvicorn.run(
        app,
        host='127.0.0.1',
        port=PORT,
        log_level="info"
    )


if __name__ == '__main__':
    run()