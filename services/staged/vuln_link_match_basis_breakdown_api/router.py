from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnLink

from .logic import get_match_basis_breakdown

router = APIRouter(prefix="/api", tags=["vuln-links"])


class MatchBasisBreakdownRow(BaseModel):
    match_basis: str
    rows: int
    advisories: int
    servers: int
    avg_confidence: float


class MatchBasisBreakdownResponse(BaseModel):
    bases: list[MatchBasisBreakdownRow] = Field(default_factory=list)


@router.get(
    "/vuln-links/match-basis-breakdown",
    response_model=MatchBasisBreakdownResponse,
)
def match_basis_breakdown(
    session: Session = Depends(get_session),
) -> MatchBasisBreakdownResponse:
    result: Any = get_match_basis_breakdown(session)
    if isinstance(result, dict):
        bases = result.get("bases", [])
    else:
        bases = result

    return MatchBasisBreakdownResponse(bases=bases)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnLink.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    try:
        testing_session.execute(
            VulnLink.__table__.insert(),
            [
                {
                    "advisory_id": 101,
                    "server_id": 201,
                    "match_basis": "cpe",
                    "match_value": "cpe:/a:example:server:1.0",
                    "match_confidence": 0.9000,
                    "linked_at": datetime.utcnow(),
                },
                {
                    "advisory_id": 102,
                    "server_id": 202,
                    "match_basis": "cpe",
                    "match_value": "cpe:/a:example:server:2.0",
                    "match_confidence": 0.8000,
                    "linked_at": datetime.utcnow(),
                },
                {
                    "advisory_id": 103,
                    "server_id": 201,
                    "match_basis": "hostname",
                    "match_value": "host.example",
                    "match_confidence": 0.7000,
                    "linked_at": datetime.utcnow(),
                },
            ],
        )
        testing_session.commit()
    except Exception:
        testing_session.rollback()
        raise
    finally:
        testing_session.close()

    def override_get_session():
        db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI = __import__("fastapi").FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/vuln-links/match-basis-breakdown")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["bases"]) == 2
    assert sum(item["rows"] for item in payload["bases"]) == 3
    assert any(
        item["match_basis"] == "cpe" and item["rows"] == 2
        for item in payload["bases"]
    )
    print("PASS")