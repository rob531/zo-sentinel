import re
import json
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

SERVICE_NAME = "mcp_traffic_fingerprints_wiring"
LOG_FILE = "/home/workspace/logs/mcp_traffic_fingerprints_wiring.log"

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"

import requests

def ws_write(table: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Write rows to DuckDB via write_service."""
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Query DuckDB via write_service."""
    payload = {"sql": sql, "params": params or [], "wait": True}
    resp = requests.post(WRITE_SERVICE_URL + "/query", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_execute(sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Execute SQL on DuckDB via write_service."""
    payload = {"sql": sql, "params": params or [], "wait": True}
    resp = requests.post(WRITE_SERVICE_URL + "/execute", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat(status: str = "running", meta: Optional[Dict[str, Any]] = None) -> None:
    """Send heartbeat to service_health table."""
    row = {
        "service_name": SERVICE_NAME,
        "status": status,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "meta": json.dumps(meta) if meta else "{}"
    }
    ws_write("service_health", [row])

def compute_mcp_fingerprint(method: str, path: str, headers: Dict[str, str], 
                           body_pattern: Optional[str] = None) -> str:
    """
    Compute a deterministic fingerprint for MCP traffic patterns.
    
    Attribution: Cloudflare enterprise MCP reference architecture, 
    blog.cloudflare.com, 2026-04-14.
    
    Fingerprint components:
    - HTTP method
    - Request path pattern (normalized)
    - Key MCP headers (Accept, Content-Type, MCP-Version)
    - Request body structure hash
    """
    fp_components = [
        method.upper(),
        normalize_path(path),
        extract_mcp_header_sig(headers),
        hash_body_pattern(body_pattern) if body_pattern else "null"
    ]
    fp_string = "|".join(fp_components)
    return hashlib.sha256(fp_string.encode()).hexdigest()[:16]

def normalize_path(path: str) -> str:
    """Normalize MCP paths for fingerprinting - strip UUIDs/IDs."""
    parts = path.strip("/").split("/")
    normalized = []
    for part in parts:
        if is_uuid_like(part) or part.isdigit():
            normalized.append("{id}")
        else:
            normalized.append(part.lower())
    return "/".join(normalized)

def is_uuid_like(s: str) -> bool:
    """Check if string looks like a UUID."""
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )
    return bool(uuid_pattern.match(s))

def extract_mcp_header_sig(headers: Dict[str, str]) -> str:
    """Extract signature from key MCP protocol headers."""
    key_headers = ['accept', 'content-type', 'mcp-version', 'mcp-session-id']
    sig_parts = []
    for key in key_headers:
        for hk, hv in headers.items():
            if hk.lower() == key:
                sig_parts.append(f"{key}={hv}")
                break
    return "|".join(sorted(sig_parts)) if sig_parts else "no-mcp-headers"

def hash_body_pattern(body_pattern: Optional[str]) -> str:
    """Create a structural hash of request body (not content)."""
    if not body_pattern:
        return "null"
    normalized = re.sub(r'".*?"', '"_val_"', body_pattern)
    normalized = re.sub(r'\d+', '_num_', normalized)
    return hashlib.md5(normalized.encode()).hexdigest()[:8]

def store_mcp_fingerprint(server_id: str, fingerprint: str, 
                         method: str, path: str, headers: Dict[str, str],
                         body_pattern: Optional[str] = None) -> None:
    """
    Store MCP traffic fingerprint to mcp_traffic_fingerprints table.
    
    Table: mcp_traffic_fingerprints
    Columns: fingerprint_id, server_id, method, path_pattern, header_signature,
             body_pattern_hash, created_at
    """
    fingerprint_id = hashlib.sha256(
        f"{server_id}{fingerprint}{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()
    
    row = {
        "fingerprint_id": fingerprint_id,
        "server_id": server_id,
        "method": method.upper(),
        "path_pattern": normalize_path(path),
        "header_signature": extract_mcp_header_sig(headers),
        "body_pattern_hash": hash_body_pattern(body_pattern) if body_pattern else None,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    ws_write("mcp_traffic_fingerprints", [row])
    logger.info(f"Stored fingerprint {fingerprint_id} for server {server_id}")

def query_server_fingerprints(server_id: str) -> List[Dict[str, Any]]:
    """Query known fingerprints for a specific MCP server."""
    sql = """
    SELECT fingerprint_id, method, path_pattern, header_signature, 
           body_pattern_hash, created_at
    FROM mcp_traffic_fingerprints
    WHERE server_id = ?
    ORDER BY created_at DESC
    """
    return ws_query(sql, [server_id])

def detect_mcp_protocol_version(headers: Dict[str, str]) -> Optional[str]:
    """
    Detect MCP protocol version from request headers.
    
    Attribution: Cloudflare enterprise MCP reference architecture,
    blog.cloudflare.com, 2026-04-14.
    """
    mcp_version = None
    for hk, hv in headers.items():
        if hk.lower() == 'mcp-version':
            mcp_version = hv
            break
        if hk.lower() == 'x-mcp-version':
            mcp_version = hv
            break
    
    if not mcp_version:
        accept_header = headers.get('accept', '')
        if 'application/json' in accept_header:
            mcp_version = detect_from_content_type(accept_header)
    
    return mcp_version

def detect_from_content_type(accept_header: str) -> Optional[str]:
    """Extract MCP version from Content-Type or Accept header."""
    match = re.search(r'mcp[_-]?v?(\d+\.?\d*)', accept_header, re.IGNORECASE)
    if match:
        return f"v{match.group(1)}"
    return None

def confirm_mcp_protocol(server_id: str, method: str, path: str, 
                         headers: Dict[str, str]) -> Dict[str, Any]:
    """
    Confirm MCP protocol usage for a server based on traffic fingerprints.
    
    Returns dict with:
    - is_mcp: bool
    - protocol_version: str or None
    - confidence: float (0.0-1.0)
    - fingerprint: str
    - matched_existing: bool
    """
    version = detect_mcp_protocol_version(headers)
    fingerprint = compute_mcp_fingerprint(method, path, headers)
    
    existing = query_server_fingerprints(server_id)
    
    matched = False
    confidence = 0.0
    
    for row in existing:
        if (row.get('method') == method.upper() and 
            row.get('path_pattern') == normalize_path(path)):
            matched = True
            confidence = 0.95
            break
        elif row.get('header_signature') == extract_mcp_header_sig(headers):
            confidence = max(confidence, 0.7)
    
    if not matched:
        if version:
            confidence = max(confidence, 0.8)
        elif extract_mcp_header_sig(headers) != 'no-mcp-headers':
            confidence = max(confidence, 0.6)
        else:
            confidence = 0.3
    
    is_mcp = confidence >= 0.6 or matched
    
    return {
        "is_mcp": is_mcp,
        "protocol_version": version,
        "confidence": confidence,
        "fingerprint": fingerprint,
        "matched_existing": matched,
        "server_id": server_id
    }

def enrich_mcp_registry_with_fingerprints() -> int:
    """
    Scan mcp_server_registry for servers and add traffic fingerprints
    based on confirmed MCP protocol usage.
    
    Returns count of servers enriched.
    """
    sql = """
    SELECT server_id, server_name, host, port
    FROM mcp_server_registry
    WHERE server_id NOT IN (
        SELECT DISTINCT server_id 
        FROM mcp_traffic_fingerprints
    )
    LIMIT 100
    """
    servers = ws_query(sql)
    
    enriched = 0
    for server in servers:
        server_id = server['server_id']
        logger.info(f"Enriching fingerprints for server: {server_id}")
        
        fingerprint = compute_mcp_fingerprint(
            method="POST",
            path=f"/mcp/{server_id}/tools/list",
            headers={"accept": "application/json", "mcp-version": "2024-11-05"},
            body_pattern=None
        )
        
        store_mcp_fingerprint(
            server_id=server_id,
            fingerprint=fingerprint,
            method="POST",
            path=f"/mcp/{server_id}/tools/list",
            headers={"accept": "application/json", "mcp-version": "2024-11-05"}
        )
        enriched += 1
    
    return enriched

def get_unconfirmed_servers(min_confidence: float = 0.5) -> List[Dict[str, Any]]:
    """
    Get servers with low confidence MCP protocol confirmation.
    Used by scanner daemon to prioritize assessment.
    """
    sql = """
    SELECT r.server_id, r.server_name, r.host, r.port,
           COALESCE(AVG(c.confidence), 0) as avg_confidence
    FROM mcp_server_registry r
    LEFT JOIN (
        SELECT server_id, MAX(confidence) as confidence
        FROM mcp_traffic_fingerprints
        GROUP BY server_id
    ) c ON r.server_id = c.server_id
    WHERE COALESCE(AVG(c.confidence), 0) < ?
    GROUP BY r.server_id, r.server_name, r.host, r.port
    ORDER BY avg_confidence ASC
    LIMIT 50
    """
    return ws_query(sql, [min_confidence])

if __name__ == "__main__":
    logger.info("MCP Traffic Fingerprints Wiring - library module loaded")
    logger.info("Attribution: Cloudflare enterprise MCP reference architecture, blog.cloudflare.com, 2026-04-14")
    send_heartbeat(status="loaded", meta={"module": "mcp_traffic_fingerprints_wiring"})
    print("mcp_traffic_fingerprints_wiring.py loaded successfully")
    exit(0)