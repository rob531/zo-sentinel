"""Acceptance self-test for the threat association source rollup service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    threat_metadata = MetaData()
    threat_associations = Table(
        "mcp_threat_associations",
        threat_metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("associated_at", DateTime(timezone=True), nullable=False),
        Column("threat_type", String, nullable=False),
        Column("severity", String, nullable=False),
        Column("server_id", String, nullable=False),
        Column("confidence", Float, nullable=True),
    )
    threat_associations.create(engine, checkfirst=True)

    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime.now(timezone.utc)

    session = TestingSession()
    try:
        session.add(McpServerRegistry(server_id="srv1", name="Server One", risk_tier="HIGH"))
        session.add(McpServerRegistry(server_id="srv2", name="Server Two", risk_tier="MEDIUM"))
        session.add(McpServerRegistry(server_id="srv3", name="Server Three", risk_tier="LOW"))
        session.commit()

        session.execute(
            insert(threat_associations),
            [
                {
                    "associated_at": now - timedelta(days=1),
                    "threat_type": "phishing",
                    "severity": "HIGH",
                    "server_id": "srv1",
                    "confidence": 0.95,
                },
                {
                    "associated_at": now - timedelta(days=1),
                    "threat_type": "phishing",
                    "severity": "HIGH",
                    "server_id": "srv2",
                    "confidence": 0.90,
                },
                {
                    "associated_at": now - timedelta(days=1),
                    "threat_type": "phishing",
                    "severity": "MEDIUM",
                    "server_id": "srv1",
                    "confidence": 0.80,
                },
                {
                    "associated_at": now - timedelta(days=2),
                    "threat_type": "malware",
                    "severity": "HIGH",
                    "server_id": "srv2",
                    "confidence": 0.85,
                },
                {
                    "associated_at": now - timedelta(days=2),
                    "threat_type": "malware",
                    "severity": "MEDIUM",
                    "server_id": "srv3",
                    "confidence": 0.75,
                },
            ],
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/threat/associations/rollup?days=30")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["days"] == 30, body
    assert body["total_associations"] == 5, body
    assert body["distinct_servers"] == 3, body
    assert len(body["by_threat_type"]) >= 1, body
    assert len(body["top_servers"]) > 0, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)