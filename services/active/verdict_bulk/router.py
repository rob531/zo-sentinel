# deps: fastapi, pydantic, sqlalchemy, sqlmodel
"""Verdict Bulk Service

Provides an endpoint to retrieve bulk verdict information for a list of server IDs.

Endpoints:
- POST /api/verdict_bulk
"""
from __future__ import annotations

import os
import sys as _sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # services/active/verdict_bulk -> services/active -> services -> zo_sentinel
if _root not in _sys.path:
    _sys.path.insert(0, _root)

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Dict

from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["verdict_bulk"])


class BulkRequest(BaseModel):
    server_ids: List[str] = Field(..., description="List of server IDs to fetch verdicts for")


class VerdictItem(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    confidence: float | None = None
    risk_tier: str | None = None
    axes: Dict[str, str] = Field(default_factory=dict, description="Axis name to label mapping")


@router.post("/verdict_bulk", response_model=List[VerdictItem])
def get_bulk_verdicts(request: BulkRequest, db: Session = Depends(get_session)):
    results: List[VerdictItem] = []
    for sid in request.server_ids:
        rec = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == sid).first()
        if not rec:
            continue
        axis_scores = (
            db.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == sid)
            .all()
        )
        axes = {score.axis_name: score.label for score in axis_scores}
        item = VerdictItem(
            server_id=sid,
            name=rec.name,
            verdict=rec.verdict,
            confidence=rec.confidence,
            risk_tier=rec.risk_tier,
            axes=axes,
        )
        results.append(item)
    return results


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlmodel import SQLModel, create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    with TestSessionLocal() as db:
        server = McpServerRegistry(
            server_id="srv-001",
            name="test-server",
            verdict="low",
            confidence=0.9,
            risk_tier="low",
        )
        db.add(server)
        axis = McpLlmAxisScore(
            server_id="srv-001",
            axis_name="overall_risk",
            label="low",
            model_version="v1",
        )
        db.add(axis)
        db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)
    resp = client.post("/api/verdict_bulk", json={"server_ids": ["srv-001"]})
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    if not data or data[0].get("server_id") != "srv-001":
        print("FAIL: Unexpected response payload")
        sys.exit(1)
    print("PASS")
    sys.exit(0)
