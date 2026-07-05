import logging
from datetime import datetime
from typing import Dict, List

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, Server, PerspectiveSnapshot

logger = logging.getLogger(__name__)

def snapshot_perspective(perspective_id: str, db: Session = Depends(get_session)) -> Dict:
    """Capture the current membership of a single perspective."""
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        logger.error(f"Perspective {perspective_id} not found")
        return {}

    servers = db.query(Server).filter(Server.perspective_id == perspective_id).all()
    if not servers:
        logger.warning(f"No servers found for perspective {perspective_id}")
        return {}

    membership = {
        "perspective_id": perspective_id,
        "perspective_name": perspective.name,
        "servers": [
            {
                "server_id": server.id,
                "risk_tier": server.risk_tier,
            }
            for server in servers
        ],
        "snapshot_time": datetime.utcnow(),
    }

    snapshot = PerspectiveSnapshot(**membership)
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    return membership

def run(db: Session = Depends(get_session)):
    """Run the snapshot daemon to capture all perspectives."""
    perspectives = db.query(Perspective).all()
    for perspective in perspectives:
        try:
            snapshot_perspective(perspective.id, db)
            logger.info(f"Snapshot captured for perspective {perspective.id}")
        except Exception as e:
            logger.error(f"Failed to capture snapshot for perspective {perspective.id}: {e}")

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Mock data
    from app.models import Perspective, Server
    db = SessionLocal()
    perspective = Perspective(id="test_perspective", name="Test Perspective")
    db.add(perspective)
    db.commit()

    server1 = Server(id="server1", perspective_id="test_perspective", risk_tier=1)
    server2 = Server(id="server2", perspective_id="test_perspective", risk_tier=2)
    db.add_all([server1, server2])
    db.commit()

    # Test snapshot_perspective
    result = snapshot_perspective("test_perspective", db)
    assert result["perspective_id"] == "test_perspective"
    assert len(result["servers"]) == 2
    assert result["servers"][0]["server_id"] == "server1"
    assert result["servers"][1]["server_id"] == "server2"

    # Check if snapshot was saved
    snapshot = db.query(PerspectiveSnapshot).first()
    assert snapshot.perspective_id == "test_perspective"
    assert len(snapshot.servers) == 2

    print("PASS")