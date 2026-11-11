from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, Table, create_engine, insert, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


TRUST_SCORE_BANDS = (
    ("0-20", 0, 20),
    ("21-40", 21, 40),
    ("41-60", 41, 60),
    ("61-80", 61, 80),
    ("81-100", 81, 100),
)


def _band_for_score(score: Any) -> Optional[str]:
    if score is None:
        return "unscored"

    numeric_score = float(score)
    for band, minimum, maximum in TRUST_SCORE_BANDS:
        if minimum <= numeric_score <= maximum:
            return band
    return None


def get_trust_score_bands(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.trust_score,
        McpServerRegistry.risk_tier,
        McpServerRegistry.confidence,
    )
    rows = db.execute(statement).all()

    counts = {band: 0 for band, _, _ in TRUST_SCORE_BANDS}
    counts["unscored"] = 0
    scored_total = 0

    for row in rows:
        score = row.trust_score
        if score is not None:
            scored_total += 1

        band = _band_for_score(score)
        if band is not None:
            counts[band] += 1

    total = len(rows)
    unscored_total = counts["unscored"]
    denominator = total if total else 1

    bands: List[Dict[str, Any]] = []
    for band, minimum, maximum in TRUST_SCORE_BANDS:
        count = counts[band]
        bands.append(
            {
                "band": band,
                "min": minimum,
                "max": maximum,
                "count": count,
                "pct": round((count / denominator) * 100, 2),
            }
        )

    bands.append(
        {
            "band": "unscored",
            "min": None,
            "max": None,
            "count": unscored_total,
            "pct": round((unscored_total / denominator) * 100, 2),
        }
    )

    return {
        "bands": bands,
        "total": total,
        "scored_total": scored_total,
        "unscored_total": unscored_total,
    }


def compute_trust_score_bands(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_trust_score_bands(db)


def calculate_trust_score_bands(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_trust_score_bands(db)


def build_trust_score_bands(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_trust_score_bands(db)


def get_trust_score_band_distribution(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_trust_score_bands(db)


def compute_trust_score_band_distribution(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_trust_score_bands(db)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    source_columns = McpServerRegistry.__table__.columns
    test_table = Table(
        McpServerRegistry.__tablename__,
        metadata,
        Column(
            "server_id",
            source_columns["server_id"].type,
            nullable=source_columns["server_id"].nullable,
            primary_key=True,
        ),
        Column(
            "trust_score",
            source_columns["trust_score"].type,
            nullable=True,
        ),
        Column(
            "risk_tier",
            source_columns["risk_tier"].type,
            nullable=True,
        ),
        Column(
            "confidence",
            source_columns["confidence"].type,
            nullable=True,
        ),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            insert(test_table),
            [
                {
                    "server_id": "server-1",
                    "trust_score": 10,
                    "risk_tier": "low",
                    "confidence": 0.9,
                },
                {
                    "server_id": "server-2",
                    "trust_score": 30,
                    "risk_tier": "medium",
                    "confidence": 0.8,
                },
                {
                    "server_id": "server-3",
                    "trust_score": 55,
                    "risk_tier": "medium",
                    "confidence": 0.7,
                },
                {
                    "server_id": "server-4",
                    "trust_score": 75,
                    "risk_tier": "high",
                    "confidence": 0.6,
                },
                {
                    "server_id": "server-5",
                    "trust_score": 95,
                    "risk_tier": "low",
                    "confidence": 0.95,
                },
                {
                    "server_id": "server-6",
                    "trust_score": None,
                    "risk_tier": "unknown",
                    "confidence": None,
                },
            ],
        )

    def override_get_session():
        with Session(engine) as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/trust-score-bands")
    def trust_score_bands(db: Session = Depends(get_session)):
        return get_trust_score_bands(db)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/trust-score-bands")
        assert response.status_code == 200
        payload = response.json()
        assert payload["scored_total"] == 5
        assert payload["unscored_total"] == 1
        assert payload["total"] == 6
        assert sum(band["count"] for band in payload["bands"]) == payload["total"]

    print("PASS")


if __name__ == "__main__":
    _self_test()