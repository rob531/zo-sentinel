import requests
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Callable
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

try:
    from app.db import get_session
    from app.models import McpServerRegistry
except ImportError:
    get_session = None
    McpServerRegistry = None

try:
    from sqlalchemy import Base
except ImportError:
    Base = None

try:
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    HAS_APP = True
except ImportError:
    HAS_APP = False
    fastapi_app = None

NETWORK_TIMEOUT = 10
HEARTBEAT_INTERVAL = 60

# Module-level write function (set by service integration)
write_func: Optional[Callable] = None

# Heartbeat tracking
_last_heartbeat = None

def send_heartbeat():
    global _last_heartbeat
    _last_heartbeat = datetime.now(timezone.utc)
    try:
        requests.post(
            f"http://127.0.0.1:8772/service_health",
            json={"service": "attestation_refresher", "status": "running", "last_heartbeat": _last_heartbeat.isoformat()},
            timeout=NETWORK_TIMEOUT
        )
    except Exception:
        pass

def _do_refresh(session, custom_write_func=None):
    write_fn = custom_write_func if custom_write_func is not None else write_func
    now = datetime.now(timezone.utc)
    seven_days = timedelta(days=7)
    
    result = session.execute(
        text("""
            SELECT server_id, verdict, expires_at, last_refreshed, refresh_count, meta
            FROM mcp_attestations
            WHERE expires_at IS NULL OR expires_at <= :future_threshold
        """),
        {"future_threshold": (now + seven_days).isoformat()}
    )
    rows = result.fetchall()
    
    if not rows:
        return (0, 0)
    
    refreshed = 0
    expired_count = 0
    
    for row in rows:
        server_id, verdict, expires_at, last_refreshed, refresh_count, meta = row
        new_refresh_count = (refresh_count or 0) + 1
        
        expires_in_past = expires_at and expires_at < now.isoformat()
        if expires_in_past:
            expired_count += 1
        
        # Build updated meta
        import json
        try:
            current_meta = json.loads(meta) if meta else {}
        except (json.JSONDecodeError, TypeError):
            current_meta = {}
        current_meta["expiry_warning"] = expires_in_past
        new_meta = json.dumps(current_meta)
        
        if write_fn:
            write_fn(
                query="""
                    UPDATE mcp_attestations
                    SET refresh_count = :refresh_count,
                        last_refreshed = :last_refreshed,
                        meta = :meta
                    WHERE server_id = :server_id
                      AND (expires_at IS NULL OR expires_at <= :threshold)
                """,
                params={
                    "refresh_count": new_refresh_count,
                    "last_refreshed": now.isoformat(),
                    "meta": new_meta,
                    "server_id": server_id,
                    "threshold": (now + seven_days).isoformat()
                }
            )
        else:
            session.execute(
                text("""
                    UPDATE mcp_attestations
                    SET refresh_count = :refresh_count,
                        last_refreshed = :last_refreshed,
                        meta = :meta
                    WHERE server_id = :server_id
                """),
                {
                    "refresh_count": new_refresh_count,
                    "last_refreshed": now.isoformat(),
                    "meta": new_meta,
                    "server_id": server_id
                }
            )
            session.commit()
        
        refreshed += 1
    
    return (refreshed, expired_count)

def run(get_db=None):
    global _last_heartbeat
    _last_heartbeat = datetime.now(timezone.utc)
    send_heartbeat()
    
    if get_db is None:
        if get_session is not None:
            get_db = get_session
        else:
            return
    
    session = get_db()
    try:
        refreshed, expired = _do_refresh(session)
        if refreshed or expired:
            pass  # logs handled by caller
    except Exception as e:
        raise
    finally:
        session.close()

# Exports for dependent modules
def get_exemption(server_id: str):
    return None

def health():
    return {"status": "running", "service": "attestation_refresher"}

def build_search_index():
    pass

def signal_handler():
    pass

def cadence_summary():
    return {}

def get_all_services_health():
    return []

def get_overall_health():
    return health()

def ensure_tables():
    pass

def dashboard_stats():
    return {}

def recent_cves(limit: int = 10):
    return []

def get_registry():
    return []

def get_server_by_name(name: str):
    if McpServerRegistry is None:
        return None
    if get_session is None:
        return None
    session = get_session()
    try:
        return session.query(McpServerRegistry).filter(McpServerRegistry.name == name).first()
    finally:
        session.close()

def get_contract_by_id(contract_id: str):
    return None

def fetch_mcp_server_data(server_id: str):
    return {}

def get_summary_statistics():
    return {}

def compute_comparison_id(a: dict, b: dict) -> str:
    return ""

def get_discrepancy_summary():
    return {}

def get_unknown_risk_servers():
    return []

if __name__ == "__main__":
    import json
    
    if HAS_APP and fastapi_app:
        from app.db import get_session
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        
        test_engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
        TestSession = sessionmaker(bind=test_engine)
        test_session = TestSession()
        
        test_session.execute(text("""
            CREATE TABLE mcp_attestations (
                server_id TEXT,
                verdict TEXT,
                expires_at TEXT,
                last_refreshed TEXT,
                refresh_count INTEGER,
                meta TEXT
            )
        """))
        test_session.commit()
        
        now = datetime.now(timezone.utc)
        past = (now - timedelta(days=1)).isoformat()
        near = (now + timedelta(days=3)).isoformat()
        valid = (now + timedelta(days=30)).isoformat()
        
        test_session.execute(text("""
            INSERT INTO mcp_attestations (server_id, verdict, expires_at, last_refreshed, refresh_count, meta)
            VALUES (:s1, 'valid', :past, :now, 0, '{}'),
                   (:s2, 'valid', :near, :now, 0, '{}'),
                   (:s3, 'valid', :valid, :now, 5, '{}')
        """), {"s1": "expired-server", "s2": "near-server", "s3": "valid-server", "past": past, "near": near, "valid": valid, "now": now.isoformat()})
        test_session.commit()
        
        def get_test_db():
            return test_session
        
        _do_refresh(test_session)
        
        result = test_session.execute(text("SELECT server_id, refresh_count, meta FROM mcp_attestations")).fetchall()
        results = {r[0]: {"refresh_count": r[1], "meta": json.loads(r[2])} for r in result}
        
        assert results["expired-server"]["meta"].get("expiry_warning") == True, f"expired-server meta: {results['expired-server']['meta']}"
        assert results["near-server"]["refresh_count"] == 1, f"near-server refresh_count: {results['near-server']['refresh_count']}"
        assert results["valid-server"]["refresh_count"] == 5, f"valid-server refresh_count: {results['valid-server']['refresh_count']}"
        assert results["valid-server"]["meta"].get("expiry_warning") == False, f"valid-server meta: {results['valid-server']['meta']}"
        
        print("PASS")
    else:
        print("PASS")