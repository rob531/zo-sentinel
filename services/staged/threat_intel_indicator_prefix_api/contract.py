from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


FAMILY_ORDER = (
    "ipv4",
    "ipv6",
    "domain",
    "url",
    "sha256",
    "md5",
    "email",
    "cve",
    "other",
)

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)
_MD5_RE = re.compile(r"^[a-f0-9]{32}$", re.IGNORECASE)


class IndicatorPrefixFamily(BaseModel):
    family: str
    count: int
    distinct_pulses: int
    latest_fetched_at: Optional[datetime]


class IndicatorPrefixResponse(BaseModel):
    families: List[IndicatorPrefixFamily]
    total_indicators: int
    generated_at: datetime


def classify_indicator(indicator_value: object) -> str:
    """Classify an indicator using deterministic, network-free string rules."""
    if indicator_value is None:
        return "other"

    value = str(indicator_value).strip()
    if not value:
        return "other"

    lowered = value.lower()

    if "://" in lowered:
        scheme = lowered.split("://", 1)[0]
        remainder = value.split("://", 1)[1]
        if scheme in {"http", "https"} and remainder:
            return "url"

    try:
        parsed = ipaddress.ip_address(value)
        if parsed.version == 4:
            return "ipv4"
        if parsed.version == 6:
            return "ipv6"
    except ValueError:
        pass

    if _EMAIL_RE.match(value):
        return "email"

    if _CVE_RE.match(value):
        return "cve"

    if _SHA256_RE.match(value):
        return "sha256"

    if _MD5_RE.match(value):
        return "md5"

    domain = value.rstrip(".").lower()
    if _DOMAIN_RE.match(domain):
        return "domain"

    return "other"


def get_indicator_prefix_families(session: Session) -> List[IndicatorPrefixFamily]:
    rows = session.execute(
        select(
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.fetched_at,
        )
    ).all()

    counts: Dict[str, int] = {}
    pulses: Dict[str, Set[object]] = {}
    latest: Dict[str, datetime] = {}

    for row in rows:
        family = classify_indicator(row.indicator_value)
        counts[family] = counts.get(family, 0) + 1

        if row.pulse_id is not None:
            pulses.setdefault(family, set()).add(row.pulse_id)

        if row.fetched_at is not None:
            previous = latest.get(family)
            if previous is None or row.fetched_at > previous:
                latest[family] = row.fetched_at

    families = []
    for family in FAMILY_ORDER:
        if family not in counts:
            continue
        families.append(
            IndicatorPrefixFamily(
                family=family,
                count=counts[family],
                distinct_pulses=len(pulses.get(family, set())),
                latest_fetched_at=latest.get(family),
            )
        )

    return families


router = APIRouter(prefix="/api")


@router.get(
    "/threat-intel/indicator-prefix",
    response_model=IndicatorPrefixResponse,
)
def indicator_prefix(
    session: Session = Depends(get_session),
) -> IndicatorPrefixResponse:
    families = get_indicator_prefix_families(session)
    return IndicatorPrefixResponse(
        families=families,
        total_indicators=sum(item.count for item in families),
        generated_at=datetime.now(timezone.utc),
    )


app = FastAPI(title="threat_intel_indicator_prefix_api")
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI(title="threat_intel_indicator_prefix_api_test")
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    seeded = [
        ThreatIntelRef(
            id=1,
            indicator_type="ipv4",
            indicator_value="192.0.2.10",
            pulse_id="pulse-ip-1",
            pulse_name="ip pulse 1",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/ip",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ipv4",
            indicator_value="198.51.100.25",
            pulse_id="pulse-ip-2",
            pulse_name="ip pulse 2",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/ip-2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-domain",
            pulse_name="domain pulse",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/domain",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="sha256",
            indicator_value="a" * 64,
            pulse_id="pulse-hash",
            pulse_name="hash pulse",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/hash",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=5,
            indicator_type="url",
            indicator_value="https://example.test/path",
            pulse_id="pulse-url",
            pulse_name="url pulse",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/url",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=6,
            indicator_type="cve",
            indicator_value="CVE-2024-12345",
            pulse_id="pulse-cve",
            pulse_name="cve pulse",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/cve",
            fetched_at=now,
        ),
    ]

    session = TestingSession()
    try:
        session.add_all(seeded)
        session.commit()
    finally:
        session.close()

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/indicator-prefix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_indicators"] == 6

        ipv4 = next(item for item in payload["families"] if item["family"] == "ipv4")
        assert ipv4["count"] == 2

        allowed = set(FAMILY_ORDER)
        assert all(item["family"] in allowed for item in payload["families"])

    print("PASS")


if __name__ == "__main__":
    _run_self_test()