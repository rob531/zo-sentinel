from __future__ import annotations

import inspect
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_registry_source_risk_cross_tab as _get_source_risk_cross_tab
except ImportError:
    try:
        from .logic import get_source_risk_crosstab as _get_source_risk_cross_tab
    except ImportError:
        from .logic import get_source_risk_cross_tab as _get_source_risk_cross_tab


router = APIRouter(prefix="/api")


@router.get("/registry/source-risk-crosstab")
async def registry_source_risk_crosstab(
    registry_source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> dict:
    result = _get_source_risk_cross_tab(
        session,
        registry_source=registry_source,
        limit=limit,
    )
    if inspect.isawaitable(result):
        result = await result

    if isinstance(result, list):
        result = {"sources": result}

    result = dict(result)
    result.setdefault(
        "generated_at",
        datetime.now(timezone.utc).isoformat(),
    )
    result.setdefault("sources", [])
    return result


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    db = TestingSessionLocal()
    db.add_all(
        [
            McpServerRegistry(
                server_id="self-test-1",
                registry_source="source-a",
                risk_tier="TRUSTED_GENERAL",
                trust_score=90.0,
            ),
            McpServerRegistry(
                server_id="self-test-2",
                registry_source="source-b",
                risk_tier="TRUSTED_GENERAL",
                trust_score=80.0,
            ),
            McpServerRegistry(
                server_id="self-test-3",
                registry_source="source-b",
                risk_tier="RISK_ELEVATED",
                trust_score=40.0,
            ),
        ]
    )
    db.commit()

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = lambda: db

    with TestClient(application) as client:
        response = client.get("/api/registry/source-risk-crosstab")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["sources"]) == 2
        assert sum(source["total"] for source in payload["sources"]) == 3
        source_b = next(
            source for source in payload["sources"] if source["registry_source"] == "source-b"
        )
        assert source_b["tiers"]["TRUSTED_GENERAL"] == 1
        assert source_b["total"] == 2

    db.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()