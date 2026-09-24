from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session

router = APIRouter(prefix="/api", tags=["scorer"])


class ReconciliationRecord(BaseModel):
    server_id: str
    fire_score: float
    final_score: float
    difference: float


class ReconciliationReport(BaseModel):
    records: list[ReconciliationRecord]


@router.get("/scorer/reconciliation", response_model=ReconciliationReport)
async def get_reconciliation_report(
    session: AsyncSession = Depends(get_session),
) -> ReconciliationReport:
    query = text("""
        SELECT 
            s.server_id,
            s.fire_score,
            s.final_score,
            COALESCE(s.final_score, 0) - COALESCE(s.fire_score, 0) as difference
        FROM mcp_signal_scores s
        INNER JOIN mcp_server_registry r ON s.server_id = r.server_id
        WHERE s.fire_score IS NOT NULL OR s.final_score IS NOT NULL
        ORDER BY s.server_id
    """)
    result = await session.execute(query)
    rows = result.fetchall()
    
    records = [
        ReconciliationRecord(
            server_id=row[0],
            fire_score=row[1] if row[1] is not None else 0.0,
            final_score=row[2] if row[2] is not None else 0.0,
            difference=row[3] if row[3] is not None else 0.0,
        )
        for row in rows
    ]
    return ReconciliationReport(records=records)


if __name__ == "__main__":
    import asyncio
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import get_session
    from app.models import Base, McpServerRegistry

    async def run_test():
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)

        async def override_get_session():
            async with TestingSessionLocal() as session:
                yield session

        with TestingSessionLocal() as db:
            db.execute(text("""
                CREATE TABLE mcp_signal_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    fire_score REAL,
                    final_score REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.commit()

            for i in range(1, 3):
                server_id = f"server_{i}"
                db.execute(
                    text("INSERT INTO mcp_server_registry (server_id, name, url, risk_tier, confidence, trust_score, verdict) VALUES (:server_id, :name, :url, :risk_tier, :confidence, :trust_score, :verdict)"),
                    {"server_id": server_id, "name": f"Server {i}", "url": f"http://server{i}.com", "risk_tier": "medium", "confidence": 0.8, "trust_score": 75.0, "verdict": "unknown"}
                )
                for axis in range(1, 8):
                    fire = 50.0 + (i * 10) + axis
                    final = fire + 15.0 + (i * 5)
                    db.execute(
                        text("INSERT INTO mcp_signal_scores (server_id, fire_score, final_score) VALUES (:server_id, :fire, :final)"),
                        {"server_id": server_id, "fire": fire, "final": final}
                    )
            db.commit()

        app = FastAPI()
        app.dependency_overrides[get_session] = override_get_session
        app.include_router(router)

        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM mcp_server_registry"))
            srv_count = result.scalar()
            result = conn.execute(text("SELECT COUNT(*) FROM mcp_signal_scores"))
            score_count = result.scalar()

        client = __import__("httpx").AsyncClient(transport=__import__("httpx").ASGITransport(app=app), base_url="http://test")
        response = await client.get("/api/scorer/reconciliation")
        data = response.json()

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert srv_count == 2, f"Expected 2 servers, got {srv_count}"
        assert score_count == 14, f"Expected 14 scores (2 servers x 7 axes), got {score_count}"
        assert len(data["records"]) == 14, f"Expected 14 records, got {len(data['records'])}"

        diffs = [r["difference"] for r in data["records"]]
        assert any(d > 0 for d in diffs), f"Expected at least one difference > 0, got {diffs}"

        print("PASS")

    asyncio.run(run_test())