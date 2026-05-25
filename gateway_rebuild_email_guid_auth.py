import os
import sys
import logging
import signal
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Header, Body
from pydantic import BaseModel
import uvicorn
import requests

# ============================================================================
# SERVICE CONFIGURATION
# ============================================================================
SERVICE_NAME = "gateway_email_guid_auth"
PORT = 8775
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

# ============================================================================
# LOGGING SETUP
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"{SERVICE_NAME}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# FASTAPI APPLICATION
# ============================================================================
app = FastAPI(title=SERVICE_NAME, version="1.0.0")

# ============================================================================
# PYDANTIC MODELS
# ============================================================================
class AuthTokenRequest(BaseModel):
    email: str
    purpose: str = "access"


class AuthTokenResponse(BaseModel):
    token_id: str
    expires_at: str
    message: str


class TokenValidationRequest(BaseModel):
    token: str


class ValidateResponse(BaseModel):
    valid: bool
    email: Optional[str] = None
    purpose: Optional[str] = None


class SignalScoreRequest(BaseModel):
    server_id: str
    email: str
    signal_data: Dict[str, Any]


class SignalScoreResponse(BaseModel):
    server_id: str
    signal_name: str
    score: float
    confidence: float
    evidence_blob: Dict[str, Any]
    computed_at: str


class HealthResponse(BaseModel):
    status: str
    service: str
    uptime: float


# ============================================================================
# APPLICATION STATE
# ============================================================================
start_time = time.time()


# ============================================================================
# HELPER FUNCTIONS - Write Service Integration
# ============================================================================
def ws_query(sql: str, params: Optional[List] = None) -> Dict[str, Any]:
    """Query write_service for data retrieval."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_query failed: {e}")
        raise HTTPException(status_code=503, detail=f"WriteService unavailable: {e}")


def ws_write(sql: str, params: Optional[List] = None) -> Dict[str, Any]:
    """Execute write via write_service (uses /query endpoint with DML)."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_write failed: {e}")
        raise HTTPException(status_code=503, detail=f"WriteService unavailable: {e}")


