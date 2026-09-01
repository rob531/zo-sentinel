from typing import List
from pydantic import BaseModel
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

class Server(BaseModel):
    server_id: int
    name: str
    url: str
    registry_source: str
    first_seen: str
    scan_count: int

class BacklogResponse(BaseModel):
    total: int
    servers: List[Server]

def get_scoring_backlog(db: Session = Depends(get_session)) -> BacklogResponse:
    query = """
    SELECT
        r.server_id,
        r.name,
        r.url,
        r.registry_source,
        r.first_seen,
        r.scan_count
    FROM
        McpServerRegistry r
    LEFT JOIN
        McpLlmAxisScore s ON r.server_id = s.server_id
    WHERE
        s.scored_at IS NULL
    """
    result = db.execute(query)
    servers = [Server(**row._asdict()) for row in result]
    return BacklogResponse(total=len(servers), servers=servers)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    db = SessionLocal()
    db.execute("""
    INSERT INTO McpServerRegistry (server_id, name, url, registry_source, first_seen, scan_count)
    VALUES
        (1, 'Server 1', 'http://server1', 'source1', '2023-01-01', 10),
        (2, 'Server 2', 'http://server2', 'source2', '2023-01-02', 20),
        (3, 'Server 3', 'http://server3', 'source3', '2023-01-03', 30)
    """)
    db.execute("""
    INSERT INTO McpLlmAxisScore (server_id, scored_at)
    VALUES (1, '2023-01-01')
    """)
    db.commit()

    # Test the function
    response = get_scoring_backlog()
    assert response.total == 2
    assert any(server.server_id == 2 for server in response.servers)
    assert any(server.server_id == 3 for server in response.servers)
    print("PASS")