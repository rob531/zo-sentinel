from __future__ import annotations

from datetime import datetime
from typing import Any

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveSnapshot


class ServerInfo(BaseModel):
    server_id: str | int
    name: str | None = None
    risk_tier: str | None = None


class MembershipInfo(BaseModel):
    servers: list[ServerInfo]
    criteria_version: str | int | None = None


class SnapshotResponse(BaseModel):
    id: int
    perspective_id: str
    taken_at: datetime
    membership: MembershipInfo


Membership = MembershipInfo
PerspectiveSnapshotResponse = SnapshotResponse

router = APIRouter(prefix="/api", tags=["perspective_snapshot_fetch"])


def _membership_entries(membership: Any) -> tuple[list[dict[str, Any]], str | int | None]:
    if isinstance(membership, dict):
        criteria_version = membership.get("criteria_version")
        entries = membership.get("servers", membership.get("server_ids"))
        if entries is None:
            entries = [
                {"server_id": server_id, "risk_tier": risk_tier}
                for server_id, risk_tier in membership.items()
                if server_id != "criteria_version"
            ]
    elif isinstance(membership, list):
        criteria_version = None
        entries = membership
    else:
        return [], None

    if isinstance(entries, dict):
        entries = [
            {"server_id": server_id, "risk_tier": risk_tier}
            for server_id, risk_tier in entries.items()
        ]
    if not isinstance(entries, list):
        return [], criteria_version

    normalized = []
    for entry in entries:
        if isinstance(entry, dict):
            server_id = entry.get("server_id", entry.get("id"))
            if server_id is not None:
                normalized.append(
                    {
                        "server_id": server_id,
                        "name": entry.get("name"),
                        "risk_tier": entry.get("risk_tier"),
                    }
                )
        elif isinstance(entry, (str, int)):
            normalized.append({"server_id": entry, "name": None, "risk_tier": None})

    return normalized, criteria_version


def _build_membership(membership: Any, session: Session) -> MembershipInfo:
    entries, criteria_version = _membership_entries(membership)
    lookup_ids = {
        str(entry["server_id"])
        for entry in entries
        if entry["name"] is None or entry["risk_tier"] is None
    }
    registry = {}
    if lookup_ids:
        rows = session.execute(
            select(
                McpServerRegistry.server_id,
                McpServerRegistry.name,
                McpServerRegistry.risk_tier,
            ).where(McpServerRegistry.server_id.in_(lookup_ids))
        ).all()
        registry = {row.server_id: row for row in rows}

    servers = []
    for entry in entries:
        row = registry.get(str(entry["server_id"]))
        servers.append(
            ServerInfo(
                server_id=entry["server_id"],
                name=entry["name"] if entry["name"] is not None else (row.name if row else None),
                risk_tier=(
                    entry["risk_tier"]
                    if entry["risk_tier"] is not None
                    else (row.risk_tier if row else None)
                ),
            )
        )

    return MembershipInfo(servers=servers, criteria_version=criteria_version)


def get_snapshot(
    perspective_id: str | int,
    snapshot_id: int,
    session: Session = Depends(get_session),
) -> SnapshotResponse:
    snapshot = session.scalar(
        select(PerspectiveSnapshot).where(
            PerspectiveSnapshot.id == snapshot_id,
            PerspectiveSnapshot.perspective_id == str(perspective_id),
        )
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return SnapshotResponse(
        id=snapshot.id,
        perspective_id=snapshot.perspective_id,
        taken_at=snapshot.taken_at,
        membership=_build_membership(snapshot.membership, session),
    )


fetch_snapshot = get_snapshot
get_perspective_snapshot = get_snapshot


@router.get(
    "/perspectives/{perspective_id}/snapshots/{snapshot_id}",
    response_model=SnapshotResponse,
)
def fetch_snapshot_route(
    perspective_id: str,
    snapshot_id: int,
    session: Session = Depends(get_session),
) -> SnapshotResponse:
    return get_snapshot(perspective_id, snapshot_id, session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    perspective_id = "perspective-test"
    snapshot_id = 200
    membership = {"servers": ["srv-1", "srv-2"], "criteria_version": "v1.2"}

    with TestingSession() as db:
        db.add_all(
            [
                McpServerRegistry(server_id="srv-1", name="alpha", risk_tier="HIGH"),
                McpServerRegistry(server_id="srv-2", name="beta", risk_tier="LOW"),
                PerspectiveSnapshot(
                    id=snapshot_id,
                    perspective_id=perspective_id,
                    taken_at=datetime(2026, 9, 26, 12, 0, 0),
                    membership=membership,
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestingSession() as db:
            yield db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            f"/api/perspectives/{perspective_id}/snapshots/{snapshot_id}"
        )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["id"] == snapshot_id
    assert result["perspective_id"] == perspective_id
    assert isinstance(result["taken_at"], str)
    assert result["membership"] == {
        "servers": [
            {"server_id": "srv-1", "name": "alpha", "risk_tier": "HIGH"},
            {"server_id": "srv-2", "name": "beta", "risk_tier": "LOW"},
        ],
        "criteria_version": "v1.2",
    }
    print("PASS")
