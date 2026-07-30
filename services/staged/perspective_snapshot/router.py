from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Dict, Any

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot, McpServerRegistry
from .logic import create_perspective_snapshot

router = APIRouter(prefix="/api")

class SnapshotResponse(BaseModel):
    snapshot_id: int
    membership_count: int

@router.post("/perspective/{id}/snapshot", response_model=SnapshotResponse)
async def create_snapshot(
    id: int,
    facet_filters: Dict[str, List[str]],
    session: Session = Depends(get_session)
) -> SnapshotResponse:
    perspective = session.query(Perspective).filter(Perspective.id == id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    servers = session.query(McpServerRegistry).all()

    snapshot = create_perspective_snapshot(
        perspective=perspective,
        servers=servers,
        facet_filters=facet_filters,
        session=session
    )

    return {
        "snapshot_id": snapshot.id,
        "membership_count": len(snapshot.membership)
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine, get_session
    from app.models import Perspective, McpServerRegistry, PerspectiveSnapshot

    app = FastAPI()
    app.include_router(router)

    Base.metadata.create_all(bind=engine)

    test_client = TestClient(app)

    test_perspective = Perspective(name="Test Perspective")
    test_server1 = McpServerRegistry(server_name="server1", server_id="server1")
    test_server2 = McpServerRegistry(server_name="server2", server_id="server2")
    test_server3 = McpServerRegistry(server_name="server3", server_id="server3")

    with Session(engine) as session:
        session.add(test_perspective)
        session.add_all([test_server1, test_server2, test_server3])
        session.commit()

        response = test_client.post(
            f"/api/perspective/{test_perspective.id}/snapshot",
            json={"facet_filters": {"server_name": ["server1"]}}
        )

        assert response.status_code == 201
        assert response.json()["membership_count"] == 1

        snapshot = session.query(PerspectiveSnapshot).filter(
            PerspectiveSnapshot.perspective_id == test_perspective.id
        ).first()

        assert snapshot
        assert len(snapshot.membership) == 1
        assert "server1" in [m["server_id"] for m in snapshot.membership]

    print("PASS")