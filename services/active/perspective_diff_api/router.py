# deps: requests
"""perspective_diff_api - Compare perspective snapshots and return diff."""
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspective_diff_api"])


class SnapshotInfo(BaseModel):
    id: str
    taken_at: Any


class ServerDiffEntry(BaseModel):
    server_id: str
    old_tier: str | None = None
    new_tier: str | None = None
    name: str | None = None


class PerspectiveDiffResponse(BaseModel):
    perspective_id: str
    snapshot_a: SnapshotInfo
    snapshot_b: SnapshotInfo
    added: list[ServerDiffEntry] = Field(default_factory=list)
    removed: list[ServerDiffEntry] = Field(default_factory=list)
    changed: list[ServerDiffEntry] = Field(default_factory=list)


def parse_membership(membership_json: Any) -> list[dict]:
    """Parse membership JSON column to list of dicts."""
    if not membership_json:
        return []
    if isinstance(membership_json, str):
        return json.loads(membership_json)
    return membership_json if isinstance(membership_json, list) else []


def get_server_names(session: Session, server_ids: list[str]) -> dict[str, str]:
    """Fetch server names from registry for the given server IDs."""
    if not server_ids:
        return {}
    result = session.query(McpServerRegistry.server_id, McpServerRegistry.name).filter(
        McpServerRegistry.server_id.in_(server_ids)
    ).all()
    return {row[0]: row[1] for row in result}


@router.get("/diff", response_model=PerspectiveDiffResponse)
def get_perspective_diff(
    snapshot_a: str,
    snapshot_b: str,
    session: Session = Depends(get_session),
) -> PerspectiveDiffResponse:
    """Compare two perspective snapshots and return the diff."""
    snap_a = session.query(PerspectiveSnapshot).filter(PerspectiveSnapshot.id == snapshot_a).first()
    if not snap_a:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_a} not found")

    snap_b = session.query(PerspectiveSnapshot).filter(PerspectiveSnapshot.id == snapshot_b).first()
    if not snap_b:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_b} not found")

    membership_a = parse_membership(snap_a.membership)
    membership_b = parse_membership(snap_b.membership)

    servers_a = {item["server_id"]: item for item in membership_a}
    servers_b = {item["server_id"]: item for item in membership_b}

    ids_a = set(servers_a.keys())
    ids_b = set(servers_b.keys())

    added_ids = ids_b - ids_a
    removed_ids = ids_a - ids_b
    common_ids = ids_a & ids_b

    changed_ids = [
        sid for sid in common_ids
        if servers_a[sid].get("risk_tier") != servers_b[sid].get("risk_tier")
    ]

    all_ids = list(added_ids | removed_ids | set(changed_ids))
    names_map = get_server_names(session, all_ids)

    added = [
        ServerDiffEntry(
            server_id=sid,
            old_tier=None,
            new_tier=servers_b[sid].get("risk_tier"),
            name=names_map.get(sid),
        )
        for sid in added_ids
    ]

    removed = [
        ServerDiffEntry(
            server_id=sid,
            old_tier=servers_a[sid].get("risk_tier"),
            new_tier=None,
            name=names_map.get(sid),
        )
        for sid in removed_ids
    ]

    changed = [
        ServerDiffEntry(
            server_id=sid,
            old_tier=servers_a[sid].get("risk_tier"),
            new_tier=servers_b[sid].get("risk_tier"),
            name=names_map.get(sid),
        )
        for sid in changed_ids
    ]

    return PerspectiveDiffResponse(
        perspective_id=snap_a.perspective_id,
        snapshot_a=SnapshotInfo(id=snap_a.id, taken_at=snap_a.taken_at),
        snapshot_b=SnapshotInfo(id=snap_b.id, taken_at=snap_b.taken_at),
        added=added,
        removed=removed,
        changed=changed,
    )


