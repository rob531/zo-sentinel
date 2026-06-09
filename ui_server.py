# wire_admin_pages_ui_server: applied
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import hashlib
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path
import json

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
LOGS_DIR = PROJECT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "ui_server.log"

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_SERVICE = "http://127.0.0.1:8772"
WRITE_URL = f"{WRITE_SERVICE}/write"
QUERY_URL = f"{QUERY_SERVICE}/query"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"

PUBLIC_ROUTES = {"/health", "/app/", "/openapi.json", "/docs", "/redoc"}
PUBLIC_API_SUBMISSIONS = {"POST"}

SERVICE_NAME = "ui_server"
PORT = 8790
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

_token_cache = {}

def log(msg):
    ts = datetime.utcnow().isoformat()
    line = f"{ts} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def ws_query(sql: str):
    try:
        import requests
        r = requests.post(QUERY_URL, json={"sql": sql}, timeout=15)
        return r.json()
    except Exception as e:
        log(f"ws_query error: {e}")
        return {"rows": [], "count": 0}

def ws_write(table: str, rows: list):
    try:
        import requests
        r = requests.post(WRITE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=15)
        return r.json()
    except Exception as e:
        log(f"ws_write error: {e}")
        return {"ok": False}

def ws_execute(sql: str):
    try:
        import requests
        r = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=15)
        return r.json()
    except Exception as e:
        log(f"ws_execute error: {e}")
        return {"ok": False}

def generate_token_id() -> str:
    raw = secrets.token_hex(32) + str(time.time())
    return hashlib.sha256(raw.encode()).hexdigest()[:48]

def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()

def is_route_public(path: str, method: str) -> bool:
    for prefix in PUBLIC_ROUTES:
        if path.startswith(prefix):
            return True
    if path == "/api/submissions" and method in PUBLIC_API_SUBMISSIONS:
        return True
    return False

def verify_token(token_id: str) -> Optional[dict]:
    if token_id in _token_cache:
        cached = _token_cache[token_id]
        if cached.get("expires_at_iso") > utc_now_iso():
            return cached
        else:
            if token_id in _token_cache:
                del _token_cache[token_id]
            return None
    
    result = ws_query(
        "SELECT token_id, action, mcp_name, submission_id, admin_email, expires_at, used, used_at, scope "
        "FROM auth_tokens WHERE token_id = ? AND used = false AND expires_at > ?",
        params=[token_id, utc_now_iso()]
    )
    rows = result.get("rows", [])
    if not rows:
        return None
    
    row = rows[0]
    expires_at_iso = row.get("expires_at", "")[:19] if row.get("expires_at") else ""
    token_data = {
        "token_id": row["token_id"],
        "admin_email": row.get("admin_email", ""),
        "scope": row.get("scope", "analyst"),
        "expires_at_iso": expires_at_iso,
        "action": row.get("action", ""),
        "mcp_name": row.get("mcp_name", "")
    }
    _token_cache[token_id] = token_data
    return token_data

def check_admin_scope(token_data: dict) -> bool:
    return token_data.get("scope") == "admin"

def migrate_auth_tokens_table():
    try:
        result = ws_query("SELECT column_name FROM information_schema.columns WHERE table_name = 'auth_tokens'")
        columns = [r["column_name"] for r in result.get("rows", [])]
        
        if "scope" not in columns:
            ws_execute("ALTER TABLE auth_tokens ADD COLUMN scope VARCHAR DEFAULT 'analyst'")
            log("Added scope column to auth_tokens")
        
        if "created_at" not in columns:
            ws_execute("ALTER TABLE auth_tokens ADD COLUMN created_at TIMESTAMP DEFAULT now()")
            log("Added created_at column to auth_tokens")
            
    except Exception as e:
        log(f"migrate_auth_tokens_table error: {e}")

def auth_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method
    
    if is_route_public(path, method):
        return call_next(request)
    
    if not path.startswith("/api/"):
        return call_next(request)
    
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "Missing or invalid Authorization header"}
        )
    
    token_id = auth_header[7:]
    if not token_id:
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "Empty token"}
        )
    
    token_data = verify_token(token_id)
    if not token_data:
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "Invalid or expired token"}
        )
    
    request.state.token_data = token_data
    return call_next(request)

