from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session

try:
    from app.models import MCPServer as ServerModel
except ImportError:
    try:
        from app.models import McpServerRegistry as ServerModel
    except ImportError:
        try:
            from app.models import McpServer as ServerModel
        except ImportError:
            try:
                from app.models import McpServerRegistry as ServerModel
            except ImportError:
                ServerModel = None


CONFIDENCE_BANDS = (
    ("0.0-0.25", "Very Low", 0.0, 0.25),
    ("0.25-0.5", "Low", 0.25, 0.5),
    ("0.5-0.75", "Medium", 0.5, 0.75),
    ("0.75-1.0", "High", 0.75, 1.0),
)

UNKNOWN_BAND = "unknown"


def _confidence_band(confidence: Optional[float]) -> str:
    if confidence is None:
        return UNKNOWN_BAND
    if 0.0 <= confidence < 0.25:
        return "0.0-0.25"
    if 0.25 <= confidence < 0.5:
        return "0.25-0.5"
    if 0.5 <= confidence < 0.75:
        return "0.5-0.75"
    if 0.75 <= confidence <= 1.0:
        return "0.75-1.0"
    return UNKNOWN_BAND


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def get_confidence_bands(session: Session = Depends(get_session)) -> Dict[str, Any]:
    statement = text(
        """
        SELECT
            server_id,
            verdict,
            confidence,
            risk_tier,
            last_assessed
        FROM mcp_server_registry
        """
    )
    rows = session.execute(statement).mappings().all()

    band_rows: Dict[str, List[Dict[str, Any]]] = {
        band: [] for band, _label, _lower, _upper in CONFIDENCE_BANDS
    }
    band_rows[UNKNOWN_BAND] = []

    for row in rows:
        raw_confidence = row["confidence"]
        confidence = (
            None if raw_confidence is None else float(raw_confidence)
        )
        band = _confidence_band(confidence)
        band_rows[band].append(
            {
                "server_id": _json_value(row["server_id"]),
                "verdict": row["verdict"],
                "confidence": confidence,
                "risk_tier": row["risk_tier"],
                "last_assessed": _json_value(row["last_assessed"]),
            }
        )

    bands: List[Dict[str, Any]] = []
    for band, label, _lower, _upper in CONFIDENCE_BANDS:
        members = band_rows[band]
        confidences = [row["confidence"] for row in members]
        bands.append(
            {
                "band": band,
                "band_label": label,
                "count": len(members),
                "avg_confidence": (
                    round(sum(confidences) / len(confidences), 4)
                    if confidences
                    else None
                ),
                "verdicts": dict(
                    Counter(
                        row["verdict"]
                        for row in members
                        if row["verdict"] is not None
                    )
                ),
            }
        )

    unknown_members = band_rows[UNKNOWN_BAND]
    bands.append(
        {
            "band": UNKNOWN_BAND,
            "band_label": "Unknown",
            "count": len(unknown_members),
            "avg_confidence": None,
            "verdicts": dict(
                Counter(
                    row["verdict"]
                    for row in unknown_members
                    if row["verdict"] is not None
                )
            ),
        }
    )

    low_confidence = [
        row
        for row in rows
        if row["confidence"] is not None
        and float(row["confidence"]) < 0.5
        and row["verdict"] not in ("INSUFFICIENT",)
    ]
    low_confidence.sort(
        key=lambda row: (
            float(row["confidence"]),
            str(row["server_id"]),
        )
    )
    low_confidence = [
        {
            "server_id": _json_value(row["server_id"]),
            "verdict": row["verdict"],
            "confidence": float(row["confidence"]),
            "risk_tier": row["risk_tier"],
            "last_assessed": _json_value(row["last_assessed"]),
        }
        for row in low_confidence[:50]
    ]

    return {
        "bands": bands,
        "low_confidence": low_confidence,
        "total": len(rows),
    }


router = FastAPI()
app = router


@app.get("/api/verdict/confidence-bands")
def confidence_bands_endpoint(
    response: Dict[str, Any] = Depends(get_confidence_bands),
) -> Dict[str, Any]:
    return response


def run_self_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    verdict VARCHAR,
                    confidence FLOAT,
                    risk_tier VARCHAR,
                    last_assessed TIMESTAMP
                )
                """
            )
        )

    def override_get_session():
        with Session(test_engine) as session:
            yield session

    seed_rows = (
        {
            "server_id": "server-low-1",
            "verdict": "SUFFICIENT",
            "confidence": 0.1,
            "risk_tier": "LOW",
            "last_assessed": "2025-01-01T00:00:00",
        },
        {
            "server_id": "server-low-2",
            "verdict": "SUFFICIENT",
            "confidence": 0.3,
            "risk_tier": "MEDIUM",
            "last_assessed": "2025-01-02T00:00:00",
        },
        {
            "server_id": "server-high",
            "verdict": "SUFFICIENT",
            "confidence": 0.8,
            "risk_tier": "LOW",
            "last_assessed": "2025-01-03T00:00:00",
        },
        {
            "server_id": "server-unknown",
            "verdict": "SUFFICIENT",
            "confidence": None,
            "risk_tier": "UNKNOWN",
            "last_assessed": "2025-01-04T00:00:00",
        },
    )

    with Session(test_engine) as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    verdict,
                    confidence,
                    risk_tier,
                    last_assessed
                ) VALUES (
                    :server_id,
                    :verdict,
                    :confidence,
                    :risk_tier,
                    :last_assessed
                )
                """
            ),
            seed_rows,
        )
        session.commit()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/verdict/confidence-bands")
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["total"] == 4
            assert sum(band["count"] for band in payload["bands"]) == 4

            bands_by_name = {
                band["band"]: band for band in payload["bands"]
            }
            assert bands_by_name["unknown"]["count"] == 1
            assert bands_by_name["unknown"]["avg_confidence"] is None
            assert bands_by_name["0.0-0.25"]["count"] == 1
            assert bands_by_name["0.25-0.5"]["count"] == 1
            assert bands_by_name["0.75-1.0"]["count"] == 1

            low_ids = {
                row["server_id"] for row in payload["low_confidence"]
            }
            assert low_ids == {"server-low-1", "server-low-2"}
            assert len(payload["low_confidence"]) == 2
            assert payload["low_confidence"][0]["confidence"] == 0.1
            assert payload["low_confidence"][1]["confidence"] == 0.3
    finally:
        app.dependency_overrides.pop(get_session, None)
        test_engine.dispose()


def main() -> None:
    run_self_test()
    print("PASS")


if __name__ == "__main__":
    main()