from datetime import datetime
import json

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def get_ecosystem_matrix(session=Depends(get_session)):
    rows = (
        session.query(
            VulnAdvisory.ecosystem,
            VulnAdvisory.severity,
        )
        .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnAdvisory.ecosystem, VulnAdvisory.severity)
        .order_by(VulnAdvisory.ecosystem, VulnAdvisory.severity)
        .all()
    )

    advisory_counts = dict(
        session.query(
            VulnAdvisory.ecosystem,
            VulnAdvisory.severity,
        )
        .with_entities(
            VulnAdvisory.ecosystem,
            VulnAdvisory.severity,
        )
        .all()
    )
    del advisory_counts

    result = []
    for ecosystem, severity in rows:
        advisory_count = (
            session.query(VulnAdvisory.id)
            .filter(
                VulnAdvisory.ecosystem == ecosystem,
                VulnAdvisory.severity == severity,
            )
            .distinct()
            .count()
        )
        linked_server_count = (
            session.query(VulnLink.server_id)
            .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
            .filter(
                VulnAdvisory.ecosystem == ecosystem,
                VulnAdvisory.severity == severity,
            )
            .distinct()
            .count()
        )
        result.append(
            {
                "ecosystem": ecosystem,
                "severity": severity,
                "advisory_count": advisory_count,
                "linked_server_count": linked_server_count,
            }
        )

    return result


def build_ecosystem_matrix(session=Depends(get_session)):
    return get_ecosystem_matrix(session=session)


def _json_value(column, value):
    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        return json.dumps(value)

    if python_type is list:
        return value
    if python_type is dict:
        return {"values": value}
    return json.dumps(value)


def _self_test():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)
    TestSession = sessionmaker(bind=engine)

    advisory_table = VulnAdvisory.__table__
    link_table = VulnLink.__table__

    with TestSession() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="test",
                    severity="high",
                    ecosystem="npm",
                    package="package-one",
                    published_at=datetime(2025, 1, 1),
                    fetched_at=datetime(2025, 1, 2),
                    identities=_json_value(advisory_table.c.identities, ["CVE-0001"]),
                    affected_ranges=_json_value(
                        advisory_table.c.affected_ranges, ["<1.0.0"]
                    ),
                    aliases=_json_value(advisory_table.c.aliases, ["TEST-0001"]),
                    content_hash="test-hash-1",
                    source_url="https://example.test/advisory/1",
                    summary="Test advisory one",
                ),
                VulnAdvisory(
                    id=2,
                    feed="test",
                    severity="high",
                    ecosystem="npm",
                    package="package-two",
                    published_at=datetime(2025, 1, 3),
                    fetched_at=datetime(2025, 1, 4),
                    identities=_json_value(advisory_table.c.identities, ["CVE-0002"]),
                    affected_ranges=_json_value(
                        advisory_table.c.affected_ranges, ["<2.0.0"]
                    ),
                    aliases=_json_value(advisory_table.c.aliases, ["TEST-0002"]),
                    content_hash="test-hash-2",
                    source_url="https://example.test/advisory/2",
                    summary="Test advisory two",
                ),
                VulnAdvisory(
                    id=3,
                    feed="test",
                    severity="critical",
                    ecosystem="pypi",
                    package="package-three",
                    published_at=datetime(2025, 1, 5),
                    fetched_at=datetime(2025, 1, 6),
                    identities=_json_value(advisory_table.c.identities, ["CVE-0003"]),
                    affected_ranges=_json_value(
                        advisory_table.c.affected_ranges, ["<3.0.0"]
                    ),
                    aliases=_json_value(advisory_table.c.aliases, ["TEST-0003"]),
                    content_hash="test-hash-3",
                    source_url="https://example.test/advisory/3",
                    summary="Test advisory three",
                ),
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id=501,
                    match_confidence=0.95,
                    linked_at=datetime(2025, 1, 7),
                    match_basis="package",
                    match_value="package-one",
                ),
                VulnLink(
                    id=2,
                    advisory_id=2,
                    server_id=501,
                    match_confidence=0.90,
                    linked_at=datetime(2025, 1, 8),
                    match_basis="package",
                    match_value="package-two",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/vuln/advisories/ecosystem-matrix")
    def ecosystem_matrix_endpoint(session=Depends(get_session)):
        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "ecosystems": get_ecosystem_matrix(session=session),
        }

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/vuln/advisories/ecosystem-matrix")
    assert response.status_code == 200
    npm = next(
        row
        for row in response.json()["ecosystems"]
        if row["ecosystem"] == "npm"
    )
    assert npm["advisory_count"] == 2
    assert npm["linked_server_count"] == 1
    print("PASS")


if __name__ == "__main__":
    _self_test()