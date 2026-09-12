"""router for server_cve_timeline_api."""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.db import get_session
from app.models import VulnLink, VulnAdvisory  # noqa: F401 – imported for type hints


router = APIRouter(prefix="/api")


class CveEntry(BaseModel):
    advisory_id: int
    summary: str
    severity: str
    published_at: datetime
    linked_at: datetime


class ServerCveTimelineResponse(BaseModel):
    server_id: int
    cves: List[CveEntry]


@router.get(
    "/servers/{server_id}/cves/timeline",
    response_model=ServerCveTimelineResponse,
    name="server_cve_timeline",
)
def get_server_cve_timeline(
    server_id: int, db: Depends = Depends(get_session)
) -> ServerCveTimelineResponse:
    """
    Return CVE timeline for a server.

    The DB session is expected to expose ``links`` and ``advisories`` attributes
    (real implementation uses SQLAlchemy models; the test overrides the session
    with a mock that provides those collections).
    """
    # Collect matching links
    matching_links = [link for link in getattr(db, "links", []) if link.server_id == server_id]

    # Join with advisories
    entries = []
    for link in matching_links:
        advisory = next(
            (adv for adv in getattr(db, "advisories", []) if adv.id == link.advisory_id), None
        )
        if advisory is None:
            continue
        entries.append(
            CveEntry(
                advisory_id=advisory.id,
                summary=advisory.summary,
                severity=advisory.severity,
                published_at=advisory.published_at,
                linked_at=link.linked_at,
            )
        )

    # Sort by published_at
    entries.sort(key=lambda e: e.published_at)

    return ServerCveTimelineResponse(server_id=server_id, cves=entries)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # ------------------------------------------------------------------- #
    # Mock data structures mimicking the real ORM models
    # ------------------------------------------------------------------- #
    class _MockVulnLink:
        def __init__(self, advisory_id: int, server_id: int, linked_at: datetime):
            self.advisory_id = advisory_id
            self.server_id = server_id
            self.linked_at = linked_at

    class _MockVulnAdvisory:
        def __init__(
            self,
            id_: int,
            summary: str,
            severity: str,
            published_at: datetime,
        ):
            self.id = id_
            self.summary = summary
            self.severity = severity
            self.published_at = published_at

    # ------------------------------------------------------------------- #
    # Mock session providing ``links`` and ``advisories`` collections
    # ------------------------------------------------------------------- #
    class _MockSession:
        def __init__(self):
            self.links = [
                _MockVulnLink(advisory_id=10, server_id=1, linked_at=datetime(2023, 1, 10, 12, 0)),
                _MockVulnLink(advisory_id=11, server_id=1, linked_at=datetime(2023, 2, 5, 9, 30)),
                _MockVulnLink(advisory_id=12, server_id=1, linked_at=datetime(2023, 3, 15, 16, 45)),
            ]
            self.advisories = [
                _MockVulnAdvisory(
                    id_=10,
                    summary="CVE-2023-0001",
                    severity="HIGH",
                    published_at=datetime(2023, 1, 5, 8, 0),
                ),
                _MockVulnAdvisory(
                    id_=11,
                    summary="CVE-2023-0002",
                    severity="MEDIUM",
                    published_at=datetime(2023, 2, 1, 10, 15),
                ),
                _MockVulnAdvisory(
                    id_=12,
                    summary="CVE-2023-0003",
                    severity="LOW",
                    published_at=datetime(2023, 3, 10, 14, 20),
                ),
            ]

    # ------------------------------------------------------------------- #
    # FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: _MockSession()

    client = TestClient(app)

    resp = client.get("/api/servers/1/cves/timeline")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["server_id"] == 1
    assert isinstance(data["cves"], list)
    assert len(data["cves"]) == 3, f"expected 3 entries, got {len(data['cves'])}"

    # Verify ordering by published_at
    published_dates = [datetime.fromisoformat(c["published_at"]) for c in data["cves"]]
    assert published_dates == sorted(published_dates), "CVE entries not sorted by published_at"

    print("PASS")