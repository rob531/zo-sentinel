from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool

# Real imports from app
from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


# Pydantic models for response
class TopAdvisory(BaseModel):
    id: str
    severity: str
    count: int


class VulnAnalysisResponse(BaseModel):
    severity_counts: dict[str, int]
    tier_distribution: dict[str, int]
    top_advisories: list[TopAdvisory]


# Router
def create_router() -> Any:
    from fastapi import APIRouter
    
    router = APIRouter()
    
    @router.get("/vuln-analysis", response_model=VulnAnalysisResponse)
    def get_vuln_analysis(db: Session = Depends(get_session)) -> VulnAnalysisResponse:
        # Aggregate by severity
        severity_query = text("""
            SELECT va.severity, COUNT(*) as count
            FROM vuln_advisories va
            JOIN vuln_links vl ON va.id = vl.advisory_id
            GROUP BY va.severity
            ORDER BY count DESC
        """)
        severity_results = db.execute(severity_query).fetchall()
        severity_counts = {row[0]: row[1] for row in severity_results}
        
        # Aggregate by server risk tier
        tier_query = text("""
            SELECT msr.risk_tier, COUNT(*) as count
            FROM vuln_links vl
            JOIN mcp_server_registry msr ON vl.server_id = msr.server_id
            GROUP BY msr.risk_tier
            ORDER BY count DESC
        """)
        tier_results = db.execute(tier_query).fetchall()
        tier_distribution = {row[0]: row[1] for row in tier_results if row[0]}
        
        # Top advisories by link count
        top_query = text("""
            SELECT va.id, va.severity, COUNT(vl.id) as count
            FROM vuln_advisories va
            JOIN vuln_links vl ON va.id = vl.advisory_id
            GROUP BY va.id, va.severity
            ORDER BY count DESC
            LIMIT 10
        """)
        top_results = db.execute(top_query).fetchall()
        top_advisories = [
            TopAdvisory(id=row[0], severity=row[1], count=row[2])
            for row in top_results
        ]
        
        return VulnAnalysisResponse(
            severity_counts=severity_counts,
            tier_distribution=tier_distribution,
            top_advisories=top_advisories
        )
    
    return router


def create_app() -> FastAPI:
    app = FastAPI(title="vuln_analysis_dashboard_api")
    app.include_router(create_router(), prefix="/api", tags=["vuln-analysis"])
    return app


