#!/usr/bin/env python3
"""
ZO-SENTINEL GraphQL Schema Daemon
Provides GraphQL API for MCP server data using strawberry-graphql
"""

import sys
import os
import time
import threading
from datetime import datetime
from typing import Optional, List

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter
import strawberry

# Add workspace to path
sys.path.insert(0, '/home/workspace')

# Database service URL
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_NAME = "graphql_schema"
PORT = 8788
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

# ============================================================================
# Strawberry GraphQL Types
# ============================================================================

@strawberry.type
class SignalScore:
    """Signal score for an MCP server"""
    signal_name: str
    score: float
    evidence: Optional[str]
    scored_at: Optional[str]

@strawberry.type
class ThreatAssociation:
    """Threat association for an MCP server"""
    threat_type: str
    severity: str
    evidence: Optional[str]
    reported_at: Optional[str]

@strawberry.type
class MCPServer:
    """MCP Server type"""
    server_id: str
    name: str
    url: str
    verdict: Optional[str]
    trust_score: Optional[float]
    description: Optional[str]
    risk_tier: Optional[str]
    risk_rank: Optional[int]
    threat_count: Optional[int]
    scan_count: Optional[int]
    signals: List[SignalScore]
    threats: List[ThreatAssociation]

@strawberry.type
class Assessment:
    """Full assessment for an MCP server"""
    server: MCPServer
    signals: List[SignalScore]
    threats: List[ThreatAssociation]
    attestation: str

@strawberry.type
class ThreatSummary:
    """Threat summary type"""
    server_id: str
    server_name: str
    threat_type: str
    severity: str
    evidence: Optional[str]

@strawberry.type
class SearchResult:
    """Search result type"""
    server_id: str
    name: str
    url: str
    verdict: Optional[str]
    trust_score: Optional[float]
    description: Optional[str]

# ============================================================================
# Database Helper Functions
# ============================================================================

def _query_db(sql: str) -> dict:
    """Query database via write service"""
    import requests
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql},
            timeout=10
        )
        return response.json()
    except Exception as e:
        return {"error": str(e), "rows": [], "count": 0}

def _execute_db(sql: str) -> dict:
    """Execute database operation via write service"""
    import requests
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/execute",
            json={"sql": sql},
            timeout=10
        )
        return response.json()
    except Exception as e:
        return {"error": str(e), "ok": False}

# ============================================================================
# Data Fetching Functions
# ============================================================================

def fetch_server_by_id(server_id: str) -> Optional[dict]:
    """Fetch server details by ID"""
    result = _query_db(f"""
        SELECT s.server_id, s.name, s.url, s.verdict, s.trust_score,
               s.description, r.risk_tier, r.risk_rank, r.threat_count,
               s.scan_count
        FROM mcp_server_registry s
        LEFT JOIN mcp_risk_register r ON s.server_id = r.server_id
        WHERE s.server_id = '{server_id}'
    """)
    if result.get("rows") and len(result["rows"]) > 0:
        return result["rows"][0]
    return None

def fetch_server_signals(server_id: str) -> List[SignalScore]:
    """Fetch signal scores for a server"""
    result = _query_db(f"""
        SELECT signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
    """)
    signals = []
    for row in result.get("rows", []):
        signals.append(SignalScore(
            signal_name=row.get("signal_name", ""),
            score=float(row.get("score", 0)),
            evidence=row.get("evidence"),
            scored_at=row.get("scored_at")
        ))
    return signals

def fetch_server_threats(server_id: str) -> List[ThreatAssociation]:
    """Fetch threat associations for a server"""
    result = _query_db(f"""
        SELECT threat_type, severity, evidence, reported_at
        FROM mcp_threat_associations
        WHERE server_id = '{server_id}'
    """)
    threats = []
    for row in result.get("rows", []):
        threats.append(ThreatAssociation(
            threat_type=row.get("threat_type", ""),
            severity=row.get("severity", ""),
            evidence=row.get("evidence"),
            reported_at=row.get("reported_at")
        ))
    return threats

