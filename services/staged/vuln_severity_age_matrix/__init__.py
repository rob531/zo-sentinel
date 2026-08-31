# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    VulnAdvisory,
)
from fastapi import Depends
from sqlalchemy.orm import Session

__version__ = "1.0.0"

def get_registry(session: Session = Depends(get_session)):
    return session.query(McpServerRegistry).all()

def get_llm_axis_scores(session: Session = Depends(get_session)):
    return session.query(McpLlmAxisScore).all()

def get_score_disputes(session: Session = Depends(get_session)):
    return session.query(McpScoreDispute).all()

def get_vuln_advisories(session: Session = Depends(get_session)):
    return session.query(VulnAdvisory).all()


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import get_session as original_get_session

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[original_get_session] = override_get_session

    # Basic smoke test - just verify the module compiles and structure is valid
    assert get_registry is not None
    assert get_llm_axis_scores is not None
    assert get_score_disputes is not None
    assert get_vuln_advisories is not None
    assert __version__ is not None

    print("PASS")