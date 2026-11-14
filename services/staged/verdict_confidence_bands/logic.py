"""logic.py -- verdict confidence band computation over the real registry."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BANDS = (
    ("0.0-0.25", "Very Low"),
    ("0.25-0.5", "Low"),
    ("0.5-0.75", "Medium"),
    ("0.75-1.0", "High"),
)


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _band_for(confidence: Optional[float]) -> str:
    if confidence is None or confidence < 0.0 or confidence > 1.0:
        return "unknown"
    if confidence < 0.25:
        return "0.0-0.25"
    if confidence < 0.5:
        return "0.25-0.5"
    if confidence < 0.75:
        return "0.5-0.75"
    return "0.75-1.0"


def calculate_confidence_bands(db: Session) -> Dict[str, Any]:
    """Build confidence-band aggregates and a capped low-confidence list."""
    servers = db.execute(select(McpServerRegistry)).scalars().all()

    band_values: Dict[str, List[Any]] = {
        band: [] for band, _label in _BANDS
    }
    band_values["unknown"] = []

    for server in servers:
        confidence = _as_float(server.confidence)
        band_values[_band_for(confidence)].append((server, confidence))

    bands: List[Dict[str, Any]] = []
    for band, label in _BANDS:
        entries = band_values[band]
        verdict_counts: Dict[str, int] = {}
        confidences: List[float] = []

        for server, confidence in entries:
            verdict = server.verdict or "unknown"
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            if confidence is not None:
                confidences.append(confidence)

        bands.append(
            {
                "band": band,
                "band_label": label,
                "count": len(entries),
                "verdicts": verdict_counts,
                "avg_confidence": (
                    sum(confidences) / len(confidences) if confidences else None
                ),
            }
        )

    unknown_entries = band_values["unknown"]
    unknown_verdict_counts: Dict[str, int] = {}
    for server, _confidence in unknown_entries:
        verdict = server.verdict or "unknown"
        unknown_verdict_counts[verdict] = unknown_verdict_counts.get(verdict, 0) + 1

    bands.append(
        {
            "band": "unknown",
            "band_label": "Unknown",
            "count": len(unknown_entries),
            "verdicts": unknown_verdict_counts,
            "avg_confidence": None,
        }
    )

    low_confidence: List[Dict[str, Any]] = []
    for server in servers:
        confidence = _as_float(server.confidence)
        if confidence is None or confidence >= 0.5:
            continue
        if server.verdict == "INSUFFICIENT":
            continue

        low_confidence.append(
            {
                "server_id": server.server_id,
                "verdict": server.verdict,
                "confidence": confidence,
                "risk_tier": server.risk_tier,
                "last_assessed": server.last_assessed,
            }
        )

    low_confidence.sort(
        key=lambda row: (row["confidence"], str(row["server_id"]))
    )
    low_confidence = low_confidence[:50]

    return {
        "bands": bands,
        "low_confidence": low_confidence,
        "total": len(servers),
    }


def get_confidence_bands(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return confidence bands for the verdict endpoint."""
    return calculate_confidence_bands(db)


def get_verdict_confidence_bands(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Compatibility alias for the verdict confidence-bands service."""
    return calculate_confidence_bands(db)


def build_confidence_bands(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers using the computation-style name."""
    return calculate_confidence_bands(db)


if __name__ == "__main__":
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()

    @test_app.get("/api/verdict/confidence-bands")
    def confidence_bands_endpoint(
        result: Dict[str, Any] = Depends(get_confidence_bands),
    ) -> Dict[str, Any]:
        return result

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="confidence-01",
                    name="Confidence One",
                    confidence=0.1,
                    verdict="LOW",
                    risk_tier="HIGH",
                ),
                McpServerRegistry(
                    server_id="confidence-02",
                    name="Confidence Two",
                    confidence=0.3,
                    verdict="MEDIUM",
                    risk_tier="MEDIUM",
                ),
                McpServerRegistry(
                    server_id="confidence-03",
                    name="Confidence Three",
                    confidence=0.8,
                    verdict="HIGH",
                    risk_tier="LOW",
                ),
                McpServerRegistry(
                    server_id="confidence-04",
                    name="Confidence Four",
                    confidence=None,
                    verdict="UNKNOWN",
                    risk_tier=None,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    response = client.get("/api/verdict/confidence-bands")

    assert response.status_code == 200
    payload = response.json()
    assert sum(band["count"] for band in payload["bands"]) == 4
    unknown_band = next(
        band for band in payload["bands"] if band["band"] == "unknown"
    )
    assert unknown_band["count"] == 1
    assert {
        row["server_id"] for row in payload["low_confidence"]
    } == {"confidence-01", "confidence-02"}
    print("PASS")