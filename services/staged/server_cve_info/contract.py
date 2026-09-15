from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

from app.main import app as fastapi_app


class ServerInfo(BaseModel):
    server_id: str
    name: str | None = None
    url: str | None = None
    description: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    confidence: float | None = None
    last_scanned: datetime | None = None
    last_seen: datetime | None = None
    first_seen: datetime | None = None
    last_assessed: datetime | None = None
    scan_count: int = 0
    registry_source: str | None = None
    verdict: str | None = None
    verdict_reasoning: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class CVEInfo(BaseModel):
    id: str
    summary: str
    severity: str | None = None
    published_at: datetime | None = None


class CVEsResponse(BaseModel):
    server: ServerInfo
    cves: list[CVEInfo]


async def _get_server_cves(db: AsyncSession, server_id: str) -> dict[str, Any]:
    """Fetch server and its CVEs from real data layer."""
    result = await db.execute(
        text("""
            SELECT server_id, name, url, description, risk_tier, trust_score,
                   confidence, last_scanned, last_seen, first_seen, last_assessed,
                   scan_count, registry_source, verdict, verdict_reasoning, meta
            FROM mcp_server_registry
            WHERE server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Server not found")
    
    server = {
        "server_id": row[0],
        "name": row[1],
        "url": row[2],
        "description": row[3],
        "risk_tier": row[4],
        "trust_score": row[5],
        "confidence": row[6],
        "last_scanned": row[7],
        "last_seen": row[8],
        "first_seen": row[9],
        "last_assessed": row[10],
        "scan_count": row[11],
        "registry_source": row[12],
        "verdict": row[13],
        "verdict_reasoning": row[14],
        "meta": row[15] if row[15] else {},
    }
    
    cve_result = await db.execute(
        text("""
            SELECT va.id, va.summary, va.severity, va.published_at
            FROM vuln_advisories va
            INNER JOIN vuln_links vl ON va.id = vl.advisory_id
            WHERE vl.server_id = :server_id
            ORDER BY va.published_at DESC NULLS LAST
        """),
        {"server_id": server_id}
    )
    cves = [
        {"id": r[0], "summary": r[1], "severity": r[2], "published_at": r[3]}
        for r in cve_result.fetchall()
    ]
    
    return {"server": server, "cves": cves}


async def get_server_cves(db: AsyncSession = Depends(get_session), server_id: str = "") -> dict[str, Any]:
    """Public endpoint handler."""
    return await _get_server_cves(db, server_id)


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def main():
    test_app = FastAPI()
    
    @test_app.get("/api/servers/{server_id}/cves")
    async def get_cves(server_id: str, db: AsyncSession = Depends(get_session)):
        return await _get_server_cves(db, server_id)
    
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    async def override_get_session():
        async with AsyncSession(engine) as session:
            yield session
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    async def setup_db():
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    url TEXT,
                    description TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    confidence REAL,
                    last_scanned TIMESTAMP,
                    last_seen TIMESTAMP,
                    first_seen TIMESTAMP,
                    last_assessed TIMESTAMP,
                    scan_count INTEGER DEFAULT 0,
                    registry_source TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    meta TEXT
                )
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS vuln_advisories (
                    id TEXT PRIMARY KEY,
                    summary TEXT,
                    severity TEXT,
                    published_at TIMESTAMP,
                    affected_ranges TEXT,
                    aliases TEXT,
                    content_hash TEXT,
                    ecosystem TEXT,
                    feed TEXT,
                    fetched_at TIMESTAMP,
                    identities TEXT,
                    package TEXT,
                    source_url TEXT
                )
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS vuln_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT,
                    advisory_id TEXT,
                    linked_at TIMESTAMP,
                    match_basis TEXT,
                    match_confidence REAL,
                    match_value TEXT
                )
            """))
    
    run_async(setup_db())
    
    async def seed_data():
        servers = [
            ("srv-001", "Production API Server", "https://api.example.com"),
            ("srv-002", "Staging Database Server", "https://db-staging.example.com"),
        ]
        cves = [
            ("CVE-2021-44228", "Log4j Remote Code Execution", "CRITICAL", "2021-12-10"),
            ("CVE-2022-12345", "OpenSSL Buffer Overflow", "HIGH", "2022-03-15"),
            ("CVE-2023-67890", "Kernel Privilege Escalation", "MEDIUM", "2023-06-20"),
        ]
        
        async with AsyncSession(engine) as session:
            for srv_id, name, url in servers:
                await session.execute(
                    text("INSERT INTO mcp_server_registry (server_id, name, url) VALUES (:srv_id, :name, :url)"),
                    {"srv_id": srv_id, "name": name, "url": url}
                )
            
            for cve_id, summary, severity, published in cves:
                await session.execute(
                    text("INSERT INTO vuln_advisories (id, summary, severity, published_at) VALUES (:id, :summary, :severity, :published)"),
                    {"id": cve_id, "summary": summary, "severity": severity, "published": published}
                )
            
            for srv_id, cve_id in [
                ("srv-001", "CVE-2021-44228"),
                ("srv-001", "CVE-2022-12345"),
                ("srv-001", "CVE-2023-67890"),
                ("srv-002", "CVE-2021-44228"),
                ("srv-002", "CVE-2022-12345"),
                ("srv-002", "CVE-2023-67890"),
            ]:
                await session.execute(
                    text("INSERT INTO vuln_links (server_id, advisory_id) VALUES (:srv_id, :advisory_id)"),
                    {"srv_id": srv_id, "advisory_id": cve_id}
                )
            
            await session.commit()
    
    run_async(seed_data())
    
    client = TestClient(test_app)
    
    response = client.get("/api/servers/srv-001/cves")
    if response.status_code != 200:
        print("FAIL")
        sys.exit(1)
    
    data = response.json()
    if len(data.get("cves", [])) != 3:
        print("FAIL")
        sys.exit(1)
    
    for cve in data["cves"]:
        if not all(k in cve for k in ("id", "summary", "severity", "published_at")):
            print("FAIL")
            sys.exit(1)
    
    response2 = client.get("/api/servers/srv-002/cves")
    if response2.status_code != 200 or len(response2.json().get("cves", [])) != 3:
        print("FAIL")
        sys.exit(1)
    
    response3 = client.get("/api/servers/nonexistent/cves")
    if response3.status_code != 404:
        print("FAIL")
        sys.exit(1)
    
    print("PASS")


if __name__ == "__main__":
    main()