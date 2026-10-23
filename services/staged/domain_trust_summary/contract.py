from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class DistributionItem(BaseModel):
    label_index: int
    label: str
    count: int
    avg_p_top: float
    avg_p_critical: float


class DomainTrustSummaryResponse(BaseModel):
    axis: str
    total_servers: int
    distribution: list[DistributionItem]


def get_distribution(session: Session, axis_name: str = "domain_trust") -> DomainTrustSummaryResponse:
    query = text("""
        SELECT 
            s.label_index,
            s.label,
            COUNT(*) as count,
            AVG(s.p_top) as avg_p_top,
            AVG(s.p_critical) as avg_p_critical
        FROM mcp_llm_axis_scores s
        WHERE s.axis_name = :axis_name
        GROUP BY s.label_index, s.label
        ORDER BY s.label_index
    """)
    result = session.execute(query, {"axis_name": axis_name})
    rows = result.fetchall()

    distribution = [
        DistributionItem(
            label_index=row.label_index,
            label=row.label,
            count=row.count,
            avg_p_top=float(row.avg_p_top) if row.avg_p_top else 0.0,
            avg_p_critical=float(row.avg_p_critical) if row.avg_p_critical else 0.0,
        )
        for row in rows
    ]

    total_query = text("""
        SELECT COUNT(DISTINCT s.server_id) as total_servers
        FROM mcp_llm_axis_scores s
        WHERE s.axis_name = :axis_name
    """)
    total_result = session.execute(total_query, {"axis_name": axis_name})
    total_servers = total_result.scalar() or 0

    return DomainTrustSummaryResponse(
        axis=axis_name,
        total_servers=total_servers,
        distribution=distribution,
    )


@router.get("/api/summary/domain-trust", response_model=DomainTrustSummaryResponse)
def get_domain_trust_summary(session: Session = Depends(get_session)):
    return get_distribution(session)


def create_tables(engine):
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                description TEXT,
                meta TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                model_version TEXT,
                decision_rule_version TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                adapter_sha256 TEXT,
                scored_at TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
            )
        """))


def seed_test_data(session: Session):
    servers = [
        ("srv-001", "Server Alpha", "https://alpha.example.com"),
        ("srv-002", "Server Beta", "https://beta.example.com"),
        ("srv-003", "Server Gamma", "https://gamma.example.com"),
        ("srv-004", "Server Delta", "https://delta.example.com"),
        ("srv-005", "Server Epsilon", "https://epsilon.example.com"),
    ]
    for server_id, name, url in servers:
        session.execute(
            text("INSERT OR IGNORE INTO mcp_server_registry (server_id, name, url) VALUES (:server_id, :name, :url)"),
            {"server_id": server_id, "name": name, "url": url}
        )

    axis_scores = [
        ("srv-001", "domain_trust", "low", 0, 0.1, 0.05),
        ("srv-002", "domain_trust", "low", 0, 0.15, 0.08),
        ("srv-003", "domain_trust", "medium", 1, 0.5, 0.3),
        ("srv-004", "domain_trust", "medium", 1, 0.55, 0.35),
        ("srv-005", "domain_trust", "high", 2, 0.9, 0.95),
    ]
    for server_id, axis_name, label, label_index, p_top, p_critical in axis_scores:
        session.execute(
            text("""
                INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, label_index, p_top, p_critical)
                VALUES (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical)
            """),
            {
                "server_id": server_id,
                "axis_name": axis_name,
                "label": label,
                "label_index": label_index,
                "p_top": p_top,
                "p_critical": p_critical,
            }
        )
    session.commit()


def _run_self_test():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)
    create_tables(engine)

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as session:
        seed_test_data(session)

    client = TestClient(app)
    response = client.get("/api/summary/domain-trust")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["axis"] == "domain_trust"
    assert data["total_servers"] == 5
    assert len(data["distribution"]) == 3

    dist_by_label = {d["label"]: d for d in data["distribution"]}
    assert dist_by_label["low"]["count"] == 2, f"Expected count 2 for 'low', got {dist_by_label['low']['count']}"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()