if __name__ == "__main__":
    import os
    import sys

    # repo root: services/active/perspective_diff_api/router.py → repo root
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo not in sys.path:
        sys.path.insert(0, repo)

    # ── minimal in-memory test DB (local classes only; app imports stay clean) ──
    from fastapi import FastAPI
    from sqlalchemy import Column, String, create_engine, text
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.pool import StaticPool

    Base = declarative_base()

    class LocalSnapshot(Base):
        __tablename__ = "perspective_snapshots"
        id = Column(String, primary_key=True)
        perspective_id = Column(String)
        taken_at = Column(String)
        membership = Column(String)

    class LocalRegistry(Base):
        __tablename__ = "mcp_server_registry"
        server_id = Column(String, primary_key=True)
        name = Column(String)
        risk_tier = Column(String)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    session.execute(text("DELETE FROM perspective_snapshots"))
    session.execute(text("DELETE FROM mcp_server_registry"))
    session.commit()

    registry_data = [
        {"server_id": "srv-001", "name": "Alpha Service", "risk_tier": "high"},
        {"server_id": "srv-002", "name": "Beta Service", "risk_tier": "medium"},
        {"server_id": "srv-003", "name": "Gamma Service", "risk_tier": "low"},
        {"server_id": "srv-004", "name": "Delta Service", "risk_tier": "high"},
        {"server_id": "srv-005", "name": "Epsilon Service", "risk_tier": "low"},
        {"server_id": "srv-006", "name": "Zeta Service", "risk_tier": "medium"},
    ]
    for r in registry_data:
        session.add(LocalRegistry(**r))
    session.commit()

    membership_a = [
        {"server_id": "srv-001", "risk_tier": "high"},
        {"server_id": "srv-002", "risk_tier": "medium"},
        {"server_id": "srv-003", "risk_tier": "low"},
        {"server_id": "srv-004", "risk_tier": "high"},
    ]
    membership_b = [
        {"server_id": "srv-002", "risk_tier": "high"},
        {"server_id": "srv-003", "risk_tier": "low"},
        {"server_id": "srv-004", "risk_tier": "low"},
        {"server_id": "srv-005", "risk_tier": "low"},
        {"server_id": "srv-006", "risk_tier": "medium"},
    ]

    session.add(LocalSnapshot(id="snap-a", perspective_id="persp-1", taken_at="2024-01-01T00:00:00", membership=json.dumps(membership_a)))
    session.add(LocalSnapshot(id="snap-b", perspective_id="persp-1", taken_at="2024-01-02T00:00:00", membership=json.dumps(membership_b)))
    session.commit()

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        try:
            yield session
        finally:
            pass

    # ── AFTER repo root is in sys.path, safe to import app internals ──
    from app.db import get_session
    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(app)

    response = client.get("/api/diff", params={"snapshot_a": "snap-a", "snapshot_b": "snap-b"})
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert len(data["added"]) > 0, "Expected non-empty added list"
    assert len(data["changed"]) > 0, "Expected non-empty changed list"

    all_entries = data["added"] + data["removed"] + data["changed"]
    assert any(entry.get("name") for entry in all_entries), "Expected server names in response"

    response_not_found = client.get("/api/diff", params={"snapshot_a": "nonexistent", "snapshot_b": "snap-b"})
    assert response_not_found.status_code == 404, f"Expected 404 for nonexistent snapshot, got {response_not_found.status_code}"

    print("PASS")

    from fastapi import FastAPI
    from sqlalchemy import Column, String, create_engine
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.pool import StaticPool

    Base = declarative_base()

    class LocalSnapshot(Base):
        __tablename__ = "perspective_snapshots"
        id = Column(String, primary_key=True)
        perspective_id = Column(String)
        taken_at = Column(String)
        membership = Column(String)

    class LocalRegistry(Base):
        __tablename__ = "mcp_server_registry"
        server_id = Column(String, primary_key=True)
        name = Column(String)
        risk_tier = Column(String)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    session.execute(text("DELETE FROM perspective_snapshots"))
    session.execute(text("DELETE FROM mcp_server_registry"))
    session.commit()

    registry_data = [
        {"server_id": "srv-001", "name": "Alpha Service", "risk_tier": "high"},
        {"server_id": "srv-002", "name": "Beta Service", "risk_tier": "medium"},
        {"server_id": "srv-003", "name": "Gamma Service", "risk_tier": "low"},
        {"server_id": "srv-004", "name": "Delta Service", "risk_tier": "high"},
        {"server_id": "srv-005", "name": "Epsilon Service", "risk_tier": "low"},
        {"server_id": "srv-006", "name": "Zeta Service", "risk_tier": "medium"},
    ]
    for r in registry_data:
        session.add(LocalRegistry(**r))
    session.commit()

    membership_a = [
        {"server_id": "srv-001", "risk_tier": "high"},
        {"server_id": "srv-002", "risk_tier": "medium"},
        {"server_id": "srv-003", "risk_tier": "low"},
        {"server_id": "srv-004", "risk_tier": "high"},
    ]
    membership_b = [
        {"server_id": "srv-002", "risk_tier": "high"},
        {"server_id": "srv-003", "risk_tier": "low"},
        {"server_id": "srv-004", "risk_tier": "low"},
        {"server_id": "srv-005", "risk_tier": "low"},
        {"server_id": "srv-006", "risk_tier": "medium"},
    ]

    session.add(LocalSnapshot(id="snap-a", perspective_id="persp-1", taken_at="2024-01-01T00:00:00", membership=json.dumps(membership_a)))
    session.add(LocalSnapshot(id="snap-b", perspective_id="persp-1", taken_at="2024-01-02T00:00:00", membership=json.dumps(membership_b)))
    session.commit()

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(app)

    response = client.get("/api/diff", params={"snapshot_a": "snap-a", "snapshot_b": "snap-b"})
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert len(data["added"]) > 0, "Expected non-empty added list"
    assert len(data["changed"]) > 0, "Expected non-empty changed list"

    all_entries = data["added"] + data["removed"] + data["changed"]
    assert any(entry.get("name") for entry in all_entries), "Expected server names in response"

    response_not_found = client.get("/api/diff", params={"snapshot_a": "nonexistent", "snapshot_b": "snap-b"})
    assert response_not_found.status_code == 404, f"Expected 404 for nonexistent snapshot, got {response_not_found.status_code}"

    print("PASS")
