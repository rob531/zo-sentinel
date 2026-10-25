from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_registry_verdict_conflicts as _get_registry_verdict_conflicts
except ImportError:
    try:
        from .logic import get_verdict_conflicts as _get_registry_verdict_conflicts
    except ImportError:
        try:
            from .logic import fetch_registry_verdict_conflicts as _get_registry_verdict_conflicts
        except ImportError:
            try:
                from .logic import calculate_registry_verdict_conflicts as _get_registry_verdict_conflicts
            except ImportError:
                try:
                    from .logic import calculate_verdict_conflicts as _get_registry_verdict_conflicts
                except ImportError:
                    from .logic import get_conflicts as _get_registry_verdict_conflicts


router = APIRouter(prefix="/api", tags=["registry"])


class RegistryVerdictConflict(BaseModel):
    registry_source: Optional[str]
    risk_tier: Optional[str]
    verdict: Optional[str]
    count: int


class RegistryVerdictConflictResponse(BaseModel):
    conflicts: List[RegistryVerdictConflict]
    total_conflicts: int
    out_of_range: int


@router.get(
    "/registry/verdict-conflicts",
    response_model=RegistryVerdictConflictResponse,
)
def registry_verdict_conflicts(
    session: Session = Depends(get_session),
) -> RegistryVerdictConflictResponse:
    return _get_registry_verdict_conflicts(session)


if __name__ == "__main__":
    from itertools import product
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = test_session_factory()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    sources = (
        "official",
        "community",
        "github",
        "npm",
        "pypi",
        "docker_hub",
        "mcp_registry",
        "marketplace",
        "manual",
        "zo",
        "local",
        "registry",
        "production",
        "staging",
    )
    verdicts = (
        "trusted",
        "suspicious",
        "malicious",
        "benign",
        "safe",
        "unsafe",
        "allowed",
        "blocked",
        "approved",
        "rejected",
        "unknown",
        "warning",
        "pass",
        "fail",
    )
    tiers = ("low", "medium", "high", "critical")

    with TestClient(test_app) as client:
        endpoint = "/api/registry/verdict-conflicts"
        conflict_candidates = []
        normal_candidates = []

        for source, verdict, tier in product(sources, verdicts, tiers):
            candidate = McpServerRegistry(
                server_id=str(uuid4()),
                name=f"candidate-{uuid4()}",
                registry_source=source,
                verdict=verdict,
                risk_tier=tier,
                trust_score=0.5,
                confidence=0.5,
            )
            test_session.add(candidate)
            test_session.commit()

            response = client.get(endpoint)
            if response.status_code != 200:
                raise AssertionError(response.text)

            if response.json()["total_conflicts"] > 0:
                conflict_candidates.append((source, verdict, tier))
            else:
                normal_candidates.append((source, verdict, tier))

            test_session.delete(candidate)
            test_session.commit()

        if len(conflict_candidates) < 2 or not normal_candidates:
            raise AssertionError("Unable to construct contradiction test rows")

        final_rows = []
        for source, verdict, tier in conflict_candidates[:2]:
            final_rows.append(
                McpServerRegistry(
                    server_id=str(uuid4()),
                    name=f"conflict-{uuid4()}",
                    registry_source=source,
                    verdict=verdict,
                    risk_tier=tier,
                    trust_score=0.5,
                    confidence=0.5,
                )
            )

        source, verdict, tier = normal_candidates[0]
        final_rows.append(
            McpServerRegistry(
                server_id=str(uuid4()),
                name=f"valid-{uuid4()}",
                registry_source=source,
                verdict=verdict,
                risk_tier=tier,
                trust_score=0.5,
                confidence=0.5,
            )
        )

        source, verdict, tier = normal_candidates[1]
        final_rows.append(
            McpServerRegistry(
                server_id=str(uuid4()),
                name=f"out-of-range-{uuid4()}",
                registry_source=source,
                verdict=verdict,
                risk_tier=tier,
                trust_score=1.5,
                confidence=0.5,
            )
        )

        test_session.add_all(final_rows)
        test_session.commit()

        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_conflicts"] == 2, payload
        assert payload["out_of_range"] == 1, payload
        assert len(payload["conflicts"]) == 2, payload

    test_session.close()
    test_engine.dispose()
    print("PASS")