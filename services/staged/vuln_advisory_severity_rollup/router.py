"""HTTP surface for vulnerability advisory severity rollups."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

from .logic import severity_rollup

router = APIRouter(prefix="/api", tags=["vuln-advisory-severity-rollup"])


class SeverityRollupItem(BaseModel):
    severity: str
    advisories: int
    linked_servers: int


class EcosystemRollupItem(BaseModel):
    ecosystem: str
    advisories: int


class TopPackageRollupItem(BaseModel):
    package: str
    ecosystem: str
    advisories: int
    linked_servers: int


class SeverityRollupResponse(BaseModel):
    generated_at: datetime
    total_advisories: int
    by_severity: list[SeverityRollupItem]
    by_ecosystem: list[EcosystemRollupItem]
    top_packages: list[TopPackageRollupItem]


@router.get(
    "/vuln/advisories/severity-rollup",
    response_model=SeverityRollupResponse,
)
def get_severity_rollup(db: Session = Depends(get_session)) -> SeverityRollupResponse:
    payload: Any = severity_rollup(db)
    return SeverityRollupResponse.model_validate(payload)


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(bind=engine)
    VulnLink.__table__.create(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = TestingSession()

    now = datetime.now(timezone.utc)
    db.add_all(
        [
            VulnAdvisory(
                id="adv-critical",
                feed="test-feed",
                summary="critical advisory",
                severity="CRITICAL",
                ecosystem="PyPI",
                package="critical-package",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/critical",
                published_at=now,
                identities=[],
                content_hash="critical-hash",
            ),
            VulnAdvisory(
                id="adv-high-one",
                feed="test-feed",
                summary="high advisory one",
                severity="HIGH",
                ecosystem="npm",
                package="high-package-one",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/high-one",
                published_at=now,
                identities=[],
                content_hash="high-one-hash",
            ),
            VulnAdvisory(
                id="adv-high-two",
                feed="test-feed",
                summary="high advisory two",
                severity="HIGH",
                ecosystem="PyPI",
                package="high-package-two",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.invalid/high-two",
                published_at=now,
                identities=[],
                content_hash="high-two-hash",
            ),
        ]
    )
    db.add_all(
        [
            VulnLink(
                id=1,
                advisory_id="adv-critical",
                server_id="server-critical",
                match_basis="package",
                match_value="critical-package",
                match_confidence=0.95,
                linked_at=now,
            ),
            VulnLink(
                id=2,
                advisory_id="adv-high-one",
                server_id="server-high",
                match_basis="package",
                match_value="high-package-one",
                match_confidence=0.90,
                linked_at=now,
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: db

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/advisories/severity-rollup")
        assert response.status_code == 200
        body = response.json()
        assert body["total_advisories"] == 3
        high = next(item for item in body["by_severity"] if item["severity"] == "HIGH")
        assert high["advisories"] == 2
        critical = next(
            item for item in body["by_severity"] if item["severity"] == "CRITICAL"
        )
        assert critical["linked_servers"] == 1
        assert body["top_packages"]

    db.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()