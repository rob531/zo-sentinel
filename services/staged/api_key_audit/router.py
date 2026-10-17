"""API Key Audit Router - mirrors _exemplar pattern."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["api-key-audit"])


class ApiKeyAuditEntry(BaseModel):
    id: int
    org_id: int
    label: str
    created_at: str
    user_email: str


class ApiKeyAuditResponse(BaseModel):
    keys: list[ApiKeyAuditEntry]


@router.get("/admin/api-keys/audit", response_model=ApiKeyAuditResponse)
def get_api_key_audit(session: Session = Depends(get_session)) -> ApiKeyAuditResponse:
    sql = text("""
        SELECT
            ak.id,
            ak.org_id,
            ak.label,
            ak.created_at,
            u.email as user_email
        FROM api_keys ak
        JOIN users u ON ak.org_id = u.org_id
        ORDER BY ak.created_at DESC
    """)
    result = session.execute(sql)
    rows = result.fetchall()
    return ApiKeyAuditResponse(
        keys=[
            ApiKeyAuditEntry(
                id=row.id,
                org_id=row.org_id,
                label=row.label,
                created_at=str(row.created_at),
                user_email=row.user_email,
            )
            for row in rows
        ]
    )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session as SASession
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT, created_at TEXT)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, org_id INTEGER, email TEXT, created_at TEXT, password_hash TEXT, role TEXT, clerk_id TEXT, clerk_created_at TEXT, clerk_synced_via TEXT)"))
        conn.execute(text("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, org_id INTEGER, key_hash TEXT, label TEXT, created_at TEXT)"))

        conn.execute(text("INSERT INTO orgs (id, name, created_at) VALUES (1, 'Org A', '2024-01-01')"))
        conn.execute(text("INSERT INTO orgs (id, name, created_at) VALUES (2, 'Org B', '2024-01-02')"))
        conn.execute(text("INSERT INTO users (id, org_id, email, created_at) VALUES (1, 1, 'alice@org-a.com', '2024-01-01')"))
        conn.execute(text("INSERT INTO users (id, org_id, email, created_at) VALUES (2, 2, 'bob@org-b.com', '2024-01-02')"))
        conn.execute(text("INSERT INTO api_keys (id, org_id, key_hash, label, created_at) VALUES (1, 1, 'hash1', 'Admin Key', '2024-01-15')"))
        conn.execute(text("INSERT INTO api_keys (id, org_id, key_hash, label, created_at) VALUES (2, 2, 'hash2', 'Service Key', '2024-01-20')"))

    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session() -> SASession:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)

    from fastapi.testclient import TestClient

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/admin/api-keys/audit")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "keys" in data, "Missing 'keys' in response"
    assert len(data["keys"]) == 2, f"Expected 2 keys, got {len(data['keys'])}"
    assert all("label" in k for k in data["keys"]), "Missing 'label' field in keys"
    print("PASS")