def row_to_mcp_server(row: dict, signals: List[SignalScore] = None,
                      threats: List[ThreatAssociation] = None) -> MCPServer:
    """Convert database row to MCPServer type"""
    return MCPServer(
        server_id=row.get("server_id", ""),
        name=row.get("name", ""),
        url=row.get("url", ""),
        verdict=row.get("verdict"),
        trust_score=float(row.get("trust_score")) if row.get("trust_score") is not None else None,
        description=row.get("description"),
        risk_tier=row.get("risk_tier"),
        risk_rank=int(row.get("risk_rank")) if row.get("risk_rank") is not None else None,
        threat_count=int(row.get("threat_count")) if row.get("threat_count") is not None else None,
        scan_count=int(row.get("scan_count")) if row.get("scan_count") is not None else None,
        signals=signals or [],
        threats=threats or []
    )

def fetch_servers(verdict: Optional[str] = None, risk_tier: Optional[str] = None,
                  limit: int = 50) -> List[MCPServer]:
    """Fetch servers with optional filters"""
    conditions = []
    if verdict:
        conditions.append(f"s.verdict = '{verdict}'")
    if risk_tier:
        conditions.append(f"r.risk_tier = '{risk_tier}'")
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    result = _query_db(f"""
        SELECT s.server_id, s.name, s.url, s.verdict, s.trust_score,
               s.description, r.risk_tier, r.risk_rank, r.threat_count,
               s.scan_count
        FROM mcp_server_registry s
        LEFT JOIN mcp_risk_register r ON s.server_id = r.server_id
        WHERE {where_clause}
        ORDER BY r.risk_rank NULLS LAST, s.trust_score DESC
        LIMIT {limit}
    """)
    
    servers = []
    for row in result.get("rows", []):
        server_id = row.get("server_id")
        signals = fetch_server_signals(server_id)
        threats = fetch_server_threats(server_id)
        servers.append(row_to_mcp_server(row, signals, threats))
    return servers

def search_servers(q: str, limit: int = 50) -> List[SearchResult]:
    """Search servers by name, URL, or description"""
    search_term = q.replace("'", "''")
    result = _query_db(f"""
        SELECT server_id, name, url, verdict, trust_score, description
        FROM mcp_server_registry
        WHERE name ILIKE '%{search_term}%'
           OR url ILIKE '%{search_term}%'
           OR description ILIKE '%{search_term}%'
        ORDER BY trust_score DESC NULLS LAST
        LIMIT {limit}
    """)
    
    results = []
    for row in result.get("rows", []):
        results.append(SearchResult(
            server_id=row.get("server_id", ""),
            name=row.get("name", ""),
            url=row.get("url", ""),
            verdict=row.get("verdict"),
            trust_score=float(row.get("trust_score")) if row.get("trust_score") is not None else None,
            description=row.get("description")
        ))
    return results

def fetch_threats_by_severity(severity: Optional[str] = None,
                              limit: int = 100) -> List[ThreatSummary]:
    """Fetch threat associations with optional severity filter"""
    severity_filter = f"AND ta.severity = '{severity}'" if severity else ""
    
    result = _query_db(f"""
        SELECT ta.server_id, s.name as server_name, ta.threat_type,
               ta.severity, ta.evidence
        FROM mcp_threat_associations ta
        JOIN mcp_server_registry s ON ta.server_id = s.server_id
        WHERE 1=1 {severity_filter}
        ORDER BY 
            CASE ta.severity
                WHEN 'critical' THEN 1
                WHEN 'high' THEN 2
                WHEN 'medium' THEN 3
                WHEN 'low' THEN 4
                ELSE 5
            END,
            ta.reported_at DESC
        LIMIT {limit}
    """)
    
    threats = []
    for row in result.get("rows", []):
        threats.append(ThreatSummary(
            server_id=row.get("server_id", ""),
            server_name=row.get("server_name", ""),
            threat_type=row.get("threat_type", ""),
            severity=row.get("severity", ""),
            evidence=row.get("evidence")
        ))
    return threats

