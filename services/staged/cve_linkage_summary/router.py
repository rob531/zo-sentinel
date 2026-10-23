from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter()


class AdvisorySummary(BaseModel):
    id: int
    feed: str
    severity: str | None
    summary: str
    published_at: str
    source_url: str
    match_basis: str | None
    match_confidence: float | None


class CVESummaryResponse(BaseModel):
    server_id: str
    total_links: int
    severity_breakdown: dict
    recent_advisories: list


@router.get("/servers/{server_id}/cve_summary", response_model=CVESummaryResponse)
def get_cve_summary(server_id: str, session: Session = Depends(get_session)) -> CVESummaryResponse:
    result = session.execute(
        text("""
            SELECT
                va.severity,
                va.id,
                va.feed,
                va.summary,
                va.published_at,
                va.source_url,
                vl.match_basis,
                vl.match_confidence
            FROM vuln_links vl
            JOIN vuln_advisories va ON vl.advisory_id = va.id
            WHERE vl.server_id = :server_id
            ORDER BY va.published_at DESC
        """),
        {"server_id": server_id}
    )
    rows = result.fetchall()

    total_links = len(rows)
    severity_breakdown = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}

    recent_advisories = []
    for row in rows:
        sev = row.severity or "unknown"
        if sev in severity_breakdown:
            severity_breakdown[sev] += 1
        if len(recent_advisories) < 5:
            recent_advisories.append(AdvisorySummary(
                id=row.id,
                feed=row.feed,
                severity=row.severity,
                summary=row.summary,
                published_at=row.published_at.isoformat() if hasattr(row.published_at, 'isoformat') else str(row.published_at),
                source_url=row.source_url,
                match_basis=row.match_basis,
                match_confidence=row.match_confidence
            ))

    return CVESummaryResponse(
        server_id=server_id,
        total_links=total_links,
        severity_breakdown=severity_breakdown,
        recent_advisories=recent_advisories
    )


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    import sys
    sys.path.insert(0, '.')

    from main import app

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE vuln_advisories (id INTEGER PRIMARY KEY, feed TEXT, severity TEXT, summary TEXT, published_at TEXT, source_url TEXT)"))
        conn.execute(text("CREATE TABLE vuln_links (id INTEGER PRIMARY KEY, server_id TEXT, advisory_id INTEGER, match_basis TEXT, match_confidence REAL)"))

        conn.execute(text("INSERT INTO vuln_advisories (id, feed, severity, summary, published_at, source_url) VALUES (1, 'test', 'critical', 'Test 1', '2024-01-06', 'http://test1.com')"))
        conn.execute(text("INSERT INTO vuln_links (server_id, advisory_id, match_basis, match_confidence) VALUES ('svr_test', 1, 'test', 0.9)"))
        conn.execute(text("INSERT INTO vuln_advisories (id, feed, severity, summary, published_at, source_url) VALUES (2, 'test', 'high', 'Test 2', '2024-01-05', 'http://test2.com')"))
        conn.execute(text("INSERT INTO vuln_links (server_id, advisory_id, match_basis, match_confidence) VALUES ('svr_test', 2, 'test', 0.8)"))
        conn.execute(text("INSERT INTO vuln_advisories (id, feed, severity, summary, published_at, source_url) VALUES (3, 'test', 'medium', 'Test 3', '2024-01-04', 'http://test3.com')"))
        conn.execute(text("INSERT INTO vuln_links (server_id, advisory_id, match_basis, match_confidence) VALUES ('svr_test', 3, 'test', 0.7)"))
        conn.execute(text("INSERT INTO vuln_advisories (id, feed, severity, summary, published_at, source_url) VALUES (4, 'test', 'low', 'Test 4', '2024-01-03', 'http://test4.com')"))
        conn.execute(text("INSERT INTO vuln_links (server_id, advisory_id, match_basis, match_confidence) VALUES ('svr_test', 4, 'test', 0.5)"))
        conn.execute(text("INSERT INTO vuln_advisories (id, feed, severity, summary, published_at, source_url) VALUES (5, 'test', NULL, 'Test 5', '2024-01-02', 'http://test5.com')"))
        conn.execute(text("INSERT INTO vuln_links (server_id, advisory_id, match_basis, match_confidence) VALUES ('svr_test', 5, 'test', 0.5)"))

    from sqlalchemy.orm import sessionmaker
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    resp = client.get("/api/servers/svr_test/cve_summary")
    data = resp.json()

    assert data["server_id"] == "svr_test", f"Expected server_id='svr_test', got {data['server_id']}"
    assert data["total_links"] == 4, f"Expected total_links=4, got {data['total_links']}"
    assert data["severity_breakdown"]["critical"] == 1, f"Expected critical=1, got {data['severity_breakdown']['critical']}"
    assert data["severity_breakdown"]["high"] == 1, f"Expected high=1, got {data['severity_breakdown']['high']}"
    assert data["severity_breakdown"]["medium"] == 1, f"Expected medium=1, got {data['severity_breakdown']['medium']}"
    assert data["severity_breakdown"]["low"] == 1, f"Expected low=1, got {data['severity_breakdown']['low']}"
    assert data["severity_breakdown"]["unknown"] == 1, f"Expected unknown=1, got {data['severity_breakdown']['unknown']}"
    assert len(data["recent_advisories"]) == 4, f"Expected 4 advisories, got {len(data['recent_advisories'])}"

    pubs = [a["published_at"] for a in data["recent_advisories"]]
    assert pubs == sorted(pubs, reverse=True), f"Expected sorted desc, got {pubs}"

    print("PASS")