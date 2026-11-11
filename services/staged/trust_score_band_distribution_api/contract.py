from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BAND_RANGES = (
    (0, 9),
    (10, 19),
    (20, 29),
    (30, 39),
    (40, 49),
    (50, 59),
    (60, 69),
    (70, 79),
    (80, 89),
    (90, 100),
)


class TrustBand(BaseModel):
    band: str
    lo: int
    hi: int
    count: int
    by_tier: dict[str, int]


class TrustScoreBandDistribution(BaseModel):
    bands: list[TrustBand]
    total: int
    unscored: int
    generated_at: str


router = APIRouter(prefix="/api", tags=["trust"])


def _normalize_tier(value: object) -> str:
    raw_value = getattr(value, "value", value)
    if raw_value is None:
        return "UNKNOWN"
    tier = str(raw_value).strip().upper()
    return tier or "UNKNOWN"


def calculate_band_distribution(session: Session) -> TrustScoreBandDistribution:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.last_assessed,
        )
    ).all()

    band_counts = [0 for _ in BAND_RANGES]
    tier_counts: list[dict[str, int]] = [{} for _ in BAND_RANGES]
    unscored = 0

    for row in rows:
        if row.trust_score is None:
            unscored += 1
            continue

        score = float(row.trust_score)
        if score < 0:
            band_index = 0
        elif score >= 100:
            band_index = len(BAND_RANGES) - 1
        else:
            band_index = int(score // 10)

        band_counts[band_index] += 1
        tier = _normalize_tier(row.risk_tier)
        counts = tier_counts[band_index]
        counts[tier] = counts.get(tier, 0) + 1

    bands = [
        TrustBand(
            band=f"{lo}-{hi}",
            lo=lo,
            hi=hi,
            count=band_counts[index],
            by_tier={
                tier: tier_counts[index][tier]
                for tier in sorted(tier_counts[index])
            },
        )
        for index, (lo, hi) in enumerate(BAND_RANGES)
    ]

    return TrustScoreBandDistribution(
        bands=bands,
        total=len(rows),
        unscored=unscored,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/trust/band-distribution",
    response_model=TrustScoreBandDistribution,
)
def trust_score_band_distribution(
    session: Session = Depends(get_session),
) -> TrustScoreBandDistribution:
    return calculate_band_distribution(session)


def run() -> None:
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
    session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI(title="Trust Score Band Distribution Contract")
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    fixtures = (
        ("contract-score-0", 0, "LOW"),
        ("contract-score-45", 45, "MEDIUM"),
        ("contract-score-50", 50, "HIGH"),
        ("contract-score-77", 77, "LOW"),
        ("contract-score-100", 100, "MEDIUM"),
        ("contract-score-null", None, "HIGH"),
    )
    assessed_at = datetime.now(timezone.utc)

    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id=server_id,
                    name=f"Contract server {index}",
                    trust_score=trust_score,
                    risk_tier=risk_tier,
                    last_assessed=assessed_at,
                )
                for index, (server_id, trust_score, risk_tier) in enumerate(
                    fixtures, start=1
                )
            ]
        )
        session.commit()

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/trust/band-distribution")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total"] == 6
            assert payload["unscored"] == 1

            expected_bands = {
                "0-9": (0, 9),
                "10-19": (10, 19),
                "20-29": (20, 29),
                "30-39": (30, 39),
                "40-49": (40, 49),
                "50-59": (50, 59),
                "60-69": (60, 69),
                "70-79": (70, 79),
                "80-89": (80, 89),
                "90-100": (90, 100),
            }
            bands_by_name = {band["band"]: band for band in payload["bands"]}

            assert set(bands_by_name) == set(expected_bands)
            for name, (lo, hi) in expected_bands.items():
                assert bands_by_name[name]["lo"] == lo
                assert bands_by_name[name]["hi"] == hi
                assert "count" in bands_by_name[name]
                assert "by_tier" in bands_by_name[name]

            assert bands_by_name["40-49"]["count"] == 1
            assert sum(band["count"] for band in payload["bands"]) + payload[
                "unscored"
            ] == payload["total"]
            datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
    finally:
        test_engine.dispose()


if __name__ == "__main__":
    run()
    print("PASS")