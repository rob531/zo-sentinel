import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def write_service_query(sql: str, params: dict | None = None) -> list[dict]:
    import requests
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params or {}},
        headers={"Content-Type": "application/json"},
        timeout=10
    )
    response.raise_for_status()
    return response.json()


class HealthAggregatedResponse(BaseModel):
    total_services: int
    healthy: int
    stale: int
    by_status: dict[str, int]
    stale_services: list[dict[str, Any]]


def get_aggregated_health() -> dict[str, Any]:
    sql = text("""
        SELECT service, status, last_heartbeat
        FROM service_health
        ORDER BY last_heartbeat DESC
    """)
    rows = write_service_query(
        "SELECT service, status, last_heartbeat FROM service_health ORDER BY last_heartbeat DESC",
        {}
    )
    
    total_services = len(rows)
    by_status: dict[str, int] = {}
    stale_services: list[dict[str, Any]] = []
    
    for row in rows:
        status = row.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        
        if row.get("last_heartbeat"):
            hb = row["last_heartbeat"]
            if isinstance(hb, str):
                hb_dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
            else:
                hb_dt = hb
            age_seconds = int((datetime.now().astimezone() - hb_dt).total_seconds())
        else:
            age_seconds = -1
        
        if status in ("stale", "unknown") or age_seconds > 300:
            stale_services.append({
                "service": row.get("service"),
                "last_heartbeat": row.get("last_heartbeat"),
                "age_seconds": age_seconds
            })
    
    healthy = by_status.get("healthy", 0)
    stale = total_services - healthy
    
    return {
        "total_services": total_services,
        "healthy": healthy,
        "stale": stale,
        "by_status": by_status,
        "stale_services": stale_services
    }


def get_aggregated_health_sqlalchemy(session: Session) -> dict[str, Any]:
    result = session.execute(text("""
        SELECT service, status, last_heartbeat
        FROM service_health
        ORDER BY last_heartbeat DESC
    """))
    rows = [dict(row._mapping) for row in result]
    
    total_services = len(rows)
    by_status: dict[str, int] = {}
    stale_services: list[dict[str, Any]] = []
    
    for row in rows:
        status = row.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        
        if row.get("last_heartbeat"):
            hb = row["last_heartbeat"]
            if isinstance(hb, str):
                hb_dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
            else:
                hb_dt = hb
            age_seconds = int((datetime.now().astimezone() - hb_dt).total_seconds())
        else:
            age_seconds = -1
        
        if status in ("stale", "unknown") or age_seconds > 300:
            stale_services.append({
                "service": row.get("service"),
                "last_heartbeat": row.get("last_heartbeat"),
                "age_seconds": age_seconds
            })
    
    healthy = by_status.get("healthy", 0)
    stale = total_services - healthy
    
    return {
        "total_services": total_services,
        "healthy": healthy,
        "stale": stale,
        "by_status": by_status,
        "stale_services": stale_services
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS service_health (
                id INTEGER PRIMARY KEY,
                service TEXT NOT NULL,
                status TEXT NOT NULL,
                last_heartbeat TIMESTAMP NOT NULL
            )
        """))
        conn.commit()
        
        now = datetime.now().astimezone()
        past_stale = (now.replace(microsecond=0)).isoformat()
        past_healthy = (now.replace(microsecond=0)).isoformat()
        
        mock_rows = [
            ("auth_service", "healthy", past_healthy),
            ("user_service", "healthy", past_healthy),
            ("cache_service", "stale", past_stale),
            ("gateway_service", "unknown", past_stale),
        ]
        
        for service, status, heartbeat in mock_rows:
            conn.execute(
                text("INSERT INTO service_health (service, status, last_heartbeat) VALUES (:s, :st, :h)"),
                {"s": service, "st": status, "h": heartbeat}
            )
        conn.commit()
    
    app = FastAPI()
    
    def get_session_override():
        return SessionLocal()
    
    @app.get("/api/health/aggregated")
    def endpoint(session: Session = Depends(get_session_override)):
        result = get_aggregated_health_sqlalchemy(session)
        return result
    
    client = TestClient(app)
    response = client.get("/api/health/aggregated")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    
    assert data["total_services"] == 4, f"Expected total_services=4, got {data['total_services']}"
    assert "healthy" in data["by_status"], f"Expected 'healthy' in by_status keys: {data['by_status'].keys()}"
    assert "stale" in data["by_status"], f"Expected 'stale' in by_status keys: {data['by_status'].keys()}"
    assert "unknown" in data["by_status"], f"Expected 'unknown' in by_status keys: {data['by_status'].keys()}"
    
    print("PASS")