from collections.abc import Iterator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import case, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnLink


BANDS = (
    ("0.0-0.25", 0.0, 0.25),
    ("0.25-0.5", 0.25, 0.5),
    ("0.5-0.75", 0.5, 0.75),
    ("0.75-1.0", 0.75, 1.0),
)


def get_link_confidence_bands(session: Session = Depends(get_session)) -> dict:
    band = case(
        (VulnLink.match_confidence < 0.25, "0.0-0.25"),
        (VulnLink.match_confidence < 0.5, "0.25-0.5"),
        (VulnLink.match_confidence < 0.75, "0.5-0.75"),
        else_="0.75-1.0",
    )

    rows = session.execute(
        select(
            band.label("band"),
            func.count(VulnLink.id).label("count"),
            func.count(func.distinct(VulnLink.advisory_id)).label(
                "distinct_advisories"
            ),
            func.count(func.distinct(VulnLink.server_id)).label("distinct_servers"),
        ).group_by(band)
    ).all()

    counts = {
        row.band: {
            "band": row.band,
            "count": int(row.count),
            "distinct_advisories": int(row.distinct_advisories),
            "distinct_servers": int(row.distinct_servers),
        }
        for row in rows
    }

    bands = []
    for name, _, _ in BANDS:
        bands.append(
            counts.get(
                name,
                {
                    "band": name,
                    "count": 0,
                    "distinct_advisories": 0,
                    "distinct_servers": 0,
                },
            )
        )

    total = int(
        session.execute(select(func.count(VulnLink.id))).scalar_one()
    )
    mean_confidence = session.execute(
        select(func.avg(VulnLink.match_confidence))
    ).scalar_one()

    return {
        "bands": bands,
        "mean_confidence": (
            float(mean_confidence) if mean_confidence is not None else None
        ),
        "total": total,
    }


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnLink.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    with testing_session() as session:
        session.add_all(
            [
                VulnLink(
                    advisory_id="ADV-1",
                    match_basis="cve",
                    match_confidence=0.1,
                    match_value="CVE-2024-0001",
                    server_id="server-1",
                ),
                VulnLink(
                    advisory_id="ADV-2",
                    match_basis="cve",
                    match_confidence=0.6,
                    match_value="CVE-2024-0002",
                    server_id="server-2",
                ),
                VulnLink(
                    advisory_id="ADV-3",
                    match_basis="cve",
                    match_confidence=0.9,
                    match_value="CVE-2024-0003",
                    server_id="server-3",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/vuln/link-confidence-bands")
    def link_confidence_bands(session: Session = Depends(get_session)) -> dict:
        return get_link_confidence_bands(session)

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/link-confidence-bands")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert sum(band["count"] for band in payload["bands"]) == 3
        assert sum(
            1 for band in payload["bands"] if band["count"] > 0
        ) == 3
        assert payload["mean_confidence"] is not None

    print("PASS")


if __name__ == "__main__":
    _self_test()