def generate_attestation(server: MCPServer) -> str:
    """Generate attestation string for a server"""
    if not server.verdict:
        return "No verdict available"
    
    if server.verdict == "approved":
        return f"Verified MCP server with trust score {server.trust_score or 'N/A'}"
    elif server.verdict == "flagged":
        return f"Flagged server - {server.threat_count or 0} threats detected"
    elif server.verdict == "banned":
        return "BANNED - Server poses significant risk"
    else:
        return "Assessment pending"

# ============================================================================
# GraphQL Query Resolvers
# ============================================================================

@strawberry.type
class Query:
    @strawberry.field
    def server(self, id: str) -> Optional[MCPServer]:
        """Get a single server by ID"""
        row = fetch_server_by_id(id)
        if not row:
            return None
        signals = fetch_server_signals(id)
        threats = fetch_server_threats(id)
        return row_to_mcp_server(row, signals, threats)
    
    @strawberry.field
    def servers(self, verdict: Optional[str] = None,
                risk_tier: Optional[str] = None,
                limit: int = 50) -> List[MCPServer]:
        """Get list of servers with optional filters"""
        return fetch_servers(verdict, risk_tier, min(limit, 100))
    
    @strawberry.field
    def search(self, q: str, limit: int = 50) -> List[SearchResult]:
        """Search servers by query string"""
        return search_servers(q, min(limit, 100))
    
    @strawberry.field
    def threats(self, severity: Optional[str] = None,
                limit: int = 100) -> List[ThreatSummary]:
        """Get threat associations with optional severity filter"""
        return fetch_threats_by_severity(severity, min(limit, 200))
    
    @strawberry.field
    def assessment(self, server_id: str) -> Optional[Assessment]:
        """Get full assessment for a server"""
        row = fetch_server_by_id(server_id)
        if not row:
            return None
        signals = fetch_server_signals(server_id)
        threats = fetch_server_threats(server_id)
        server = row_to_mcp_server(row, signals, threats)
        attestation = generate_attestation(server)
        return Assessment(
            server=server,
            signals=signals,
            threats=threats,
            attestation=attestation
        )

@strawberry.type
class Mutation:
    @strawberry.mutation
    def health_check(self) -> str:
        """Health check mutation"""
        return "ok"

# ============================================================================
# FastAPI Application
# ============================================================================

# Create Strawberry schema
schema = strawberry.Schema(query=Query, mutation=Mutation)

# Create FastAPI app
app = FastAPI(title="ZO-SENTINEL GraphQL API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create GraphQL router
graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")

# Health check endpoint
@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "uptime": get_uptime()}

@app.get("/")
async def root():
    return {
        "service": "ZO-SENTINEL GraphQL API",
        "version": "1.0.0",
        "graphql_endpoint": "/graphql",
        "graphql_playground": "/graphql"
    }

# ============================================================================
# Service Daemon Functions
# ============================================================================

start_time = time.time()

def get_uptime() -> float:
    """Get service uptime in seconds"""
    return time.time() - start_time

def check_single_instance():
    """Ensure only one instance is running"""
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            print(f"ERROR: Service already running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ProcessLookupError):
            pass
    
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def send_heartbeat():
    """Send heartbeat to write service"""
    import requests
    try:
        requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "service_health",
                "rows": {
                    "service": SERVICE_NAME,
                    "last_heartbeat": datetime.utcnow().isoformat()
                },
                "wait": True
            },
            timeout=5
        )
    except Exception:
        pass

def run():
    """Main service loop"""
    check_single_instance()
    print(f"Starting {SERVICE_NAME} on port {PORT}")
    print(f"GraphQL endpoint: http://127.0.0.1:{PORT}/graphql")
    
    # Start heartbeat thread
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    # Run uvicorn server
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="info"
    )

def _heartbeat_loop():
    """Heartbeat loop running in separate thread"""
    while True:
        send_heartbeat()
        time.sleep(30)

if __name__ == "__main__":
    run()