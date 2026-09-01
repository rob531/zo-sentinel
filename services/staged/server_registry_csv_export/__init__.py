from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.db import get_session
from app.models import McpServerRegistry
import csv
import io

router = APIRouter()


@router.get("/csv")
def export_servers_csv(session: Session = Depends(get_session)):
    """Export MCP server registry as CSV."""
    servers = session.query(McpServerRegistry).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "server_name", "server_type", "status", 
        "created_at", "updated_at"
    ])
    
    for server in servers:
        writer.writerow([
            server.id,
            server.server_name,
            getattr(server, 'server_type', ''),
            getattr(server, 'status', ''),
            getattr(server, 'created_at', ''),
            getattr(server, 'updated_at', '')
        ])
    
    return {"csv_data": output.getvalue()}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.main import app as main_app
    
    test_app = FastAPI()
    test_app.include_router(router)
    
    with open("/tmp/test_servers.csv", "w") as f:
        f.write("")
    
    def override_get_session():
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        
        from app.models import Base
        Base.metadata.create_all(bind=engine)
        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        session = TestingSessionLocal()
        return session
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    response = client.get("/csv")
    
    if response.status_code == 200:
        data = response.json()
        if "csv_data" in data and "id" in data["csv_data"]:
            print("PASS")
        else:
            print(f"FAIL: unexpected response format")
    else:
        print(f"FAIL: status {response.status_code}")