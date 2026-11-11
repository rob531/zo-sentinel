from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink


def get_match_basis_breakdown(db: Session = Depends(get_session)) -> Dict[str, List[Dict[str, Any]]]:
    rows = (
        db.query(
            VulnLink.match_basis.label("match_basis"),
            func.count(VulnLink.id).label("rows"),
            func.count(distinct(VulnLink.advisory_id)).label("advisories"),
            func.count(distinct(VulnLink.server_id)).label("servers"),
            func.avg(VulnLink.match_confidence).label("avg_confidence"),
        )
        .group_by(VulnLink.match_basis)
        .all()
    )

    bases = []
    for row in rows:
        average = row.avg_confidence
        bases.append(
            {
                "match_basis": "" if row.match_basis is None else str(row.match_basis),
                "rows": int(row.rows or 0),
                "advisories": int(row.advisories or 0),
                "servers": int(row.servers or 0),
                "avg_confidence": round(float(average), 4) if average is not None else 0.0,
            }
        )

    return {"bases": bases}


get_vuln_link_match_basis_breakdown = get_match_basis_breakdown
get_breakdown = get_match_basis_breakdown
get_data = get_match_basis_breakdown


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnLink.__table__.create(bind=engine)
    testing_session = sessionmaker(bind=engine)()

    testing_session.add_all(
        [
            VulnLink(
                id=1,
                advisory_id=10,
                server_id=20,
                match_basis="cve",
                match_value="CVE-2024-0001",
                match_confidence=0.9,
                linked_at=datetime.now(timezone.utc),
            ),
            VulnLink(
                id=2,
                advisory_id=10,
                server_id=21,
                match_basis="cve",
                match_value="CVE-2024-0001",
                match_confidence=0.8,
                linked_at=datetime.now(timezone.utc),
            ),
            VulnLink(
                id=3,
                advisory_id=11,
                server_id=20,
                match_basis="package",
                match_value="example-package",
                match_confidence=0.5,
                linked_at=datetime.now(timezone.utc),
            ),
        ]
    )
    testing_session.commit()

    that_app = FastAPI()

    def override_get_session():
        yield testing_session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/vuln-links/match-basis-breakdown")
    def match_basis_breakdown_endpoint(
        result: Dict[str, List[Dict[str, Any]]] = Depends(get_match_basis_breakdown),
    ):
        return result

    with TestClient(that_app) as client:
        response = client.get("/api/vuln-links/match-basis-breakdown")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["bases"]) == 2
        cve = next(item for item in payload["bases"] if item["match_basis"] == "cve")
        assert cve["rows"] == 2
        assert cve["advisories"] == 1
        assert cve["servers"] == 2
        assert cve["avg_confidence"] == 0.85

    testing_session.close()
    engine.dispose()
    print("PASS")