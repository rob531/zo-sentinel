# services/staged/verdict_audit_trail/logic.py
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

router = APIRouter()


class AuditEntry(BaseModel):
    timestamp_iso: str
    action: str
    verdict: Optional[str]
    actor: Optional[str]
    detail: Optional[str]


class AuditTrailResponse(BaseModel):
    server_id: str
    entries: List[AuditEntry]


def get_audit_trail(server_id: str) -> Dict[str, Any]:
    """
    Query audit_log table via write_service /query endpoint.
    Returns {server_id, entries: [{timestamp_iso, action, verdict, actor, detail}]}
    """
    query = {
        "sql": """
            SELECT 
                al.timestamp,
                al.action,
                al.verdict,
                al.actor,
                al.detail
            FROM audit_log al
            WHERE al.target_server_id = :server_id
            ORDER BY al.timestamp DESC
            LIMIT 50
        """,
        "params": {"server_id": server_id}
    }
    
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json=query,
            timeout=10
        )
        response.raise_for_status()
        rows = response.json()
        
        entries = []
        for row in rows:
            ts = row.get("timestamp")
            if isinstance(ts, str):
                ts_iso = ts
            elif ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else datetime.fromtimestamp(ts, tz=timezone.utc)
                ts_iso = dt.isoformat()
            else:
                ts_iso = None
            
            entries.append(AuditEntry(
                timestamp_iso=ts_iso,
                action=row.get("action", ""),
                verdict=row.get("verdict"),
                actor=row.get("actor"),
                detail=row.get("detail")
            ))
        
        return {"server_id": server_id, "entries": entries}
    except Exception as e:
        return {"server_id": server_id, "entries": [], "error": str(e)}


@router.get("/api/servers/{server_id}/audit-trail", response_model=AuditTrailResponse)
def get_server_audit_trail(server_id: str) -> AuditTrailResponse:
    """Get audit trail for a specific server."""
    result = get_audit_trail(server_id)
    return AuditTrailResponse(**result)


if __name__ == "__main__":
    import sys
    
    # Self-test with in-memory SQLite
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)
    
    # Create tables
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                target_server_id TEXT,
                action TEXT,
                verdict TEXT,
                actor TEXT,
                detail TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS McpServerRegistry (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                name TEXT
            )
        """))
        conn.commit()
    
    # Seed 2 audit rows for server "srv_test_001"
    with engine.connect() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(text("""
            INSERT INTO audit_log (timestamp, target_server_id, action, verdict, actor, detail)
            VALUES 
                (:ts1, 'srv_test_001', 'scan_completed', 'safe', 'system', 'vulnerability scan finished'),
                (:ts2, 'srv_test_001', 'score_updated', 'medium', 'admin', 'risk score adjusted')
        """), {"ts1": now, "ts2": now})
        conn.commit()
    
    # Test the query logic directly
    class FakeResponse:
        def json(self):
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT al.timestamp, al.action, al.verdict, al.actor, al.detail
                    FROM audit_log al
                    WHERE al.target_server_id = :server_id
                    ORDER BY al.timestamp DESC
                    LIMIT 50
                """), {"server_id": "srv_test_001"})
                rows = []
                for row in result:
                    rows.append({
                        "timestamp": row[0],
                        "action": row[1],
                        "verdict": row[2],
                        "actor": row[3],
                        "detail": row[4]
                    })
                return rows
        
        def raise_for_status(self):
            pass
    
    original_post = requests.post
    requests.post = lambda *args, **kwargs: FakeResponse()
    
    try:
        result = get_audit_trail("srv_test_001")
        
        # Assertions
        assert result["server_id"] == "srv_test_001", f"Expected server_id 'srv_test_001', got {result['server_id']}"
        assert len(result["entries"]) == 2, f"Expected 2 entries, got {len(result['entries'])}"
        
        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    finally:
        requests.post = original_post