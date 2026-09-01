from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

def get_risk_tier_distribution(session: Session = Depends(get_session)):
    result = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id)
    ).group_by(McpServerRegistry.risk_tier).all()

    return {tier: count for tier, count in result}

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()

    DATABASE_URL = "sqlite:///:memory:"

    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = SessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    db.add_all([
        McpServerRegistry(server_id="1", risk_tier="low"),
        McpServerRegistry(server_id="2", risk_tier="medium"),
        McpServerRegistry(server_id="3", risk_tier="high"),
    ])
    db.commit()

    distribution = get_risk_tier_distribution(db)
    assert len(distribution) == 3
    assert distribution["low"] == 1
    print("PASS")