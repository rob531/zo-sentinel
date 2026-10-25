from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class TrustScoreBand(BaseModel):
    band: str
    min: Optional[int] = None
    max: Optional[int] = None
    count: int
    pct: float


class TrustScoreBandsResponse(BaseModel):
    bands: list[TrustScoreBand]
    total: int
    scored_total: int
    unscored_total: int


def get_trust_score_bands(session: Session) -> TrustScoreBandsResponse:
    statement = __import__("sqlalchemy").select(
        McpServerRegistry.server_id,
        McpServerRegistry.trust_score,
    )
    rows = session.execute(statement).all()

    counts = OrderedDict(
        (
            ("0-20", 0),
            ("21-40", 0),
            ("41-60", 0),
            ("61-80", 0),
            ("81-100", 0),
            ("unscored", 0),
        )
    )

    scored_total = 0
    for row in rows:
        score = row.trust_score
        if score is None:
            counts["unscored"] += 1
            continue

        score = int(score)
        scored_total += 1
        if 0 <= score <= 20:
            counts["0-20"] += 1
        elif 21 <= score <= 40:
            counts["21-40"] += 1
        elif 41 <= score <= 60:
            counts["41-60"] += 1
        elif 61 <= score <= 80:
            counts["61-80"] += 1
        else:
            counts["81-100"] += 1

    total = len(rows)
    unscored_total = counts["unscored"]
    bands = []
    for band, count in counts.items():
        if band == "unscored":
            minimum = None
            maximum = None
        else:
            minimum, maximum = (int(part) for part in band.split("-"))
        bands.append(
            TrustScoreBand(
                band=band,
                min=minimum,
                max=maximum,
                count=count,
                pct=round((count / total) * 100, 2) if total else 0.0,
            )
        )

    return TrustScoreBandsResponse(
        bands=bands,
        total=total,
        scored_total=scored_total,
        unscored_total=unscored_total,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/registry/trust-score-bands",
    response_model=TrustScoreBandsResponse,
)
def trust_score_bands(session: Session = Depends(get_session)):
    return get_trust_score_bands(session)


app = FastAPI()
app.include_router(router)


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    trust_score INTEGER
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, trust_score)
                VALUES (:server_id, :trust_score)
                """
            ),
            [
                {"server_id": "server-1", "trust_score": 10},
                {"server_id": "server-2", "trust_score": 30},
                {"server_id": "server-3", "trust_score": 55},
                {"server_id": "server-4", "trust_score": 75},
                {"server_id": "server-5", "trust_score": 95},
                {"server_id": "server-6", "trust_score": None},
            ],
        )

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/registry/trust-score-bands")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["scored_total"] == 5, payload
            assert payload["unscored_total"] == 1, payload
            assert sum(band["count"] for band in payload["bands"]) == payload["total"], payload
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()