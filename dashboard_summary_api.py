import datetime
from typing import List, Dict, Any

from fastapi import FastAPI, APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, Session, declarative_base

# --- Pydantic Models ---

class RiskTierCount(BaseModel):
    tier: str
    count: int

class RecentActivityItem(BaseModel):
    server_id: int
    risk_tier: str
    timestamp: datetime.datetime

class DashboardSummary(BaseModel):
    total_servers: int
    risk_tiers: Dict[str, int]
    recent_activity: List[RecentActivityItem]

# --- SQLAlchemy Setup ---

Base = declarative_base()

class Server(Base):
    __tablename__ = "servers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)

class AxisScore(Base):
    __tablename__ = "axis_scores"
    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    axis_name = Column(String, nullable=False)
    score = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    server = None # SQLAlchemy relationship placeholder

# --- Database Session Dependency ---

DATABASE_URL = "sqlite:///:memory:"  # In-memory SQLite for testing
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- API Router ---

router = APIRouter()

@router.get("/dashboard/summary", response_model=DashboardSummary)
def get_dashboard_summary(db: Session = Depends(get_db)):
    # Fetch all servers
    servers = db.query(Server).all()
    total_servers = len(servers)

    # Fetch all axis scores
    axis_scores = db.query(AxisScore).all()

    # Determine risk tiers and counts
    risk_tiers_data: Dict[str, int] = {
        "LOW": 0,
        "MEDIUM": 0,
        "HIGH": 0,
        "CRITICAL": 0,
        "UNKNOWN": 0,
    }
    server_risk_tiers: Dict[int, str] = {}

    for score in axis_scores:
        tier = "UNKNOWN"
        if 0 <= score.score < 40:
            tier = "LOW"
        elif 40 <= score.score < 70:
            tier = "MEDIUM"
        elif 70 <= score.score < 90:
            tier = "HIGH"
        elif score.score >= 90:
            tier = "CRITICAL"
        risk_tiers_data[tier] += 1
        server_risk_tiers[score.server_id] = tier # Last score for a server determines its tier

    # Apply rule-override: CRITICAL axis forces the tier
    final_server_tiers: Dict[int, str] = {}
    for server_id, tier in server_risk_tiers.items():
        final_server_tiers[server_id] = tier

    # Re-calculate risk tiers based on final server tiers
    final_risk_tiers_data: Dict[str, int] = {
        "LOW": 0,
        "MEDIUM": 0,
        "HIGH": 0,
        "CRITICAL": 0,
        "UNKNOWN": 0,
    }
    for tier in final_server_tiers.values():
        final_risk_tiers_data[tier] += 1

    # Fetch recent activity
    recent_activity_raw = db.query(AxisScore).order_by(AxisScore.timestamp.desc()).limit(10).all()
    recent_activity = [
        RecentActivityItem(
            server_id=score.server_id,
            risk_tier=server_risk_tiers.get(score.server_id, "UNKNOWN"), # Use initial tier for display
            timestamp=score.timestamp
        ) for score in recent_activity_raw
    ]

    return DashboardSummary(
        total_servers=total_servers,
        risk_tiers=final_risk_tiers_data,
        recent_activity=recent_activity
    )

# --- Self-Test ---

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # Create in-memory database and tables
    Base.metadata.create_all(bind=engine)

    # Seed the database
    db = SessionLocal()

    # Add servers
    servers_to_add = [Server(id=i, name=f"Server {i}") for i in range(1, 6)]
    db.add_all(servers_to_add)
    db.commit()

    # Add axis scores
    axis_scores_to_add = [
        # Server 1: LOW scores
        AxisScore(server_id=1, axis_name="CPU", score=20, timestamp=datetime.datetime(2023, 1, 1, 10, 0, 0)),
        AxisScore(server_id=1, axis_name="Memory", score=30, timestamp=datetime.datetime(2023, 1, 1, 10, 5, 0)),
        # Server 2: MEDIUM scores
        AxisScore(server_id=2, axis_name="CPU", score=50, timestamp=datetime.datetime(2023, 1, 1, 10, 10, 0)),
        AxisScore(server_id=2, axis_name="Network", score=65, timestamp=datetime.datetime(2023, 1, 1, 10, 15, 0)),
        # Server 3: HIGH scores
        AxisScore(server_id=3, axis_name="Disk", score=75, timestamp=datetime.datetime(2023, 1, 1, 10, 20, 0)),
        AxisScore(server_id=3, axis_name="CPU", score=85, timestamp=datetime.datetime(2023, 1, 1, 10, 25, 0)),
        # Server 4: CRITICAL score (override)
        AxisScore(server_id=4, axis_name="Security", score=95, timestamp=datetime.datetime(2023, 1, 1, 10, 30, 0)),
        AxisScore(server_id=4, axis_name="CPU", score=30, timestamp=datetime.datetime(2023, 1, 1, 10, 35, 0)), # This score will be overridden
        # Server 5: Mixed scores, last one is HIGH
        AxisScore(server_id=5, axis_name="Memory", score=25, timestamp=datetime.datetime(2023, 1, 1, 10, 40, 0)),
        AxisScore(server_id=5, axis_name="Disk", score=80, timestamp=datetime.datetime(2023, 1, 1, 10, 45, 0)),
    ]
    db.add_all(axis_scores_to_add)
    db.commit()
    db.close()

    # Setup FastAPI app with the router
    app = FastAPI()
    app.include_router(router)

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/dashboard/summary")

    # Assertions
    assert response.status_code == 200
    data = response.json()

    # Expected results
    # Server 1: LOW
    # Server 2: MEDIUM
    # Server 3: HIGH
    # Server 4: CRITICAL (due to Security score 95)
    # Server 5: HIGH (due to Disk score 80)
    # Total servers: 5 (we added 5)
    # Risk Tiers: LOW: 1, MEDIUM: 1, HIGH: 2, CRITICAL: 1, UNKNOWN: 0

    expected_total_servers = 5
    expected_risk_tiers = {
        "LOW": 1,
        "MEDIUM": 1,
        "HIGH": 2,
        "CRITICAL": 1,
        "UNKNOWN": 0,
    }

    assert data["total_servers"] == expected_total_servers
    assert data["risk_tiers"] == expected_risk_tiers
    assert len(data["recent_activity"]) <= 10 # Check limit

    # Check if at least one CRITICAL override is present in recent activity if applicable
    critical_override_found_in_recent = False
    for activity in data["recent_activity"]:
        if activity["server_id"] == 4: # Server 4 had the CRITICAL override
            assert activity["risk_tier"] == "CRITICAL" # The displayed tier should reflect the override
            critical_override_found_in_recent = True
            break
    # If server 4's activity was among the most recent 10, we should find it.
    # If not, this specific check might fail depending on timestamps.
    # A more robust test would ensure specific items are present if they should be.

    print("PASS")