def send_heartbeat() -> None:
    """Send service heartbeat to service_health table."""
    now = datetime.now(timezone.utc).isoformat()
    uptime = time.time() - start_time
    meta = {"uptime_seconds": round(uptime, 2), "pid": os.getpid()}
    sql = """
    INSERT INTO service_health (service, last_heartbeat, status, meta)
    VALUES (?, ?, 'running', ?)
    ON CONFLICT (service) DO UPDATE SET
        last_heartbeat = excluded.last_heartbeat,
        status = excluded.status,
        meta = excluded.meta
    """
    try:
        ws_write(sql, [SERVICE_NAME, now, str(meta)])
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def compute_signal_score(server_id: str, email: str, signal_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute signal score for email GUID auth signal.
    
    Signal type: email_guid_verification
    Computes confidence based on:
    - Token existence and validity
    - Email domain reputation
    - Submission history patterns
    """
    import hashlib
    
    # Normalize inputs
    email_lower = email.lower().strip()
    server_id_normalized = server_id.strip()
    
    # Generate deterministic signal_id
    signal_content = f"{server_id_normalized}:{email_lower}:email_guid"
    signal_id = hashlib.sha256(signal_content.encode()).hexdigest()[:16]
    
    # Query auth_tokens for evidence
    evidence_blob = {
        "signal_type": "email_guid_verification",
        "email_domain": email_lower.split("@")[-1] if "@" in email_lower else "unknown",
        "token_submissions": 0,
        "valid_tokens": 0,
        "expired_tokens": 0,
        "recent_activity": False,
        "domain_age_days": None,
    }
    
    confidence = 0.0
    score = 0.5  # Neutral baseline
    
    try:
        # Check token history for this email
        token_sql = """
        SELECT 
            COUNT(*) as total_tokens,
            SUM(CASE WHEN used = 1 THEN 1 ELSE 0 END) as used_tokens,
            SUM(CASE WHEN expires_at > NOW() AND used = 0 THEN 1 ELSE 0 END) as valid_tokens,
            SUM(CASE WHEN expires_at <= NOW() AND used = 0 THEN 1 ELSE 0 END) as expired_tokens,
            MAX(created_at) as last_activity
        FROM auth_tokens 
        WHERE admin_email = ?
        """
        result = ws_query(token_sql, [email_lower])
        
        if result.get("rows") and len(result["rows"]) > 0:
            row = result["rows"][0]
            total_tokens = row.get("total_tokens", 0) or 0
            valid_tokens = row.get("valid_tokens", 0) or 0
            expired_tokens = row.get("expired_tokens", 0) or 0
            last_activity = row.get("last_activity")
            
            evidence_blob["token_submissions"] = total_tokens
            evidence_blob["valid_tokens"] = valid_tokens
            evidence_blob["expired_tokens"] = expired_tokens
            
            # Compute confidence based on token history
            if total_tokens > 0:
                confidence = min(0.9, 0.3 + (valid_tokens * 0.2) + (total_tokens * 0.05))
                score = 0.5 + (valid_tokens * 0.1) - (expired_tokens * 0.05)
                
            if last_activity:
                from datetime import datetime, timezone
                try:
                    last_dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    days_since = (now - last_dt).days
                    evidence_blob["recent_activity"] = days_since <= 7
                    if days_since <= 7:
                        confidence = min(0.95, confidence + 0.1)
                except Exception:
                    pass
            
            # Check for signal_data enrichment
            if signal_data:
                if signal_data.get("domain_verified"):
                    confidence = min(0.95, confidence + 0.15)
                    score = min(1.0, score + 0.2)
                if signal_data.get("guid_exists"):
                    confidence = min(0.95, confidence + 0.1)
                if signal_data.get("submission_quality") == "high":
                    score = min(1.0, score + 0.1)
    except Exception as e:
        logger.warning(f"Token history check failed: {e}")
    
    # Clamp values
    score = max(0.0, min(1.0, score))
    confidence = max(0.0, min(1.0, confidence))
    
    return {
        "server_id": server_id_normalized,
        "signal_name": "email_guid_verification",
        "score": round(score, 4),
        "confidence": round(confidence, 4),
        "evidence_blob": evidence_blob,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "signal_id": signal_id
    }


# ============================================================================
# API ENDPOINTS
# ============================================================================
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    uptime = time.time() - start_time
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": round(uptime, 2)
    }


@app.get("/")
async def root():
    """Root endpoint with service info."""
    uptime = time.time() - start_time
    return {
        "service": SERVICE_NAME,
        "version": "1.0.0",
        "port": PORT,
        "uptime": round(uptime, 2),
        "endpoints": [
            "GET /health",
            "POST /api/auth/token",
            "POST /api/auth/validate",
            "POST /api/auth/revoke",
            "POST /api/signal/compute"
        ]
    }


@app.post("/api/auth/token", response_model=AuthTokenResponse)
async def generate_auth_token(request: AuthTokenRequest, authorization: Optional[str] = Header(None)):
    """Generate an authentication token for email access."""
    # Basic auth check - in production this would verify admin privileges
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    email = request.email.lower().strip()
    purpose = request.purpose.strip()
    
    # Validate email format
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    import hashlib
    import secrets
    
    # Generate token
    token_content = f"{email}:{purpose}:{time.time()}:{secrets.token_hex(16)}"
    token_id = hashlib.sha256(token_content.encode()).hexdigest()
    
    # Set expiration (24 hours)
    expires_at = datetime.now(timezone.utc)
    expires_at = expires_at.replace(hour=expires_at.hour + 24)
    expires_at_str = expires_at.isoformat()
    
    # Store token in auth_tokens table
    now = datetime.now(timezone.utc).isoformat()
    sql = """
    INSERT INTO auth_tokens (token_id, action, mcp_name, submission_id, admin_email, expires_at, used, created_at)
    VALUES (?, ?, ?, ?, ?, ?, 0, ?)
    """
    try:
        ws_write(sql, [token_id, purpose, "gateway_email_guid_auth", None, email, expires_at_str, now])
        logger.info(f"Generated token for {email}, purpose={purpose}")
        return AuthTokenResponse(
            token_id=token_id,
            expires_at=expires_at_str,
            message="Token generated successfully"
        )
    except Exception as e:
        logger.error(f"Token generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate token")


@app.post("/api/auth/validate", response_model=ValidateResponse)
async def validate_token(request: TokenValidationRequest):
    """Validate an authentication token."""
    token = request.token.strip()
    
    if not token:
        raise HTTPException(status_code=400, detail="Token required")
    
    sql = """
    SELECT token_id, admin_email, action, expires_at, used
    FROM auth_tokens
    WHERE token_id = ?
    LIMIT 1
    """
    
    try:
        result = ws_query(sql, [token])
        
        if not result.get("rows") or len(result["rows"]) == 0:
            return ValidateResponse(valid=False)
        
        row = result["rows"][0]
        expires_at_str = row.get("expires_at")
        used = row.get("used", 0)
        
        # Check expiration
        if expires_at_str:
            expires_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires_dt:
                return ValidateResponse(valid=False, email=row.get("admin_email"))
        
        if used == 1:
            return ValidateResponse(valid=False, email=row.get("admin_email"))
        
        return ValidateResponse(
            valid=True,
            email=row.get("admin_email"),
            purpose=row.get("action")
        )
    except Exception as e:
        logger.error(f"Token validation failed: {e}")
        raise HTTPException(status_code=500, detail="Validation error")


@app.post("/api/auth/revoke")
async def revoke_token(request: TokenValidationRequest, authorization: Optional[str] = Header(None)):
    """Revoke an authentication token (mark as used)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    token = request.token.strip()
    
    sql = """
    UPDATE auth_tokens
    SET used = 1, used_at = ?
    WHERE token_id = ?
    """
    
    try:
        result = ws_write(sql, [datetime.now(timezone.utc).isoformat(), token])
        logger.info(f"Token revoked: {token[:16]}...")
        return {"status": "ok", "message": "Token revoked"}
    except Exception as e:
        logger.error(f"Token revocation failed: {e}")
        raise HTTPException(status_code=500, detail="Revocation error")


@app.post("/api/signal/compute", response_model=SignalScoreResponse)
async def compute_signal(request: SignalScoreRequest, authorization: Optional[str] = Header(None)):
    """
    Compute email GUID verification signal score for a server.
    
    Request body:
    - server_id: The MCP server identifier
    - email: Email associated with the submission
    - signal_data: Optional enrichment data
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    if not request.server_id:
        raise HTTPException(status_code=400, detail="server_id required")
    
    if not request.email:
        raise HTTPException(status_code=400, detail="email required")
    
    # Compute the signal score
    result = compute_signal_score(request.server_id, request.email, request.signal_data or {})
    
    # Store in mcp_signal_scores table
    sql = """
    INSERT INTO mcp_signal_scores (
        server_id, signal_name, score, evidence, computed_at
    ) VALUES (?, ?, ?, ?, ?)
    """
    
    try:
        ws_write(sql, [
            result["server_id"],
            result["signal_name"],
            result["score"],
            str(result["evidence_blob"]),
            result["computed_at"]
        ])
        logger.info(f"Signal computed for {request.server_id}: score={result['score']}")
    except Exception as e:
        logger.warning(f"Signal storage failed (may be duplicate): {e}")
    
    return SignalScoreResponse(
        server_id=result["server_id"],
        signal_name=result["signal_name"],
        score=result["score"],
        confidence=result["confidence"],
        evidence_blob=result["evidence_blob"],
        computed_at=result["computed_at"]
    )


# ============================================================================
# SINGLE INSTANCE GUARD
# ============================================================================
def check_single_instance():
    """Ensure only one instance of the service is running."""
    if Path(PID_FILE).exists():
        old_pid = Path(PID_FILE).read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f"Service already running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ValueError):
            logger.info(f"Stale PID file removed: {old_pid}")
    
    Path(PID_FILE).write_text(str(os.getpid()))
    logger.info(f"Service started with PID {os.getpid()}")


def remove_pid_file():
    """Remove PID file on exit."""
    if Path(PID_FILE).exists():
        Path(PID_FILE).unlink()
        logger.info("PID file removed")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    sys.exit(0)


# ============================================================================
# MAIN ENTRYPOINT
# ============================================================================
def run():
    """Run the uvicorn server."""
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"Starting {SERVICE_NAME} on port {PORT}")
    
    # Initial heartbeat
    send_heartbeat()
    
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="info"
    )


if __name__ == "__main__":
    run()