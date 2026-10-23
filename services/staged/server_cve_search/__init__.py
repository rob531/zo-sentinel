from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "healthy"}

def self_test():
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for self-test
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables for self-test
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Test data access
    session = SessionLocal()
    try:
        # Test McpServerRegistry
        server = McpServerRegistry(name="test_server", url="http://test.com")
        session.add(server)
        session.commit()

        # Test McpLlmAxisScore
        score = McpLlmAxisScore(server_id=server.id, axis="test_axis", score=0.5)
        session.add(score)
        session.commit()

        # Test McpScoreDispute
        dispute = McpScoreDispute(server_id=server.id, axis="test_axis", dispute_reason="test")
        session.add(dispute)
        session.commit()

        # Test Org
        org = Org(name="test_org")
        session.add(org)
        session.commit()

        # Test User
        user = User(name="test_user", org_id=org.id)
        session.add(user)
        session.commit()

        print("PASS")
    finally:
        session.close()
        app.dependency_overrides.clear()

if __name__ == "__main__":
    self_test()