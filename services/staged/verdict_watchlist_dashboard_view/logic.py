from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

def get_servers_by_verdict(session: Session = Depends(get_session)):
    servers = session.query(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.verdict).all()
    return {"servers": [{"server_id": server.server_id, "name": server.name, "verdict": server.verdict} for server in servers]}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    test_servers = [
        McpServerRegistry(server_id="1", name="Server1", verdict="safe"),
        McpServerRegistry(server_id="2", name="Server2", verdict="risky"),
        McpServerRegistry(server_id="3", name="Server3", verdict="safe"),
    ]
    session.add_all(test_servers)
    session.commit()

    @app.get("/api/verdict/watchlist")
    def test_get_servers_by_verdict():
        return get_servers_by_verdict(session)

    client = TestClient(app)
    response = client.get("/api/verdict/watchlist")
    assert response.status_code == 200
    data = response.json()
    assert len(data["servers"]) == 3
    assert data["servers"][0]["verdict"] == "safe"
    print("PASS")