def create_app():
    app = FastAPI(title="Zo Sentinel UI Server", version="2.0.0")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    app.middleware(auth_middleware)
    
    migrate_auth_tokens_table()
    
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": SERVICE_NAME, "port": PORT}
    
    @app.get("/api/auth/check")
    async def auth_check(request: Request):
        token_data = getattr(request.state, "token_data", None)
        if token_data:
            return {
                "authenticated": True,
                "email": token_data.get("admin_email"),
                "scope": token_data.get("scope"),
                "expires_at": token_data.get("expires_at_iso")
            }
        return {"authenticated": False}
    
    @app.post("/api/auth/token")
    async def create_token(request: Request, body: dict = {}):
        action = body.get("action", "api_access")
        mcp_name = body.get("mcp_name")
        admin_email = body.get("admin_email", "")
        scope = body.get("scope", "analyst")
        expiry_days = body.get("expiry_days", 30)
        
        if scope not in ("admin", "analyst", "viewer"):
            scope = "analyst"
        
        token_id = generate_token_id()
        
        if expiry_days == 0:
            expires_at = datetime.utcnow() + timedelta(days=36500)
        elif expiry_days == -1:
            expires_at = datetime.utcnow() + timedelta(days=30)
        else:
            expires_at = datetime.utcnow() + timedelta(days=expiry_days)
        
        expires_at_iso = expires_at.isoformat()
        
        ws_write("auth_tokens", [{
            "token_id": token_id,
            "action": action,
            "mcp_name": mcp_name,
            "submission_id": None,
            "admin_email": admin_email,
            "expires_at": expires_at_iso,
            "used": False,
            "used_at": None,
            "scope": scope,
            "created_at": utc_now_iso()
        }])
        
        log(f"Token created for {admin_email} with scope {scope}")
        
        return {
            "token_id": token_id,
            "expires_at": expires_at_iso,
            "scope": scope,
            "admin_email": admin_email,
            "warning": "Store this token securely. It will not be shown again."
        }
    
    @app.get("/api/auth/tokens")
    async def list_tokens(request: Request):
        token_data = getattr(request.state, "token_data", None)
        if not token_data or not check_admin_scope(token_data):
            raise HTTPException(status_code=403, detail="Admin scope required")
        
        result = ws_query(
            "SELECT token_id, action, mcp_name, admin_email, expires_at, used, used_at, scope, created_at "
            "FROM auth_tokens ORDER BY created_at DESC LIMIT 100"
        )
        
        tokens = []
        for row in result.get("rows", []):
            masked_token = row["token_id"][:8] + "..." + row["token_id"][-4:]
            tokens.append({
                "token_id_masked": masked_token,
                "token_id": row["token_id"],
                "action": row.get("action"),
                "mcp_name": row.get("mcp_name"),
                "admin_email": row.get("admin_email"),
                "expires_at": row.get("expires_at"),
                "used": row.get("used"),
                "scope": row.get("scope", "analyst"),
                "created_at": row.get("created_at"),
                "is_active": not row.get("used", True) and row.get("expires_at", "") > utc_now_iso()
            })
        
        return {"tokens": tokens, "count": len(tokens)}
    
    @app.delete("/api/auth/tokens/{token_id}")
    async def revoke_token(request: Request, token_id: str):
        token_data = getattr(request.state, "token_data", None)
        if not token_data or not check_admin_scope(token_data):
            raise HTTPException(status_code=403, detail="Admin scope required")
        
        ws_execute("UPDATE auth_tokens SET used = true, used_at = ? WHERE token_id = ?", params=[utc_now_iso(), token_id])
        
        if token_id in _token_cache:
            del _token_cache[token_id]
        
        log(f"Token revoked by {token_data.get('admin_email')}")
        
        return {"ok": True, "message": "Token revoked"}
    
    @app.get("/api/servers")
    async def get_servers(request: Request):
        limit = request.query_params.get("limit", 50)
        offset = request.query_params.get("offset", 0)
        verdict = request.query_params.get("verdict")
        
        where_clause = ""
        params = []
        if verdict:
            where_clause = "WHERE verdict = ?"
            params = [verdict]
        
        sql = f"""
            SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count, last_scan
            FROM mcp_server_registry
            {where_clause}
            ORDER BY trust_score DESC
            LIMIT {int(limit)} OFFSET {int(offset)}
        """
        result = ws_query(sql, params=params)
        return {"servers": result.get("rows", []), "count": result.get("count", 0)}
    
    @app.get("/api/servers/{server_id}")
    async def get_server_detail(request: Request, server_id: str):
        result = ws_query(
            "SELECT * FROM mcp_server_registry WHERE server_id = ?", params=[server_id]
        )
        rows = result.get("rows", [])
        if not rows:
            raise HTTPException(status_code=404, detail="Server not found")
        
        signals = ws_query(
            "SELECT signal_name, score, evidence, scored_at FROM mcp_signal_scores WHERE server_id = ?",
            params=[server_id]
        )
        
        return {
            "server": rows[0],
            "signals": signals.get("rows", [])
        }
    
    @app.get("/api/dashboard/summary")
    async def get_summary(request: Request):
        total = ws_query("SELECT COUNT(*) as c FROM mcp_server_registry")
        high_risk = ws_query("SELECT COUNT(*) as c FROM mcp_risk_register WHERE risk_tier = 'high'")
        medium_risk = ws_query("SELECT COUNT(*) as c FROM mcp_risk_register WHERE risk_tier = 'medium'")
        
        verdict_dist = ws_query("""
            SELECT verdict, COUNT(*) as cnt FROM mcp_server_registry GROUP BY verdict
        """)
        
        return {
            "total_servers": total.get("rows", [{"c": 0}])[0]["c"],
            "high_risk": high_risk.get("rows", [{"c": 0}])[0]["c"],
            "medium_risk": medium_risk.get("rows", [{"c": 0}])[0]["c"],
            "verdict_distribution": verdict_dist.get("rows", [])
        }
    
    @app.get("/api/submissions")
    async def list_submissions(request: Request):
        result = ws_query("""
            SELECT submission_id, server_name, url, submitted_by, status, verdict, created_at
            FROM mesh_events WHERE event_type = 'submission'
            ORDER BY created_at DESC LIMIT 100
        """)
        return {"submissions": result.get("rows", [])}
    
    @app.post("/api/submissions")
    async def create_submission(request: Request, body: dict = {}):
        import uuid
        submission_id = str(uuid.uuid4())
        
        ws_write("mesh_events", [{
            "event_id": submission_id,
            "event_type": "submission",
            "server_name": body.get("server_name", ""),
            "url": body.get("url", ""),
            "submitted_by": body.get("submitted_by", "anonymous"),
            "status": "pending",
            "verdict": None,
            "created_at": utc_now_iso()
        }])
        
        return {"submission_id": submission_id, "status": "pending"}
    
    @app.get("/api/attestations")
    async def list_attestations(request: Request):
        result = ws_query("""
            SELECT server_id, server_name, attested_at, expires_at, status
            FROM mcp_attestations
            ORDER BY attested_at DESC LIMIT 100
        """)
        return {"attestations": result.get("rows", [])}
    
    @app.get("/api/audit-log")
    async def get_audit_log(request: Request):
        limit = request.query_params.get("limit", 100)
        result = ws_query(f"""
            SELECT id, target_server_id, event_type, actor, detail, created_at
            FROM audit_log
            ORDER BY created_at DESC
            LIMIT {int(limit)}
        """)
        return {"events": result.get("rows", [])}
    
    @app.get("/api/policies")
    async def list_policies(request: Request):
        result = ws_query("""
            SELECT policy_id, name, description, rule_json, active, created_at
            FROM policy_rules
            ORDER BY created_at DESC
        """)
        return {"policies": result.get("rows", [])}
    
    # Admin HTML page routes (PRODUCT_SPEC Appendix A)
    @app.get("/admin/exemptions")
    async def admin_exemptions():
        html_path = PROJECT_DIR / "admin_exemptions.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(), media_type="text/html")
        return JSONResponse(status_code=404, content={"error": "Not found"})
    
    @app.get("/admin/policies")
    async def admin_policies():
        html_path = PROJECT_DIR / "admin_policies.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(), media_type="text/html")
        return JSONResponse(status_code=404, content={"error": "Not found"})
    
    @app.get("/admin/submissions")
    async def admin_submissions():
        html_path = PROJECT_DIR / "admin_submissions.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(), media_type="text/html")
        return JSONResponse(status_code=404, content={"error": "Not found"})
    
    return app

def run():
    log(f"Starting {SERVICE_NAME} on port {PORT}")
    import os
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    
    import signal
    def signal_handler(sig, frame):
        log("Received shutdown signal")
        try:
            os.remove(PID_FILE)
        except:
            pass
        exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")

if __name__ == "__main__":
    run()