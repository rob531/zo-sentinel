# deps: fastapi, sqlalchemy
"""Perspective Analysis Service Router.

Analyzes perspective membership changes over time and returns current roster.
Uses app Postgres for Perspective/PerspectiveSnapshot tables.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspective_analysis"])


class Change(BaseModel):
    date: str
    added: List[str]
    removed: List[str]


class CurrentMember(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    last_scanned: Optional[str] = None


class AnalysisResponse(BaseModel):
    perspective_id: str
    perspective_name: str
    changes: List[Change]
    current_members: List[CurrentMember]


def _compute_membership_change(
    prev: Dict[str, str], curr: Dict[str, str]
) -> tuple[List[str], List[str]]:
    """Compute added and removed server_ids between two membership dicts."""
    prev_ids = set(prev.keys())
    curr_ids = set(curr.keys())
    added = sorted(curr_ids - prev_ids)
    removed = sorted(prev_ids - curr_ids)
    return added, removed


def get_perspective_analysis(
    perspective_id: str, db: Session
) -> AnalysisResponse:
    """Compute membership changes and current roster for a perspective.

    Args:
        perspective_id: The perspective to analyze.
        db: SQLAlchemy session.

    Returns:
        AnalysisResponse with changes timeline and current members.

    Raises:
        HTTPException: If perspective not found.
    """
    perspective = db.query(Perspective).filter(
        Perspective.id == perspective_id
    ).first()

    if not perspective:
        raise HTTPException(
            status_code=404,
            detail=f"Perspective '{perspective_id}' not found"
        )

    snapshots: List[PerspectiveSnapshot] = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at)
        .all()
    )

    if not snapshots:
        return AnalysisResponse(
            perspective_id=perspective_id,
            perspective_name=perspective.name,
            changes=[],
            current_members=[]
        )

    changes: List[Change] = []
    prev_membership: Dict[str, str] = {}

    for snap in snapshots:
        curr_membership = snap.membership or {}
        added, removed = _compute_membership_change(prev_membership, curr_membership)

        if added or removed:
            changes.append(Change(
                date=snap.taken_at.isoformat(),
                added=added,
                removed=removed
            ))
        prev_membership = curr_membership

    latest_membership = snapshots[-1].membership or {}
    current_server_ids = list(latest_membership.keys())

    current_members: List[CurrentMember] = []
    if current_server_ids:
        servers = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id.in_(current_server_ids))
            .all()
        )
        server_map = {s.server_id: s for s in servers}

        for server_id in current_server_ids:
            srv = server_map.get(server_id)
            if srv:
                current_members.append(CurrentMember(
                    server_id=server_id,
                    name=srv.name,
                    risk_tier=srv.risk_tier,
                    last_scanned=(
                        srv.last_scanned.isoformat()
                        if srv.last_scanned else None
                    )
                ))
            else:
                current_members.append(CurrentMember(
                    server_id=server_id,
                    name=None,
                    risk_tier=latest_membership.get(server_id),
                    last_scanned=None
                ))

    return AnalysisResponse(
        perspective_id=perspective_id,
        perspective_name=perspective.name,
        changes=changes,
        current_members=current_members
    )


@router.get(
    "/perspective/{perspective_id}/analysis",
    response_model=AnalysisResponse
)
def perspective_analysis(
    perspective_id: str,
    db: Session = Depends(get_session)
) -> AnalysisResponse:
    """Return membership changes and current roster for a perspective."""
    return get_perspective_analysis(perspective_id, db)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(bind=engine)

    from app.db import Base
    Base.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as db:
        db.add(Perspective(
            id="persp-1",
            name="High Risk View",
            description="All high-risk servers",
            facet_filters={"risk_tier": ["HIGH"]},
            created_by="system"
        ))
        db.add(PerspectiveSnapshot(
            perspective_id="persp-1",
            taken_at=datetime(2024, 1, 1, 0, 0, 0),
            membership={"srv-a": "HIGH", "srv-b": "HIGH"}
        ))
        db.add(PerspectiveSnapshot(
            perspective_id="persp-1",
            taken_at=datetime(2024, 2, 1, 0, 0, 0),
            membership={"srv-b": "HIGH", "srv-c": "HIGH"}
        ))
        db.add(McpServerRegistry(
            server_id="srv-a",
            name="Server A",
            risk_tier="HIGH",
            last_scanned=datetime(2024, 1, 15, 12, 0, 0)
        ))
        db.add(McpServerRegistry(
            server_id="srv-b",
            name="Server B",
            risk_tier="HIGH",
            last_scanned=datetime(2024, 2, 10, 12, 0, 0)
        ))
        db.add(McpServerRegistry(
            server_id="srv-c",
            name="Server C",
            risk_tier="HIGH",
            last_scanned=datetime(2024, 2, 5, 12, 0, 0)
        ))
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/perspective/persp-1/analysis")

    if resp.status_code != 200:
        print(f"FAIL – status {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()

    if data["perspective_id"] != "persp-1":
        print(f"FAIL – wrong perspective_id: {data['perspective_id']}", file=sys.stderr)
        sys.exit(1)

    if len(data["changes"]) != 1:
        print(f"FAIL – expected 1 change entry, got {len(data['changes'])}", file=sys.stderr)
        sys.exit(1)

    change = data["changes"][0]
    if change["removed"] != ["srv-a"] or change["added"] != ["srv-c"]:
        print(f"FAIL – wrong change: {change}", file=sys.stderr)
        sys.exit(1)

    if len(data["current_members"]) != 2:
        print(f"FAIL – expected 2 current members, got {len(data['current_members'])}", file=sys.stderr)
        sys.exit(1)

    srv_ids = {m["server_id"] for m in data["current_members"]}
    if srv_ids != {"srv-b", "srv-c"}:
        print(f"FAIL – wrong current members: {srv_ids}", file=sys.stderr)
        sys.exit(1)

    resp_not_found = client.get("/api/perspective/nonexistent/analysis")
    if resp_not_found.status_code != 404:
        print(f"FAIL – expected 404 for nonexistent perspective", file=sys.stderr)
        sys.exit(1)

    print("PASS")
