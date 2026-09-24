from typing import Optional

from app.db import get_session
from app.models import Base
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from unittest.mock import patch

try:
    from trust_gating_override import trust_gate
    HAS_TRUST_GATING = True
except ImportError:
    HAS_TRUST_GATING = False
    def trust_gate(url: str, name: str, params: dict) -> dict:
        return {}


class OverrideResponse(BaseModel):
    server_id: str
    name: str
    url: str
    has_override: bool
    override_tier: Optional[str]
    override_reason: Optional[str]


def get_override_info(server_id: str, db: Session = Depends(get_session)) -> OverrideResponse:
    query = """
        SELECT server_id, name, url
        FROM mcp_server_registry
        WHERE server_id = :server_id
    """
    result = db.execute(text(query), {"server_id": server_id}).fetchone()
    if not result:
        name = ""
        url = ""
    else:
        name = result.name
        url = result.url

    override_resp = trust_gate(url, name, {}) if HAS_TRUST_GATING else {}
    
    if override_resp and override_resp.get("published_overall_risk"):
        has_override = True
        override_tier = override_resp.get("published_overall_risk")
        override_reason = override_resp.get("reason") or override_resp.get("published_overall_risk")
    elif override_resp and override_resp.get("trusted"):
        has_override = True
        override_tier = "trusted"
        override_reason = override_resp.get("reason") or "trusted"
    else:
        has_override = False
        override_tier = None
        override_reason = None

    return OverrideResponse(
        server_id=server_id,
        name=name,
        url=url,
        has_override=has_override,
        override_tier=override_tier,
        override_reason=override_reason
    )


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    with TestingSessionLocal() as db:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                url VARCHAR,
                registry_source VARCHAR,
                risk_tier VARCHAR,
                trust_score FLOAT,
                verdict VARCHAR,
                verdict_reasoning TEXT,
                confidence FLOAT,
                description TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        db.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, url, registry_source, risk_tier)
            VALUES ('srv1', 'Trusted Server', 'https://github.com/trusted-org/repo', 'github', 'high')
        """))
        db.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, url, registry_source, risk_tier)
            VALUES ('srv2', 'Regular Server', 'https://github.com/other-org/repo', 'github', 'medium')
        """))
        db.commit()

    from fastapi import FastAPI
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    def mock_trust_gate(url, name, params):
        if "trusted-org" in url:
            return {"published_overall_risk": "low", "reason": "Known trusted organization"}
        return {}

    with patch("trust_score_override.logic.trust_gate", mock_trust_gate):
        result1 = get_override_info("srv1")
        result2 = get_override_info("srv2")

        assert result1.has_override is True
        assert result1.override_tier == "low"
        assert result2.has_override is False
        assert result2.override_tier is None

    print("PASS")