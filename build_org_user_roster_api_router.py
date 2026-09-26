import os
import sys
import logging
import signal
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Header, Query, Depends
from pydantic import BaseModel
import uvicorn

sys.path.insert(0, '/home/workspace/zo_sentinel')
from build_org_user_roster_api_logic import (
    SERVICE_NAME,
    SERVICE_PORT,
    WRITE_SERVICE_URL,
    utc_now_iso,
    ws_write,
    ws_query,
    ws_execute,
    verify_api_key,
)

LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)

app = FastAPI(title=f"{SERVICE_NAME} API")

_process_start_time = time.time()
_pid_file_written = False


def write_pid():
    global _pid_file_written
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        _pid_file_written = True
    except Exception as e:
        logger.warning(f"Could not write PID file: {e}")


def remove_pid_file():
    global _pid_file_written
    if _pid_file_written:
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except Exception as e:
            logger.warning(f"Could not remove PID file: {e}")


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def check_single_instance():
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid():
                try:
                    os.kill(old_pid, 0)
                    logger.error(f"Another instance is running with PID {old_pid}")
                    sys.exit(1)
                except OSError:
                    logger.warning(f"Stale PID file found, overwriting")
    except Exception as e:
        logger.warning(f"Error checking instance: {e}")


def send_heartbeat():
    try:
        ws_write('service_health', [{
            'service': SERVICE_NAME,
            'last_heartbeat': utc_now_iso(),
            'status': 'running',
            'meta': '{"uptime_seconds": %d}' % int(time.time() - _process_start_time)
        }])
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


class OrgUserRosterResponse(BaseModel):
    success: bool
    data: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    count: int = 0


class CreateUserRequest(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    org_id: Optional[str] = None


class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    org_id: Optional[str] = None


class RemoveUserRequest(BaseModel):
    user_id: str


def get_current_user(authorization: str = Header(None)) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization format")
    token = authorization.replace("Bearer ", "")
    result = verify_api_key(token)
    if not result.get('valid'):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return result.get('user', {})


@app.get("/health")
async def health():
    uptime = int(time.time() - _process_start_time)
    return {"status": "ok", "service": SERVICE_NAME, "uptime_seconds": uptime}


@app.get("/health/live")
async def liveness():
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness():
    try:
        ws_query("SELECT 1 as test")
        return {"status": "ready"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service not ready: {e}")


@app.get("/api/v1/orgs/{org_id}/users", response_model=OrgUserRosterResponse)
async def list_org_users(
    org_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        sql = f"""
        SELECT user_id, email, name, role, org_id, created_at, updated_at
        FROM org_user_roster
        WHERE org_id = '{org_id}'
        ORDER BY created_at DESC
        LIMIT {limit} OFFSET {offset}
        """
        rows = ws_query(sql)
        return OrgUserRosterResponse(
            success=True,
            data=rows,
            count=len(rows)
        )
    except Exception as e:
        logger.error(f"Error listing org users: {e}")
        return OrgUserRosterResponse(success=False, error=str(e))


@app.get("/api/v1/users/{user_id}", response_model=OrgUserRosterResponse)
async def get_user(
    user_id: str,
    user: Dict[str, Any] = Depends(get_current_user)
):
    try:
        sql = f"""
        SELECT user_id, email, name, role, org_id, created_at, updated_at
        FROM org_user_roster
        WHERE user_id = '{user_id}'
        """
        rows = ws_query(sql)
        if not rows:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        return OrgUserRosterResponse(success=True, data=rows, count=len(rows))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user: {e}")
        return OrgUserRosterResponse(success=False, error=str(e))


@app.post("/api/v1/orgs/{org_id}/users", response_model=OrgUserRosterResponse)
async def create_user(
    org_id: str,
    request: CreateUserRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    if user.get('role') not in ['admin', 'superadmin']:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    try:
        user_id = request.user_id or str(uuid.uuid4())
        now = utc_now_iso()
        sql = f"""
        INSERT INTO org_user_roster (user_id, email, name, role, org_id, created_at, updated_at)
        VALUES ('{user_id}', '{request.email}', '{request.name}', '{request.role}', 
                '{org_id if request.org_id is None else request.org_id}', '{now}', '{now}')
        """
        ws_execute(sql)
        new_user = [{
            'user_id': user_id,
            'email': request.email,
            'name': request.name,
            'role': request.role,
            'org_id': org_id if request.org_id is None else request.org_id,
            'created_at': now,
            'updated_at': now
        }]
        return OrgUserRosterResponse(success=True, data=new_user, count=1)
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return OrgUserRosterResponse(success=False, error=str(e))


@app.put("/api/v1/users/{user_id}", response_model=OrgUserRosterResponse)
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    if user.get('role') not in ['admin', 'superadmin']:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    try:
        existing = ws_query(f"SELECT * FROM org_user_roster WHERE user_id = '{user_id}'")
        if not existing:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        name = request.name if request.name is not None else existing[0]['name']
        role = request.role if request.role is not None else existing[0]['role']
        org_id = request.org_id if request.org_id is not None else existing[0]['org_id']
        now = utc_now_iso()
        sql = f"""
        UPDATE org_user_roster
        SET name = '{name}', role = '{role}', org_id = '{org_id}', updated_at = '{now}'
        WHERE user_id = '{user_id}'
        """
        ws_execute(sql)
        updated = ws_query(f"SELECT * FROM org_user_roster WHERE user_id = '{user_id}'")
        return OrgUserRosterResponse(success=True, data=updated, count=len(updated))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user: {e}")
        return OrgUserRosterResponse(success=False, error=str(e))


@app.delete("/api/v1/users/{user_id}", response_model=OrgUserRosterResponse)
async def remove_user(
    user_id: str,
    user: Dict[str, Any] = Depends(get_current_user)
):
    if user.get('role') not in ['admin', 'superadmin']:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    try:
        existing = ws_query(f"SELECT * FROM org_user_roster WHERE user_id = '{user_id}'")
        if not existing:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        sql = f"DELETE FROM org_user_roster WHERE user_id = '{user_id}'"
        ws_execute(sql)
        return OrgUserRosterResponse(success=True, data=existing, count=1)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing user: {e}")
        return OrgUserRosterResponse(success=False, error=str(e))


def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(60)


def run():
    check_single_instance()
    write_pid()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    logger.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    uvicorn.run(app, host='0.0.0.0', port=SERVICE_PORT, log_level='info')


if __name__ == '__main__':
    run()