from typing import Any

from fastapi import Depends
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from app.db import get_session
from app.models import ApiKey, User


def get_org_api_keys(org_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    stmt = select(ApiKey.id, ApiKey.label, ApiKey.created_at).where(ApiKey.org_id == org_id)
    results = session.execute(stmt).fetchall()
    return [{"id": r.id, "label": r.label, "created_at": r.created_at} for r in results]


def list_api_keys(org_id: int, session: Session = Depends(get_session)) -> dict[str, list[dict[str, Any]]]:
    keys = get_org_api_keys(org_id, session)
    return {"keys": keys}


if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)

    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE orgs (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL,
                org_id INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (org_id) REFERENCES orgs(id)
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE api_keys (
                id INTEGER PRIMARY KEY,
                org_id INTEGER NOT NULL,
                key_hash TEXT NOT NULL,
                label TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (org_id) REFERENCES orgs(id)
            )
        """)

        conn.exec_driver_sql("INSERT INTO orgs (id, name) VALUES (1, 'Org One')")
        conn.exec_driver_sql("INSERT INTO orgs (id, name) VALUES (2, 'Org Two')")
        conn.exec_driver_sql("INSERT INTO users (id, email, org_id) VALUES (1, 'user1@org1.com', 1)")
        conn.exec_driver_sql("INSERT INTO users (id, email, org_id) VALUES (2, 'user2@org2.com', 2)")
        conn.exec_driver_sql("INSERT INTO api_keys (id, org_id, key_hash, label) VALUES (1, 1, 'hash1', 'Key One')")
        conn.exec_driver_sql("INSERT INTO api_keys (id, org_id, key_hash, label) VALUES (2, 2, 'hash2', 'Key Two')")

    SessionLocal = sessionmaker(bind=engine)
    test_session = SessionLocal()

    result = list_api_keys(org_id=1, session=test_session)

    assert len(result["keys"]) == 1, f"Expected 1 key, got {len(result['keys'])}"
    assert result["keys"][0]["id"] == 1
    assert result["keys"][0]["label"] == "Key One"
    assert "key_hash" not in result["keys"][0]

    result_org2 = list_api_keys(org_id=2, session=test_session)
    assert len(result_org2["keys"]) == 1
    assert result_org2["keys"][0]["id"] == 2
    assert result_org2["keys"][0]["label"] == "Key Two"

    print("PASS")