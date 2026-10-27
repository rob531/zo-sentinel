from __future__ import annotations

import json
from typing import Any, List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import FreshnessStat


class FreshnessSurface(BaseModel):
    surface: str
    computed_at: str
    compute_seconds: float
    payload_keys: List[str]
    payload_size: int


class FreshnessSurfacesResponse(BaseModel):
    surfaces: List[FreshnessSurface]
    count: int


router = APIRouter()


def _decode_payload(payload: Any) -> Any:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except (TypeError, ValueError):
            return payload
    return payload


def _payload_size(payload: Any) -> int:
    try:
        serialized = json.dumps(payload)
    except (TypeError, ValueError):
        serialized = json.dumps(str(payload))
    return len(serialized.encode("utf-8"))


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessSurfacesResponse,
)
def list_freshness_surfaces(
    limit: int = Query(default=50, ge=1, le=500),
    db=Depends(get_session),
) -> FreshnessSurfacesResponse:
    rows = (
        db.query(FreshnessStat)
        .order_by(FreshnessStat.computed_at.desc())
        .limit(limit)
        .all()
    )

    surfaces = []
    for row in rows:
        payload = _decode_payload(row.payload)
        payload_keys = (
            [str(key) for key in payload.keys()]
            if isinstance(payload, dict)
            else []
        )
        computed_at = (
            row.computed_at.isoformat()
            if hasattr(row.computed_at, "isoformat")
            else str(row.computed_at)
        )
        surfaces.append(
            FreshnessSurface(
                surface=str(row.surface),
                computed_at=computed_at,
                compute_seconds=float(row.compute_seconds or 0.0),
                payload_keys=payload_keys,
                payload_size=_payload_size(payload),
            )
        )

    return FreshnessSurfacesResponse(surfaces=surfaces, count=len(surfaces))


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE freshness_stats (
                    surface VARCHAR NOT NULL,
                    payload JSON NOT NULL,
                    computed_at DATETIME NOT NULL,
                    compute_seconds FLOAT NOT NULL
                )
                """
            )
        )

    seeded_payloads = [
        {
            "kind": "alpha",
            "owner": "team-alpha",
            "raw_value": "alpha-payload-secret-7f31",
        },
        {
            "kind": "beta",
            "owner": "team-beta",
            "raw_value": "beta-payload-secret-9a82",
        },
        {
            "kind": "gamma",
            "owner": "team-gamma",
            "raw_value": "gamma-payload-secret-4c63",
        },
    ]
    seeded_rows = [
        ("surface-alpha", seeded_payloads[0], "2024-01-01T10:00:00", 1.25),
        ("surface-beta", seeded_payloads[1], "2024-01-02T10:00:00", 2.5),
        ("surface-gamma", seeded_payloads[2], "2024-01-03T10:00:00", 3.75),
    ]

    with engine.begin() as connection:
        for surface, payload, computed_at, compute_seconds in seeded_rows:
            connection.execute(
                text(
                    """
                    INSERT INTO freshness_stats
                        (surface, payload, computed_at, compute_seconds)
                    VALUES
                        (:surface, :payload, :computed_at, :compute_seconds)
                    """
                ),
                {
                    "surface": surface,
                    "payload": json.dumps(payload),
                    "computed_at": computed_at,
                    "compute_seconds": compute_seconds,
                },
            )

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["count"] == 3
        assert "surface-alpha" in [item["surface"] for item in body["surfaces"]]
        assert body["surfaces"][0]["surface"] == "surface-gamma"

        surfaces_by_name = {item["surface"]: item for item in body["surfaces"]}
        for surface, payload, _, _ in seeded_rows:
            item = surfaces_by_name[surface]
            assert set(item["payload_keys"]) == set(payload.keys())
            assert item["payload_size"] == len(json.dumps(payload).encode("utf-8"))

        response_body = response.text
        for payload in seeded_payloads:
            assert payload["raw_value"] not in response_body

    print("PASS")


if __name__ == "__main__":
    _run_self_test()