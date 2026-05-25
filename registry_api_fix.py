from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import requests
import logging
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("registry_api")

app = FastAPI(title="ZO-SENTINEL MCP Registry API")

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
INFERENCE_ROUTER_URL = "http://127.0.0.1:8773/route"

# Pydantic models
class MCPResource(BaseModel):
    name: str
    type: str
    endpoint: str
    capabilities: list[str]
    tags: list[str] = []

class ServiceRegistration(BaseModel):
    service_id: str
    service_name: str
    service_type: str
    host: str
    port: int
    health_check_url: str
    tags: list[str] = []

class MCPServerRegistration(BaseModel):
    server_id: str
    server_name: str
    version: str
    protocol: str
    port: int
    capabilities: list[str]
    security_level: str
    tags: list[str] = []


def write_to_db(table: str, rows: dict) -> bool:
    """Write to database via write_service at :8772"""
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"DB write failed for {table}: {e}")
        return False


def query_from_db(query: str, params: list = None) -> list:
    """Query database via read_service (placeholder - use direct endpoint)"""
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query, "params": params or []},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        logger.error(f"DB query failed: {e}")
        return []


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        resp = requests.get(f"http://127.0.0.1:8772/health", timeout=5)
        return {"status": "healthy", "write_service": "connected" if resp.status_code == 200 else "disconnected"}
    except:
        return {"status": "healthy", "write_service": "unknown"}


@app.get("/registry/mcp-servers")
async def list_mcp_servers(
    protocol: str = Query(None),
    security_level: str = Query(None),
    tag: str = Query(None),
    limit: int = Query(100, ge=1, le=1000)
):
    """List registered MCP servers"""
    conditions = []
    params = []
    
    if protocol:
        conditions.append("protocol = ?")
        params.append(protocol)
    if security_level:
        conditions.append("security_level = ?")
        params.append(security_level)
    if tag:
        conditions.append("? IN (SELECT value FROM unnest(tags))")
        params.append(tag)
    
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM mcp_server_registry{where_clause} LIMIT {limit}"
    
    results = query_from_db(query, params)
    return {"servers": results, "count": len(results)}


@app.post("/registry/mcp-servers")
async def register_mcp_server(server: MCPServerRegistration):
    """Register a new MCP server"""
    server_data = server.model_dump()
    server_data["registered_at"] = "NOW()"
    server_data["status"] = "active"
    
    success = write_to_db("mcp_server_registry", server_data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to register MCP server")
    
    return {"status": "registered", "server_id": server.server_id}


@app.get("/registry/mcp-servers/{server_id}")
async def get_mcp_server(server_id: str):
    """Get MCP server details by ID"""
    query = f"SELECT * FROM mcp_server_registry WHERE server_id = ? LIMIT 1"
    results = query_from_db(query, [server_id])
    
    if not results:
        raise HTTPException(status_code=404, detail="MCP server not found")
    
    return results[0]


@app.delete("/registry/mcp-servers/{server_id}")
async def unregister_mcp_server(server_id: str):
    """Unregister an MCP server"""
    write_to_db("mcp_server_registry", {
        "server_id": server_id,
        "status": "unregistered",
        "unregistered_at": "NOW()"
    })
    return {"status": "unregistered", "server_id": server_id}


@app.get("/registry/mcp-resources")
async def list_mcp_resources(
    resource_type: str = Query(None),
    tag: str = Query(None),
    limit: int = Query(100, ge=1, le=1000)
):
    """List MCP resources"""
    conditions = []
    params = []
    
    if resource_type:
        conditions.append("resource_type = ?")
        params.append(resource_type)
    if tag:
        conditions.append("? IN (SELECT value FROM unnest(tags))")
        params.append(tag)
    
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM mcp_resources{where_clause} LIMIT {limit}"
    
    results = query_from_db(query, params)
    return {"resources": results, "count": len(results)}


@app.post("/registry/mcp-resources")
async def register_mcp_resource(resource: MCPResource):
    """Register a new MCP resource"""
    resource_data = resource.model_dump()
    resource_data["registered_at"] = "NOW()"
    
    success = write_to_db("mcp_resources", resource_data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to register MCP resource")
    
    return {"status": "registered", "name": resource.name}


@app.get("/registry/services")
async def list_services(
    service_type: str = Query(None),
    tag: str = Query(None),
    limit: int = Query(100, ge=1, le=1000)
):
    """List registered services"""
    conditions = []
    params = []
    
    if service_type:
        conditions.append("service_type = ?")
        params.append(service_type)
    if tag:
        conditions.append("? IN (SELECT value FROM unnest(tags))")
        params.append(tag)
    
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM service_registry{where_clause} LIMIT {limit}"
    
    results = query_from_db(query, params)
    return {"services": results, "count": len(results)}


@app.post("/registry/services")
async def register_service(service: ServiceRegistration):
    """Register a new service"""
    service_data = service.model_dump()
    service_data["registered_at"] = "NOW()"
    service_data["status"] = "active"
    
    success = write_to_db("service_registry", service_data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to register service")
    
    return {"status": "registered", "service_id": service.service_id}


@app.get("/registry/services/{service_id}")
async def get_service(service_id: str):
    """Get service details by ID"""
    query = f"SELECT * FROM service_registry WHERE service_id = ? LIMIT 1"
    results = query_from_db(query, [service_id])
    
    if not results:
        raise HTTPException(status_code=404, detail="Service not found")
    
    return results[0]


@app.get("/registry/search")
async def search_registry(
    query: str = Query(..., min_length=1),
    category: str = Query(None)
):
    """Search across all registry tables"""
    results = []
    
    if category is None or category == "servers":
        servers = query_from_db(
            f"SELECT * FROM mcp_server_registry WHERE server_id LIKE ? OR server_name LIKE ? LIMIT 50",
            [f"%{query}%", f"%{query}%"]
        )
        results.extend([{"type": "server", "data": s} for s in servers])
    
    if category is None or category == "services":
        services = query_from_db(
            f"SELECT * FROM service_registry WHERE service_id LIKE ? OR service_name LIKE ? LIMIT 50",
            [f"%{query}%", f"%{query}%"]
        )
        results.extend([{"type": "service", "data": s} for s in services])
    
    return {"results": results, "count": len(results)}


@app.post("/audit/events")
async def log_audit_event(
    event_type: str,
    actor: str,
    target: str,
    action: str,
    result: str,
    metadata: dict = None
):
    """Log an audit event"""
    audit_record = {
        "event_type": event_type,
        "actor": actor,
        "target_server_id": target,
        "action": action,
        "result": result,
        "timestamp": "NOW()",
        "metadata": metadata or {}
    }
    
    success = write_to_db("audit_log", audit_record)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to log audit event")
    
    return {"status": "logged", "event_type": event_type}


def run():
    """Start the registry API server"""
    logger.info("Starting ZO-SENTINEL Registry API on port 8774")
    uvicorn.run(app, host="0.0.0.0", port=8774, log_level="info")


if __name__ == "__main__":
    run()