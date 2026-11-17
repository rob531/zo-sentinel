from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore


class VersionEntry(BaseModel):
    decision_rule_version: str
    model_version: str
    score_rows: int
    server_count: int
    adapter_sha256_count: int
    first_scored_at: Optional[datetime] = None
    last_scored_at: Optional[datetime] = None


class VersionMixResponse(BaseModel):
    versions: list[VersionEntry]
    total_versions: int
    servers_scoring_multiple_versions: int
    generated_at: str


def get_version_mix(db: Session) -> VersionMixResponse:
    decision_version = func.coalesce(McpLlmAxisScore.decision_rule_version, "unknown")
    model_version = func.coalesce(McpLlmAxisScore.model_version, "unknown")

    rows = db.execute(
        select(
            decision_version.label("decision_rule_version"),
            model_version.label("model_version"),
            func.count(McpLlmAxisScore.id).label("score_rows"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.count(func.distinct(McpLlmAxisScore.adapter_sha256)).label(
                "adapter_sha256_count"
            ),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(decision_version, model_version)
        .order_by(decision_version, model_version)
    ).mappings()

    versions = [VersionEntry(**row) for row in rows]

    multiple_version_servers = db.execute(
        select(func.count())
        .select_from(
            select(McpLlmAxisScore.server_id)
            .group_by(McpLlmAxisScore.server_id)
            .having(
                func.count(
                    func.distinct(
                        func.coalesce(
                            McpLlmAxisScore.decision_rule_version, "unknown"
                        )
                    )
                )
                > 1
            )
            .subquery()
        )
    ).scalar_one()

    return VersionMixResponse(
        versions=versions,
        total_versions=len(versions),
        servers_scoring_multiple_versions=int(multiple_version_servers or 0),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    McpLlmAxisScore.__table__.create(bind=engine)

    with SessionLocal() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    axis_name="security",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    adapter_sha256="adapter-a",
                    server_id="server-a",
                    scored_at=datetime(2025, 1, 1, 10, 0, 0),
                ),
                McpLlmAxisScore(
                    id=2,
                    axis_name="security",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    adapter_sha256="adapter-a",
                    server_id="server-b",
                    scored_at=datetime(2025, 1, 2, 10, 0, 0),
                ),
                McpLlmAxisScore(
                    id=3,
                    axis_name="security",
                    decision_rule_version="rules-v2",
                    model_version="model-v2",
                    adapter_sha256="adapter-b",
                    server_id="server-a",
                    scored_at=datetime(2025, 1, 3, 10, 0, 0),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/axis/version-mix", response_model=VersionMixResponse)
    def axis_version_mix(db: Session = Depends(get_session)) -> VersionMixResponse:
        return get_version_mix(db)

    def override_get_session() -> Iterator[Session]:
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/version-mix")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_versions"] == 2, payload
    assert payload["servers_scoring_multiple_versions"] == 1, payload
    assert len(payload["versions"]) == 2, payload
    assert any(
        version["server_count"] == 1
        and version["adapter_sha256_count"] == 1
        for version in payload["versions"]
    ), payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    main()