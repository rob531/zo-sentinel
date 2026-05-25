from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import json
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("registry_api_v2")

# Service constants
SERVICE_NAME = "registry_api_v2"
SERVICE_PORT = 8771
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
INFERENCE_ROUTER_URL = "http://127.0.0.1:8773"

# Security
security = HTTPBearer(auto_error=False)

# Pydantic Models
class ThreatPattern(BaseModel):
    pattern_id: str
    pattern_type: str
    regex: Optional[str] = None
    ioc_list: Optional[List[str]] = None
    severity: str = "medium"
    tags: Optional[List[str]] = []

class ServerRegistry(BaseModel):
    server_id: str
    hostname: str
    ip_address: str
    port: int
    service_type: str
    capabilities: Optional[List[str]] = []
    health_status: str = "active"
    metadata: Optional[Dict[str, Any]] = {}

class RegistrationRequest(BaseModel):
    server_id: str
    hostname: str
    ip_address: str
    port: int
    service_type: str
    capabilities: Optional[List[str]] = []
    metadata: Optional[Dict[str, Any]] = {}

class PatternMatchRequest(BaseModel):
    pattern_id: str
    target_server_id: str
    match_result: str
    confidence: float = 0.0
    metadata: Optional[Dict[str, Any]] = None

class HealthReport(BaseModel):
    server_id: str
    status: str
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    active_connections: Optional[int] = None
    last_heartbeat: Optional[str] = None
    error_count: Optional[int] = 0

class AuditLogEntry(BaseModel):
    event_type: str
    target_server_id: str
    source_ip: Optional[str] = None
    action: str
    result: str
    details: Optional[Dict[str, Any]] = None

# App instance
app = FastAPI(title="Registry API v2", version="2.0.0")

# In-memory registry for standalone operation
_registry: Dict[str, dict] = {}
_patterns: Dict[str, dict] = {}
_health_reports: Dict[str, dict] = {}

def get_current_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    if credentials:
        return credentials.credentials
    return "default"

def send_heartbeat(service_name: str, port: int):
    """Send heartbeat to write service"""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": service_name,
                "port": str(port),
                "last_heartbeat": datetime.utcnow().isoformat(),
                "status": "running"
            },
            "wait": True
        }
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        return response.status_code == 200
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")
        return False

def ws_write(table: str, rows: dict) -> bool:
    """Write to write_service"""
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        return response.status_code == 200
    except Exception as e:
        logger.warning(f"ws_write failed: {e}")
        return False

# Endpoints
@app.get("/health")
async def health():
    return {"status": "healthy", "service": SERVICE_NAME, "timestamp": datetime.utcnow().isoformat()}

@app.post("/registry/register")
async def register_server(request: RegistrationRequest, token: str = Depends(get_current_token)):
    """Register a server in the registry"""
    server_data = request.dict()
    server_data["registered_at"] = datetime.utcnow().isoformat()
    _registry[request.server_id] = server_data
    
    ws_write("server_registry", server_data)
    
    return {"status": "registered", "server_id": request.server_id}

@app.get("/registry/server/{server_id}")
async def get_server(server_id: str, token: str = Depends(get_current_token)):
    """Get server info by ID"""
    if server_id in _registry:
        return _registry[server_id]
    raise HTTPException(status_code=404, detail="Server not found")

@app.get("/registry/servers")
async def list_servers(
    service_type: Optional[str] = None,
    token: str = Depends(get_current_token)
):
    """List all registered servers, optionally filtered by service_type"""
    servers = list(_registry.values())
    if service_type:
        servers = [s for s in servers if s.get("service_type") == service_type]
    return {"servers": servers, "count": len(servers)}

@app.delete("/registry/server/{server_id}")
async def unregister_server(server_id: str, token: str = Depends(get_current_token)):
    """Unregister a server"""
    if server_id in _registry:
        del _registry[server_id]
        return {"status": "unregistered", "server_id": server_id}
    raise HTTPException(status_code=404, detail="Server not found")

@app.post("/patterns")
async def create_pattern(pattern: ThreatPattern, token: str = Depends(get_current_token)):
    """Create a threat detection pattern"""
    pattern_data = pattern.dict()
    pattern_data["created_at"] = datetime.utcnow().isoformat()
    _patterns[pattern.pattern_id] = pattern_data
    
    ws_write("threat_patterns", pattern_data)
    
    return {"status": "created", "pattern_id": pattern.pattern_id}

@app.get("/patterns")
async def list_patterns(token: str = Depends(get_current_token)):
    """List all threat patterns"""
    return {"patterns": list(_patterns.values()), "count": len(_patterns)}

@app.post("/patterns/match")
async def report_pattern_match(request: PatternMatchRequest, token: str = Depends(get_current_token)):
    """Report a pattern match event"""
    match_data = {
        **request.dict(),
        "reported_at": datetime.utcnow().isoformat()
    }
    ws_write("pattern_matches", match_data)
    
    return {"status": "logged", "match_id": request.pattern_id}

@app.post("/health/report")
async def report_health(health_report: HealthReport, token: str = Depends(get_current_token)):
    """Report server health status"""
    report_data = {
        **health_report.dict(),
        "reported_at": datetime.utcnow().isoformat()
    }
    _health_reports[health_report.server_id] = report_data
    
    ws_write("health_reports", report_data)
    
    return {"status": "received", "server_id": health_report.server_id}

@app.post("/audit")
async def log_audit(entry: AuditLogEntry, token: str = Depends(get_current_token)):
    """Log an audit event"""
    audit_data = {
        **entry.dict(),
        "timestamp": datetime.utcnow().isoformat()
    }
    ws_write("audit_log", audit_data)
    
    return {"status": "logged", "event_type": entry.event_type}

@app.get("/audit/events")
async def get_audit_events(
    target_server_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
    token: str = Depends(get_current_token)
):
    """Query audit log entries"""
    # In standalone mode, return empty results (would need DB connection for real data)
    return {"events": [], "count": 0, "message": "Standalone mode - audit events stored via ws_write"}

def run():
    """Run the service"""
    import uvicorn
    logger.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    send_heartbeat(SERVICE_NAME, SERVICE_PORT)
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")

if __name__ == "__main__":
    run()