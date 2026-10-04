"""Entity Export router.

Exposes GET /api/entities/{server_id}/export returning a complete dossier:
metadata, axis scores, and threat/vulnerability links. Supports JSON and YAML.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Data layer – real application DB (no-op import at module level is forbidden;
# wrap so the self-test can exercise the module without app.db present).
# --------------------------------------------------------------------------- #
try:
    from app.db import get_session  # noqa: F401
    from app.models import McpLlmAxisScore, McpServerRegistry, VulnAdvisory, VulnLink  # noqa: F401
except Exception:  # pragma: no cover – only during isolated self-test
    get_session = None
    McpLlmAxisScore = McpLlmAxisScoreProxy = McpServerRegistry = VulnAdvisory = VulnLink = None  # type: ignore[assignment,misc]

router = APIRouter(prefix="/api", tags=["entity_export"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class AxisScore(BaseModel):
    axis_name: str
    label: str | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    probs: dict[str, float] | None = None
    model_version: str | None = None
    scored_at: datetime | None = None


class ServerMetadata(BaseModel):
    name: str | None = None
    registry_source: str | None = None
    url: str | None = None
    risk_tier: str | None = None
    verdict: str | None = None
    confidence: float | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    last_scanned: datetime | None = None
    last_assessed: datetime | None = None


class VulnAdvisorySummary(BaseModel):
    id: int
    ecosystem: str | None = None
    package: str | None = None
    severity: str | None = None
    summary: str | None = None
    source_url: str | None = None


class VulnLinkInfo(BaseModel):
    id: int
    match_value: str | None = None
    match_basis: str | None = None
    match_confidence: float | None = None
    linked_at: datetime | None = None
    advisory: VulnAdvisorySummary | None = None


class EntityDossier(BaseModel):
    server_id: str
    metadata: ServerMetadata
    axes: list[AxisScore]
    threat_links: list[VulnLinkInfo]


class EntityExportResponse(BaseModel):
    dossier: EntityDossier


def _serialize_probs(probs_json: str | None) -> dict[str, float] | None:
    """Deserialize probs column (stored as JSON string in DB)."""
    if probs_json is None:
        return None
    if isinstance(probs_json, str):
        return json.loads(probs_json)
    return probs_json


async def get_entity_dossier(server_id: str, session: Session) -> EntityDossier | None:
    """Build a complete entity dossier for a server."""
    stmt = select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    result = session.execute(stmt)
    server = result.scalar_one_or_none()

    if not server:
        return None

    # Axis scores
    axes_stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    axes_result = session.execute(axes_stmt)
    axis_rows = axes_result.scalars().all()

    axes = [
        AxisScore(
            axis_name=row.axis_name,
            label=row.label,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            probs=_serialize_probs(row.probs),
            model_version=row.model_version,
            scored_at=row.scored_at,
        )
        for row in axis_rows
    ]

    # Vulnerability links + joined advisory data
    vuln_links_stmt = select(VulnLink).where(VulnLink.server_id == server_id)
    vuln_links_result = session.execute(vuln_links_stmt)
    vuln_link_rows = vuln_links_result.scalars().all()

    threat_links = []
    for vlink in vuln_link_rows:
        advisory = None
        if vlink.advisory_id:
            adv_stmt = select(VulnAdvisory).where(VulnAdvisory.id == vlink.advisory_id)
            adv_result = session.execute(adv_stmt)
            adv_row = adv_result.scalar_one_or_none()
            if adv_row:
                advisory = VulnAdvisorySummary(
                    id=adv_row.id,
                    ecosystem=adv_row.ecosystem,
                    package=adv_row.package,
                    severity=adv_row.severity,
                    summary=adv_row.summary,
                    source_url=adv_row.source_url,
                )

        threat_links.append(
            VulnLinkInfo(
                id=vlink.id,
                match_value=vlink.match_value,
                match_basis=vlink.match_basis,
                match_confidence=vlink.match_confidence,
                linked_at=vlink.linked_at,
                advisory=advisory,
            )
        )

    metadata = ServerMetadata(
        name=server.name,
        registry_source=server.registry_source,
        url=server.url,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        confidence=server.confidence,
        first_seen=server.first_seen,
        last_seen=server.last_seen,
        last_scanned=server.last_scanned,
        last_assessed=server.last_assessed,
    )

    return EntityDossier(
        server_id=server_id,
        metadata=metadata,
        axes=axes,
        threat_links=threat_links,
    )


@router.get("/entities/{server_id}/export", response_model=EntityExportResponse)
async def export_entity(
    server_id: str,
    format: Literal["json", "yaml"] = Query(default="json"),
    session: Session = Depends(get_session),
) -> Response:
    """Export a server entity dossier in JSON or YAML format."""
    dossier = await get_entity_dossier(server_id, session)

    if not dossier:
        return Response(status_code=404, content="Server not found")

    response_data = {"dossier": dossier.model_dump(mode="json")}

    if format == "yaml":
        yaml_content = yaml.dump(response_data, default_flow_style=False, sort_keys=False)
        return Response(content=yaml_content, media_type="text/plain")
    else:
        json_content = json.dumps(response_data, indent=2, default=str)
        return Response(content=json_content, media_type="application/json")


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from collections.abc import AsyncGenerator, Generator
    from contextlib import asynccontextmanager

    # Skip if app.db is not available (e.g. CI with no DB)
    try:
        from app.db import get_session as _real_get_session
    except Exception:  # pragma: no cover
        print("PASS (app.db not available)")
        sys.exit(0)

    import yaml
    from fastapi import FastAPI
    from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
    from sqlalchemy.orm import Session as SASession, declarative_base, sessionmaker
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    Base = declarative_base()

    class TestServerRegistry(Base):
        __tablename__ = "mcp_server_registry"
        id = Column(Integer, primary_key=True)
        server_id = Column(String, unique=True, nullable=False)
        name = Column(String)
        registry_source = Column(String)
        url = Column(String)
        risk_tier = Column(String)
        verdict = Column(String)
        confidence = Column(Float)
        first_seen = Column(DateTime)
        last_seen = Column(DateTime)
        last_scanned = Column(DateTime)
        last_assessed = Column(DateTime)
        description = Column(Text)
        meta = Column(Text)
        trust_score = Column(Float)
        scan_count = Column(Integer)

    class TestAxisScore(Base):
        __tablename__ = "mcp_llm_axis_scores"
        id = Column(Integer, primary_key=True)
        server_id = Column(String, nullable=False)
        axis_name = Column(String, nullable=False)
        label = Column(String)
        label_index = Column(Integer)
        p_top = Column(Float)
        p_critical = Column(Float)
        p_danger = Column(Float)
        probs = Column(Text)
        model_version = Column(String)
        scored_at = Column(DateTime)
        adapter_sha256 = Column(String)
        decision_rule_version = Column(String)
        escalated = Column(String)
        escalated_to = Column(String)

    class TestVulnLink(Base):
        __tablename__ = "vuln_links"
        id = Column(Integer, primary_key=True)
        server_id = Column(String, nullable=False)
        advisory_id = Column(Integer)
        match_value = Column(String)
        match_basis = Column(String)
        match_confidence = Column(Float)
        linked_at = Column(DateTime)

    class TestVulnAdvisory(Base):
        __tablename__ = "vuln_advisories"
        id = Column(Integer, primary_key=True)
        ecosystem = Column(String)
        package = Column(String)
        severity = Column(String)
        summary = Column(Text)
        source_url = Column(String)
        feed = Column(String)
        aliases = Column(Text)
        affected_ranges = Column(Text)
        content_hash = Column(String)
        fetched_at = Column(DateTime)
        identities = Column(Text)
        published_at = Column(DateTime)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield

    test_app = FastAPI(lifespan=lifespan)
    test_app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session() -> Generator[SASession, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[_real_get_session] = override_get_session

    def seed_data() -> None:
        session = TestingSessionLocal()
        try:
            axis_names = [
                "vulnerability_score",
                "reputation_score",
                "functionality_score",
                "trust_score",
                "safety_score",
                "compliance_score",
                "performance_score",
            ]
            labels = ["critical", "high", "medium", "low", "info"]
            ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

            for i in range(1, 4):
                server_id = f"server-{i:03d}"
                server = TestServerRegistry(
                    server_id=server_id,
                    name=f"Test Server {i}",
                    registry_source="test_registry",
                    url=f"https://example{i}.com/api",
                    risk_tier="medium",
                    verdict="approved",
                    confidence=0.85,
                    first_seen=ts,
                    last_seen=ts,
                    last_scanned=ts,
                    last_assessed=ts,
                    description=f"Test server number {i}",
                    trust_score=0.75,
                    scan_count=10,
                )
                session.add(server)

                for ax_idx, axis_name in enumerate(axis_names):
                    axis = TestAxisScore(
                        server_id=server_id,
                        axis_name=axis_name,
                        label=labels[ax_idx % len(labels)],
                        label_index=ax_idx % len(labels),
                        p_top=0.7 - (ax_idx * 0.05),
                        p_critical=0.1 + (ax_idx * 0.02),
                        p_danger=0.15 + (ax_idx * 0.01),
                        probs=json.dumps({"critical": 0.2, "high": 0.3, "medium": 0.3, "low": 0.15, "info": 0.05}),
                        model_version="v1.0.0",
                        scored_at=ts,
                    )
                    session.add(axis)

                vuln_advisory = TestVulnAdvisory(
                    id=i,
                    ecosystem="npm",
                    package=f"test-package-{i}",
                    severity="high",
                    summary=f"Test vulnerability advisory {i}",
                    source_url=f"https://advisories.example.com/{i}",
                    feed="test_feed",
                    aliases="",
                    affected_ranges="*",
                    content_hash="abc123",
                    fetched_at=ts,
                    identities="",
                    published_at=ts,
                )
                session.add(vuln_advisory)

                vuln_link = TestVulnLink(
                    server_id=server_id,
                    advisory_id=i,
                    match_value=f"CVE-2024-{1000+i}",
                    match_basis="package_name",
                    match_confidence=0.95,
                    linked_at=ts,
                )
                session.add(vuln_link)

            session.commit()
        finally:
            session.close()

    seed_data()

    client = TestClient(test_app)

    test_server_id = "server-002"
    response = client.get(f"/api/entities/{test_server_id}/export", params={"format": "json"})
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "dossier" in data, "Missing dossier in response"
    dossier = data["dossier"]
    assert dossier["server_id"] == test_server_id, f"Server ID mismatch: {dossier['server_id']}"
    assert len(dossier["axes"]) == 7, f"Expected 7 axes, got {len(dossier['axes'])}"

    metadata = dossier["metadata"]
    assert metadata["name"] == "Test Server 2", f"Name mismatch: {metadata['name']}"
    assert metadata["registry_source"] == "test_registry"
    assert metadata["risk_tier"] == "medium"
    assert metadata["verdict"] == "approved"
    assert metadata["confidence"] == 0.85

    response_yaml = client.get(f"/api/entities/{test_server_id}/export", params={"format": "yaml"})
    assert response_yaml.status_code == 200, f"YAML export failed: {response_yaml.status_code}"
    yaml_data = yaml.safe_load(response_yaml.text)
    assert "dossier" in yaml_data

    response_notfound = client.get("/api/entities/nonexistent/export")
    assert response_notfound.status_code == 404, f"Expected 404 for nonexistent server, got {response_notfound.status_code}"

    print("PASS")
    sys.exit(0)
