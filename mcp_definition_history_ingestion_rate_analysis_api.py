from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpDefinitionHistory

router = APIRouter(prefix="/api", tags=["definition-history"])


class IngestionRate(BaseModel):
    rate: int


def _compute_ingestion_rate(db: Session) -> int:
    """Compute the ingestion rate from the mcp_definition_history table."""
    count = db.execute(select(func.count()).select_from(McpDefinitionHistory)).scalar() or 0
    return count


@router.get("/definition-history-ingestion-rate", response_model=IngestionRate)
def get_ingestion_rate(db: Session = Depends(get_session)) -> IngestionRate:
    """Get the ingestion rate from the mcp_definition_history table."""
    rate = _compute_ingestion_rate(db)
    return IngestionRate(rate=rate)


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    for i in range(10):
        s.add(McpDefinitionHistory(id=i, server_id=f"srv{i}", definition="def{i}"))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/definition-history-ingestion-rate"); assert r.status_code == 200, r.text
    j = r.json()
    assert j["rate"] == 10, j
    print("PASS")
