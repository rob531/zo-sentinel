from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_composition(
    session: Session,
    registry_source: str | None = None,
    risk_tier: str | None = None,
):
    """Return server breakdown by registry_source and risk_tier."""
    query = session.query(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label("count"),
    )

    if registry_source is not None:
        query = query.filter(McpServerRegistry.registry_source == registry_source)
    if risk_tier is not None:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    total = session.query(func.count(McpServerRegistry.server_id)).scalar()

    rows = (
        query.group_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
        .order_by(McpServerRegistry.registry_source, McpServerRegistry.risk_tier)
        .all()
    )

    return [
        {
            "registry_source": row.registry_source,
            "risk_tier": row.risk_tier,
            "count": row.count,
            "percentage": round(row.count / total * 100, 2) if total else 0.0,
        }
        for row in rows
    ]


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import StaticPool, create_engine, text
    from sqlalchemy.orm import sessionmaker

    # Build an isolated in-memory store for self-test
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Declare the table schema
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    url TEXT,
                    description TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    trust_score REAL,
                    confidence REAL,
                    meta TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    last_scanned TEXT,
                    last_assessed TEXT,
                    scan_count INTEGER
                )
                """
            )
        )

    # Seed 6 servers: 2 registry sources x 3 tiers
    seed_data = [
        ("s1", "Server One",   "source_a", "tier_1"),
        ("s2", "Server Two",   "source_a", "tier_2"),
        ("s3", "Server Three", "source_a", "tier_3"),
        ("s4", "Server Four",  "source_b", "tier_1"),
        ("s5", "Server Five",  "source_b", "tier_2"),
        ("s6", "Server Six",   "source_b", "tier_3"),
    ]

    with engine.begin() as conn:
        for sid, name, src, tier in seed_data:
            conn.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, risk_tier)
                    VALUES (:sid, :name, :src, :tier)
                    """
                ),
                {"sid": sid, "name": name, "src": src, "tier": tier},
            )

    # Provide a session factory bound to the in-memory store
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    # Wrap with a minimal FastAPI app and dependency override
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    @app.get("/api/registry/composition")
    def composition_endpoint(
        registry_source: str | None = None,
        risk_tier: str | None = None,
    ):
        session = next(override_get_session())
        try:
            return get_composition(session, registry_source, risk_tier)
        finally:
            session.close()

    client = TestClient(app)
    resp = client.get("/api/registry/composition")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()

    by_source = {}
    for row in data:
        by_source.setdefault(row["registry_source"], []).append(row)

    assert len(by_source) == 2, f"Expected 2 registry_source groups, got {len(by_source)}"

    for src in ("source_a", "source_b"):
        rows = by_source[src]
        assert len(rows) == 3, f"Expected 3 tiers for {src}, got {len(rows)}"
        total_count = sum(r["count"] for r in rows)
        assert total_count == 3, f"Expected 3 servers for {src}, got {total_count}"
        for row in rows:
            assert row["percentage"] > 0, f"Expected non-zero percentage for {row}"

    print("PASS")