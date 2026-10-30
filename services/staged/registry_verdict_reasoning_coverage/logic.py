from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


def _is_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def get_registry_verdict_coverage(
    db: Session = Depends(get_session),
    registry_source: Optional[str] = None,
) -> dict:
    statement = select(
        McpServerRegistry.registry_source,
        McpServerRegistry.verdict,
        McpServerRegistry.verdict_reasoning,
        McpServerRegistry.confidence,
        McpServerRegistry.risk_tier,
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = db.execute(statement).all()

    total_servers = len(rows)
    with_verdict = 0
    with_reasoning = 0
    with_confidence = 0
    with_risk_tier = 0
    grouped = OrderedDict()

    for row in rows:
        source = row.registry_source if row.registry_source is not None else ""
        if source not in grouped:
            grouped[source] = {
                "registry_source": source,
                "total": 0,
                "with_verdict": 0,
                "coverage_ratio": 0.0,
            }

        grouped[source]["total"] += 1

        verdict_present = _is_present(row.verdict)
        reasoning_present = _is_present(row.verdict_reasoning)
        confidence_present = _is_present(row.confidence)
        risk_tier_present = _is_present(row.risk_tier)

        if verdict_present:
            with_verdict += 1
            grouped[source]["with_verdict"] += 1
        if reasoning_present:
            with_reasoning += 1
        if confidence_present:
            with_confidence += 1
        if risk_tier_present:
            with_risk_tier += 1

    for entry in grouped.values():
        entry["coverage_ratio"] = (
            entry["with_verdict"] / entry["total"] if entry["total"] else 0.0
        )

    return {
        "total_servers": total_servers,
        "with_verdict": with_verdict,
        "with_reasoning": with_reasoning,
        "with_confidence": with_confidence,
        "with_risk_tier": with_risk_tier,
        "coverage_ratio": with_verdict / total_servers if total_servers else 0.0,
        "by_registry_source": list(grouped.values()),
    }


@router.get("/registry/verdict-coverage")
def registry_verdict_coverage(
    registry_source: Optional[str] = None,
    db: Session = Depends(get_session),
) -> dict:
    return get_registry_verdict_coverage(db=db, registry_source=registry_source)


app = FastAPI(title="registry verdict reasoning coverage")
app.include_router(router)


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    registry_source TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    confidence REAL,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, registry_source, verdict, verdict_reasoning,
                     confidence, risk_tier)
                VALUES
                    (:server_id, :registry_source, :verdict, :verdict_reasoning,
                     :confidence, :risk_tier)
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "registry_source": "source-a",
                    "verdict": "malicious",
                    "verdict_reasoning": "Detected an unsafe tool declaration",
                    "confidence": 0.95,
                    "risk_tier": "high",
                },
                {
                    "server_id": "server-2",
                    "registry_source": "source-b",
                    "verdict": "safe",
                    "verdict_reasoning": None,
                    "confidence": None,
                    "risk_tier": None,
                },
                {
                    "server_id": "server-3",
                    "registry_source": "source-c",
                    "verdict": None,
                    "verdict_reasoning": "Registry metadata was insufficient",
                    "confidence": None,
                    "risk_tier": None,
                },
                {
                    "server_id": "server-4",
                    "registry_source": "source-d",
                    "verdict": None,
                    "verdict_reasoning": None,
                    "confidence": None,
                    "risk_tier": None,
                },
            ],
        )

    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/verdict-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 4
        assert payload["with_verdict"] == 2
        assert payload["with_reasoning"] == 2
        assert payload["with_confidence"] == 1
        assert payload["with_risk_tier"] == 1
        assert payload["coverage_ratio"] == 0.5
        assert len(payload["by_registry_source"]) == 4

    print("PASS")


if __name__ == "__main__":
    _self_test()