@contextmanager
def get_local_session(path: str) -> Generator[Session, None, None]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Create tables
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS mcp_server_registry (
            server_id TEXT PRIMARY KEY,
            name TEXT,
            url TEXT,
            risk_tier TEXT,
            trust_score REAL,
            confidence REAL,
            verdict TEXT,
            verdict_reasoning TEXT,
            registry_source TEXT,
            description TEXT,
            meta TEXT,
            first_seen TEXT,
            last_seen TEXT,
            last_scanned TEXT,
            last_assessed TEXT,
            scan_count INTEGER
        );
        
        CREATE TABLE IF NOT EXISTS vuln_advisories (
            id TEXT PRIMARY KEY,
            severity TEXT,
            summary TEXT,
            package TEXT,
            ecosystem TEXT,
            aliases TEXT,
            affected_ranges TEXT,
            published_at TEXT,
            fetched_at TEXT,
            content_hash TEXT,
            source_url TEXT,
            feed TEXT,
            identities TEXT
        );
        
        CREATE TABLE IF NOT EXISTS vuln_links (
            id TEXT PRIMARY KEY,
            advisory_id TEXT,
            server_id TEXT,
            match_value TEXT,
            match_basis TEXT,
            match_confidence REAL,
            linked_at TEXT,
            FOREIGN KEY (advisory_id) REFERENCES vuln_advisories(id),
            FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
        );
    """)
    conn.commit()
    
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def seed_test_data(session: Session) -> None:
    # Seed 2 servers
    servers = [
        {
            "server_id": "srv-001",
            "name": "security-scan-server",
            "url": "https://security-scan.internal",
            "risk_tier": "high",
            "trust_score": 0.7,
            "confidence": 0.85,
            "registry_source": "internal",
            "description": "Security scanning service",
            "first_seen": "2024-01-01T00:00:00Z",
            "last_seen": "2024-06-15T00:00:00Z",
            "scan_count": 100
        },
        {
            "server_id": "srv-002",
            "name": "code-analysis-server",
            "url": "https://code-analysis.internal",
            "risk_tier": "medium",
            "trust_score": 0.85,
            "confidence": 0.9,
            "registry_source": "internal",
            "description": "Code analysis service",
            "first_seen": "2024-01-15T00:00:00Z",
            "last_seen": "2024-06-14T00:00:00Z",
            "scan_count": 250
        }
    ]
    
    for srv in servers:
        session.execute(
            text("""
                INSERT OR REPLACE INTO mcp_server_registry 
                (server_id, name, url, risk_tier, trust_score, confidence, 
                 registry_source, description, first_seen, last_seen, scan_count)
                VALUES (:server_id, :name, :url, :risk_tier, :trust_score, :confidence,
                        :registry_source, :description, :first_seen, :last_seen, :scan_count)
            """),
            srv
        )
    
    # Seed 5 advisories with various severities
    advisories = [
        {
            "id": "ADV-001",
            "severity": "critical",
            "summary": "Remote code execution vulnerability",
            "package": "express",
            "ecosystem": "npm",
            "published_at": "2024-06-01T00:00:00Z",
            "fetched_at": "2024-06-10T00:00:00Z",
            "source_url": "https://example.com/adv/001",
            "feed": "nvd"
        },
        {
            "id": "ADV-002",
            "severity": "high",
            "summary": "SQL injection vulnerability",
            "package": "mysql-connector",
            "ecosystem": "pypi",
            "published_at": "2024-05-15T00:00:00Z",
            "fetched_at": "2024-06-09T00:00:00Z",
            "source_url": "https://example.com/adv/002",
            "feed": "ghsa"
        },
        {
            "id": "ADV-003",
            "severity": "medium",
            "summary": "Cross-site scripting vulnerability",
            "package": "react-dom",
            "ecosystem": "npm",
            "published_at": "2024-05-20T00:00:00Z",
            "fetched_at": "2024-06-08T00:00:00Z",
            "source_url": "https://example.com/adv/003",
            "feed": "nvd"
        },
        {
            "id": "ADV-004",
            "severity": "low",
            "summary": "Information disclosure",
            "package": "lodash",
            "ecosystem": "npm",
            "published_at": "2024-04-10T00:00:00Z",
            "fetched_at": "2024-06-07T00:00:00Z",
            "source_url": "https://example.com/adv/004",
            "feed": "nvd"
        },
        {
            "id": "ADV-005",
            "severity": "high",
            "summary": "Authentication bypass vulnerability",
            "package": "django",
            "ecosystem": "pypi",
            "published_at": "2024-06-05T00:00:00Z",
            "fetched_at": "2024-06-11T00:00:00Z",
            "source_url": "https://example.com/adv/005",
            "feed": "ghsa"
        }
    ]
    
    for adv in advisories:
        session.execute(
            text("""
                INSERT OR REPLACE INTO vuln_advisories
                (id, severity, summary, package, ecosystem, published_at, fetched_at, source_url, feed)
                VALUES (:id, :severity, :summary, :package, :ecosystem, :published_at, :fetched_at, :source_url, :feed)
            """),
            adv
        )
    
    # Seed links: advisories linked to servers
    links = [
        {"id": "link-001", "advisory_id": "ADV-001", "server_id": "srv-001", "match_confidence": 0.95, "linked_at": "2024-06-10T00:00:00Z"},
        {"id": "link-002", "advisory_id": "ADV-002", "server_id": "srv-001", "match_confidence": 0.88, "linked_at": "2024-06-09T00:00:00Z"},
        {"id": "link-003", "advisory_id": "ADV-003", "server_id": "srv-001", "match_confidence": 0.75, "linked_at": "2024-06-08T00:00:00Z"},
        {"id": "link-004", "advisory_id": "ADV-004", "server_id": "srv-002", "match_confidence": 0.60, "linked_at": "2024-06-07T00:00:00Z"},
        {"id": "link-005", "advisory_id": "ADV-005", "server_id": "srv-002", "match_confidence": 0.92, "linked_at": "2024-06-11T00:00:00Z"},
    ]
    
    for link in links:
        session.execute(
            text("""
                INSERT OR REPLACE INTO vuln_links
                (id, advisory_id, server_id, match_confidence, linked_at)
                VALUES (:id, :advisory_id, :server_id, :match_confidence, :linked_at)
            """),
            link
        )
    
    session.commit()


def run_contract_test() -> bool:
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        # Create local SQLite session
        with get_local_session(db_path) as local_session:
            seed_test_data(local_session)
        
        # Create app with SQLite override
        app = create_app()
        
        # Create SQLite engine for override
        sqlite_engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        TestingSessionLocal = sessionmaker(bind=sqlite_engine)
        
        def override_get_session() -> Generator[Session, None, None]:
            session = TestingSessionLocal()
            try:
                yield session
            finally:
                session.close()
        
        app.dependency_overrides[get_session] = override_get_session
        
        client = TestClient(app)
        response = client.get("/api/vuln-analysis")
        
        # Assertions
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        # Check severity counts are non-empty
        assert "severity_counts" in data
        severity_counts = data["severity_counts"]
        assert len(severity_counts) > 0, "severity_counts should not be empty"
        
        # Check each severity has a count
        for severity in ["critical", "high", "medium", "low"]:
            if severity in severity_counts:
                assert severity_counts[severity] > 0, f"{severity} should have non-zero count"
        
        # Check tier distribution
        assert "tier_distribution" in data
        assert len(data["tier_distribution"]) > 0, "tier_distribution should not be empty"
        
        # Check top_advisories
        assert "top_advisories" in data
        assert len(data["top_advisories"]) > 0, "top_advisories should not be empty"
        
        sqlite_engine.dispose()
        
        return True
        
    finally:
        import os
        try:
            os.unlink(db_path)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        success = run_contract_test()
        if success:
            print("PASS")
            exit(0)
        else:
            print("FAIL")
            exit(1)
    except Exception as e:
        print(f"FAIL: {e}")
        exit(1)