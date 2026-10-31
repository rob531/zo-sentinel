from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RegistrySourceScanBacklogItem(BaseModel):
    registry_source: str
    server_count: int = Field(ge=0)
    servers_with_verdict: int = Field(ge=0)
    never_scanned: int = Field(ge=0)
    stale_scanned: int = Field(ge=0)
    verdict_coverage_pct: int = Field(ge=0, le=100)
    avg_scan_count: float = Field(ge=0)
    avg_confidence: float = Field(ge=0, le=1)


class RegistrySourceScanBacklogResponse(BaseModel):
    sources: list[RegistrySourceScanBacklogItem]
    total_sources: int = Field(ge=0)
    total_servers: int = Field(ge=0)


SourceScanBacklogItem = RegistrySourceScanBacklogItem
SourceScanBacklogResponse = RegistrySourceScanBacklogResponse


def get_registry_source_scan_backlog(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=7)

    rows = db.execute(
        text(
            """
            SELECT
                COALESCE(registry_source, '') AS registry_source,
                COUNT(*) AS server_count,
                COUNT(CASE WHEN verdict IS NOT NULL THEN 1 END)
                    AS servers_with_verdict,
                COUNT(CASE WHEN last_scanned IS NULL THEN 1 END)
                    AS never_scanned,
                COUNT(
                    CASE
                        WHEN last_scanned IS NOT NULL
                             AND last_scanned < :cutoff
                        THEN 1
                    END
                ) AS stale_scanned,
                COALESCE(AVG(scan_count), 0) AS avg_scan_count,
                COALESCE(AVG(confidence), 0) AS avg_confidence
            FROM mcp_server_registry
            GROUP BY COALESCE(registry_source, '')
            ORDER BY server_count DESC, registry_source ASC
            """
        ),
        {"cutoff": cutoff},
    ).mappings()

    sources: list[dict[str, Any]] = []
    for row in rows:
        server_count = int(row["server_count"])
        servers_with_verdict = int(row["servers_with_verdict"])
        coverage = (
            int(round(servers_with_verdict * 100.0 / server_count))
            if server_count
            else 0
        )

        sources.append(
            {
                "registry_source": str(row["registry_source"]),
                "server_count": server_count,
                "servers_with_verdict": servers_with_verdict,
                "never_scanned": int(row["never_scanned"]),
                "stale_scanned": int(row["stale_scanned"]),
                "verdict_coverage_pct": coverage,
                "avg_scan_count": round(float(row["avg_scan_count"]), 2),
                "avg_confidence": round(float(row["avg_confidence"]), 3),
            }
        )

    return {
        "sources": sources,
        "total_sources": len(sources),
        "total_servers": sum(item["server_count"] for item in sources),
    }


get_source_scan_backlog = get_registry_source_scan_backlog


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    local_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.utcnow()
    with local_session() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="scan-self-test-1",
                    name="Scan Self Test 1",
                    registry_source="alpha",
                    trust_score=0.9,
                    verdict="trusted",
                    confidence=0.91,
                    last_assessed=now,
                    last_seen=now,
                    last_scanned=now,
                    scan_count=2,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="scan-self-test-2",
                    name="Scan Self Test 2",
                    registry_source="alpha",
                    trust_score=0.6,
                    confidence=0.65,
                    last_assessed=now,
                    last_seen=now,
                    last_scanned=None,
                    scan_count=0,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="scan-self-test-3",
                    name="Scan Self Test 3",
                    registry_source="beta",
                    trust_score=0.4,
                    confidence=0.55,
                    last_assessed=now,
                    last_seen=now,
                    last_scanned=now - timedelta(days=8),
                    scan_count=1,
                    risk_tier="high",
                ),
            ]
        )
        db.commit()

    that_app = FastAPI()

    def override_get_session():
        with local_session() as db:
            yield db

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get(
        "/api/registry/source-scan-backlog",
        response_model=RegistrySourceScanBacklogResponse,
    )
    def source_scan_backlog(
        result: dict[str, Any] = Depends(get_registry_source_scan_backlog),
    ) -> dict[str, Any]:
        return result

    with TestClient(that_app) as client:
        response = client.get("/api/registry/source-scan-backlog")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_sources"] == 2
        assert payload["total_servers"] == 3
        assert sum(item["never_scanned"] for item in payload["sources"]) >= 1
        for item in payload["sources"]:
            coverage = item["verdict_coverage_pct"]
            assert isinstance(coverage, int)
            assert 0 <= coverage <= 100

    print("PASS")