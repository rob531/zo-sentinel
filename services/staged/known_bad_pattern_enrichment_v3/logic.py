from datetime import datetime, timedelta
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
import requests

def enrich_known_bad_patterns(session: Session = Depends(get_session)):
    # Calculate the date 24 hours ago
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)

    # Query servers that have been seen in the last 24 hours
    servers = session.query(McpServerRegistry).filter(
        McpServerRegistry.last_seen >= twenty_four_hours_ago
    ).all()

    # Prepare data for enrichment
    enrichment_data = []
    for server in servers:
        enrichment_data.append({
            "server_id": server.server_id,
            "name": server.name,
            "url": server.url,
            "description": server.description,
            "first_seen": server.first_seen.isoformat(),
            "last_seen": server.last_seen.isoformat(),
            "last_scanned": server.last_scanned.isoformat(),
            "last_assessed": server.last_assessed.isoformat(),
            "scan_count": server.scan_count,
            "trust_score": server.trust_score,
            "risk_tier": server.risk_tier,
            "verdict": server.verdict,
            "verdict_reasoning": server.verdict_reasoning,
            "registry_source": server.registry_source,
            "meta": server.meta
        })

    # Send enrichment data to the write service
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "table": "mcp_signal_scores",
            "data": enrichment_data
        }
    )

    if response.status_code == 200:
        print("PASS")
    else:
        raise Exception("Failed to enrich known bad patterns")

def run():
    enrich_known_bad_patterns()

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool

    app = FastAPI()

    @app.get("/")
    def read_root():
        enrich_known_bad_patterns()
        return {"status": "PASS"}

    # Override the get_session dependency for testing
    def override_get_session():
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()

    app.dependency_overrides[get_session] = override_get_session

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)