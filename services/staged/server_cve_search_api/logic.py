# services/staged/server_cve_search_api/logic.py
from datetime import datetime
from typing import List

from fastapi import Depends
from sqlalchemy.orm import Session, joinedload

from app.db import get_session, Base
from app.models import VulnLink, VulnAdvisory

from .contract import (
    CveInfo,
    ServerCveResponse,
)

def get_server_cves(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerCveResponse:
    """
    Retrieve CVE information for a given server.

    The query joins `vulnerability_links` with `vulnerability_advisories`
    filtered by the supplied `server_id`.  The result is returned as a
    `ServerCveResponse` model.
    """
    links = (
        db.query(VulnLink)
        .options(joinedload(VulnLink.advisory))
        .filter(VulnLink.server_id == server_id)
        .all()
    )

    cve_list: List[CveInfo] = []
    for link in links:
        adv: VulnAdvisory = link.advisory
        cve_list.append(
            CveInfo(
                id=adv.cve_id,
                feed=adv.feed,
                summary=adv.summary,
                severity=adv.severity,
                ecosystem=adv.ecosystem,
                package=adv.package,
                source_url=adv.source_url,
                published_at=adv.published_at,
            )
        )

    return ServerCveResponse(server_id=server_id, cves=cve_list)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB and bind the real models to it
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.create_all(engine)

    # Seed data
    with SessionLocal() as db:
        adv1 = VulnAdvisory(
            id=1,
            cve_id="CVE-2021-1234",
            feed="NVD",
            summary="Test summary 1",
            severity="High",
            ecosystem="python",
            package="package1",
            source_url="http://example.com/1",
            published_at=datetime(2021, 1, 1, 0, 0, 0),
        )
        adv2 = VulnAdvisory(
            id=2,
            cve_id="CVE-2022-5678",
            feed="NVD",
            summary="Test summary 2",
            severity="Medium",
            ecosystem="go",
            package="package2",
            source_url="http://example.com/2",
            published_at=datetime(2022, 2, 2, 0, 0, 0),
        )
        db.add_all([adv1, adv2])
        db.flush()  # ensure IDs are available

        link1 = VulnLink(
            id=1,
            server_id="srv-001",
            advisory_id=adv1.id,
        )
        link2 = VulnLink(
            id=2,
            server_id="srv-001",
            advisory_id=adv2.id,
        )
        db.add_all([link1, link2])
        db.commit()

        # Invoke the logic
        resp = get_server_cves("srv-001", db=db)

        # Assertions
        assert isinstance(resp, ServerCveResponse)
        assert resp.server_id == "srv-001"
        assert len(resp.cves) >= 1
        assert any(cve.id == "CVE-2021-1234" for cve in resp.cves)

        print("PASS")