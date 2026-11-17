from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnLink

router = APIRouter()


class AgeBand(BaseModel):
    band: str
    count: int


class VulnLinkAgeResponse(BaseModel):
    days: int
    bands: list[AgeBand]
    total_links: int
    distinct_servers: int


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_band(linked_at: datetime, now: datetime) -> str:
    age = now - _as_utc(linked_at)
    if age <= timedelta(days=7):
        return "0_7d"
    if age <= timedelta(days=30):
        return "8_30d"
    if age <= timedelta(days=90):
        return "31_90d"
    if age <= timedelta(days=365):
        return "91_365d"
    return "older"


@router.get("/api/vuln/links/age", response_model=VulnLinkAgeResponse)
def get_vuln_link_age_buckets(
    days: int = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
) -> VulnLinkAgeResponse:
    now = datetime.now(timezone.utc)
    rows = session.query(VulnLink.linked_at, VulnLink.server_id).all()

    counts = {
        "0_7d": 0,
        "8_30d": 0,
        "31_90d": 0,
        "91_365d": 0,
        "older": 0,
    }
    servers = set()

    for linked_at, server_id in rows:
        counts[_age_band(linked_at, now)] += 1
        if server_id is not None:
            servers.add(server_id)

    return VulnLinkAgeResponse(
        days=days,
        bands=[AgeBand(band=band, count=counts[band]) for band in counts],
        total_links=len(rows),
        distinct_servers=len(servers),
    )


def run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    VulnLink.__table__.create(bind=engine)
    now = datetime.now(timezone.utc)

    with session_factory() as session:
        session.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id=101,
                    match_basis="advisory",
                    match_value="CVE-TEST-0001",
                    match_confidence=0.95,
                    linked_at=now,
                ),
                VulnLink(
                    id=2,
                    advisory_id=2,
                    server_id=102,
                    match_basis="advisory",
                    match_value="CVE-TEST-0002",
                    match_confidence=0.90,
                    linked_at=now - timedelta(days=10),
                ),
                VulnLink(
                    id=3,
                    advisory_id=3,
                    server_id=103,
                    match_basis="advisory",
                    match_value="CVE-TEST-0003",
                    match_confidence=0.85,
                    linked_at=now - timedelta(days=200),
                ),
            ]
        )
        session.commit()

    test_app = router.__class__ and __import__("fastapi").FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/links/age")
        assert response.status_code == 200
        payload = response.json()
        bands = {item["band"]: item["count"] for item in payload["bands"]}
        assert bands["0_7d"] == 1
        assert payload["total_links"] == 3
        assert payload["distinct_servers"] == 3

    engine.dispose()
    print("PASS")


def main() -> int:
    run_contract()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())