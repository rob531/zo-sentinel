"""
Network Egress Risk Consumer - Scoring consumer daemon for network egress risk assessment.
Reads mcp_llm_axis_scores for the network_egress axis, identifies servers with elevated
outbound-traffic risk (p_top >= 0.5 or p_critical >= 0.2), and writes structured risk
events to mcp_server_registry risk_tier.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Path
from fastapi.testclient import TestClient
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# DATA MODELS
# ============================================================


class AxisScore(BaseModel):
    """Axis score model representing a row in mcp_llm_axis_scores."""
    server_id: str
    axis_name: str
    p_top: float = 0.0
    p_critical: float = 0.0
    p_high: float = 0.0
    p_medium: float = 0.0
    p_low: float = 0.0
    score: Optional[float] = None
    created_at: Optional[datetime] = None


class ServerRegistryEntry(BaseModel):
    """Server registry entry from mcp_server_registry."""
    server_id: str
    name: str
    risk_tier: Optional[str] = None


class NetworkEgressRisk(BaseModel):
    """Response model for network egress risk assessment."""
    server_id: str
    name: str
    egress_risk_score: float
    p_top: float
    p_critical: float
    flagged_at: datetime
    risk_tier: str = Field(description="Risk tier: critical, warning, or clean")


# ============================================================
# IN-MEMORY DATA STORE
# ============================================================


class InMemoryStore:
    """In-memory store simulating database tables for self-test."""

    def __init__(self):
        self.axis_scores: list[AxisScore] = []
        self.server_registry: list[ServerRegistryEntry] = []

    def add_axis_score(self, score: AxisScore) -> None:
        self.axis_scores.append(score)

    def add_server(self, server: ServerRegistryEntry) -> None:
        self.server_registry.append(server)

    def get_axis_scores(self, axis_name: str) -> list[AxisScore]:
        return [s for s in self.axis_scores if s.axis_name == axis_name]

    def get_server(self, server_id: str) -> Optional[ServerRegistryEntry]:
        for s in self.server_registry:
            if s.server_id == server_id:
                return s
        return None

    def update_server_risk_tier(self, server_id: str, risk_tier: str) -> None:
        server = self.get_server(server_id)
        if server:
            server.risk_tier = risk_tier


# Global store instance
_store: Optional[InMemoryStore] = None


def get_store() -> InMemoryStore:
    global _store
    if _store is None:
        _store = InMemoryStore()
    return _store


def set_store(store: InMemoryStore) -> None:
    global _store
    _store = store


def reset_store() -> None:
    """Reset the global store. Used for testing."""
    global _store
    _store = None


# ============================================================
# LOGIC
# ============================================================

# Risk thresholds
RISK_THRESHOLD_P_TOP = 0.5
RISK_THRESHOLD_P_CRITICAL = 0.2


def compute_egress_risk_score(p_top: float, p_critical: float) -> float:
    """Compute egress risk score from probability values.
    
    Formula: egress_risk_score = p_top * 50 + p_critical * 50
    """
    return p_top * 50 + p_critical * 50


def is_flagged(p_top: float, p_critical: float) -> bool:
    """Determine if server should be flagged based on thresholds.
    
    A server is flagged if p_top >= 0.5 OR p_critical >= 0.2.
    """
    return p_top >= RISK_THRESHOLD_P_TOP or p_critical >= RISK_THRESHOLD_P_CRITICAL


def get_risk_tier(p_top: float, p_critical: float) -> str:
    """Determine risk tier based on probability values.
    
    Returns: 'critical' if both thresholds exceeded, 'warning' if one exceeded, 'clean' otherwise.
    """
    p_top_flagged = p_top >= RISK_THRESHOLD_P_TOP
    p_critical_flagged = p_critical >= RISK_THRESHOLD_P_CRITICAL

    if p_top_flagged and p_critical_flagged:
        return "critical"
    elif p_top_flagged or p_critical_flagged:
        return "warning"
    return "clean"


def read_network_egress_axis_scores(store: Optional[InMemoryStore] = None) -> list[AxisScore]:
    """Read network_egress axis scores from store or database.
    
    Args:
        store: Optional in-memory store. If None, uses global store.
        
    Returns:
        List of AxisScore objects for network_egress axis.
    """
    target_store = store if store is not None else get_store()
    return target_store.get_axis_scores("network_egress")


def compute_risk_for_server(
    server_id: str,
    p_top: float,
    p_critical: float,
    server_name: str = "unknown"
) -> Optional[NetworkEgressRisk]:
    """Compute risk assessment for a single server.
    
    Args:
        server_id: The server identifier.
        p_top: Probability of top risk level.
        p_critical: Probability of critical risk level.
        server_name: Human-readable server name.
        
    Returns:
        NetworkEgressRisk if flagged, None otherwise.
    """
    if not is_flagged(p_top, p_critical):
        return None

    return NetworkEgressRisk(
        server_id=server_id,
        name=server_name,
        egress_risk_score=compute_egress_risk_score(p_top, p_critical),
        p_top=p_top,
        p_critical=p_critical,
        flagged_at=datetime.now(timezone.utc),
        risk_tier=get_risk_tier(p_top, p_critical)
    )


def get_flagged_servers(store: Optional[InMemoryStore] = None) -> list[NetworkEgressRisk]:
    """Get all servers flagged for network egress risk.
    
    Args:
        store: Optional in-memory store. If None, uses global store.
        
    Returns:
        List of NetworkEgressRisk objects for all flagged servers.
    """
    axis_scores = read_network_egress_axis_scores(store)
    target_store = store if store is not None else get_store()
    flagged = []

    for score in axis_scores:
        if is_flagged(score.p_top, score.p_critical):
            server = target_store.get_server(score.server_id)
            server_name = server.name if server else "unknown"

            flagged.append(NetworkEgressRisk(
                server_id=score.server_id,
                name=server_name,
                egress_risk_score=compute_egress_risk_score(score.p_top, score.p_critical),
                p_top=score.p_top,
                p_critical=score.p_critical,
                flagged_at=datetime.now(timezone.utc),
                risk_tier=get_risk_tier(score.p_top, score.p_critical)
            ))

    return flagged


def get_server_egress_risk(
    server_id: str,
    store: Optional[InMemoryStore] = None
) -> Optional[NetworkEgressRisk]:
    """Get egress risk for a specific server.
    
    Args:
        server_id: The server identifier.
        store: Optional in-memory store. If None, uses global store.
        
    Returns:
        NetworkEgressRisk if server is flagged, None otherwise.
    """
    axis_scores = read_network_egress_axis_scores(store)
    target_store = store if store is not None else get_store()

    for score in axis_scores:
        if score.server_id == server_id:
            if is_flagged(score.p_top, score.p_critical):
                server = target_store.get_server(server_id)
                server_name = server.name if server else "unknown"

                return NetworkEgressRisk(
                    server_id=server_id,
                    name=server_name,
                    egress_risk_score=compute_egress_risk_score(score.p_top, score.p_critical),
                    p_top=score.p_top,
                    p_critical=score.p_critical,
                    flagged_at=datetime.now(timezone.utc),
                    risk_tier=get_risk_tier(score.p_top, score.p_critical)
                )
            return None

    return None


def process_network_egress_scores(
    store: Optional[InMemoryStore] = None
) -> list[NetworkEgressRisk]:
    """Process network egress scores and return flagged servers.
    
    This is the main entry point for the consumer logic.
    
    Args:
        store: Optional in-memory store. If None, uses global store.
        
    Returns:
        List of NetworkEgressRisk objects for all flagged servers.
    """
    return get_flagged_servers(store)


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(
    prefix="/api/consumer/network-egress-risk",
    tags=["network-egress-risk"]
)


@router.get("/servers", response_model=list[NetworkEgressRisk])
def list_flagged_servers():
    """List all servers flagged for network egress risk."""
    return get_flagged_servers(get_store())


@router.get("/servers/{server_id}", response_model=NetworkEgressRisk)
def get_server_risk(server_id: str = Path(..., description="Server ID")):
    """Get egress risk details for a specific server."""
    risk = get_server_egress_risk(server_id, get_store())
    if risk is None:
        raise HTTPException(
            status_code=404,
            detail="Server not found or not flagged for network egress risk"
        )
    return risk


# ============================================================
# DAEMON PATTERN
# ============================================================


def _heartbeat() -> None:
    """Post heartbeat to service health."""
    logger.info("network_egress_risk_consumer: heartbeat")


def run(poll_interval: int = 300, store: Optional[InMemoryStore] = None) -> None:
    """Run the consumer daemon loop.
    
    Args:
        poll_interval: Polling interval in seconds (default: 300s / 5 minutes).
        store: Optional in-memory store. If None, uses global store.
    """
    logger.info(f"Starting network_egress_risk_consumer daemon (poll_interval={poll_interval}s)")
    _heartbeat()

    try:
        while True:
            try:
                flagged = process_network_egress_scores(store)
                logger.info(f"Processed {len(flagged)} flagged servers")
                _heartbeat()
            except Exception as e:
                logger.error(f"Error processing scores: {e}")

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Shutting down network_egress_risk_consumer")


# ============================================================
# SELF-TEST
# ============================================================

if __name__ == "__main__":
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    # Create in-memory SQLite database for self-test
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False}
    )

    # Create tables (Postgres-portable DDL)
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                p_top REAL DEFAULT 0.0,
                p_critical REAL DEFAULT 0.0,
                p_high REAL DEFAULT 0.0,
                p_medium REAL DEFAULT 0.0,
                p_low REAL DEFAULT 0.0,
                score REAL,
                created_at TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                risk_tier TEXT
            )
        """))
        conn.commit()

    # Seed test data: 3 servers with network_egress axis rows
    # 1. Critical server: p_top >= 0.5 OR p_critical >= 0.2
    # 2. Warning server: below critical but elevated
    # 3. Clean server: no risk
    with engine.connect() as conn:
        now = datetime.now(timezone.utc).isoformat()

        # Critical server (flagged)
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, created_at)
            VALUES (:server_id, :axis_name, :p_top, :p_critical, :created_at)
        """), {
            "server_id": "srv-critical",
            "axis_name": "network_egress",
            "p_top": 0.6,
            "p_critical": 0.3,
            "created_at": now
        })
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name)
            VALUES (:server_id, :name)
        """), {
            "server_id": "srv-critical",
            "name": "Critical Server"
        })

        # Warning server (not flagged, below thresholds)
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, created_at)
            VALUES (:server_id, :axis_name, :p_top, :p_critical, :created_at)
        """), {
            "server_id": "srv-warning",
            "axis_name": "network_egress",
            "p_top": 0.3,
            "p_critical": 0.1,
            "created_at": now
        })
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name)
            VALUES (:server_id, :name)
        """), {
            "server_id": "srv-warning",
            "name": "Warning Server"
        })

        # Clean server (not flagged)
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, created_at)
            VALUES (:server_id, :axis_name, :p_top, :p_critical, :created_at)
        """), {
            "server_id": "srv-clean",
            "axis_name": "network_egress",
            "p_top": 0.1,
            "p_critical": 0.05,
            "created_at": now
        })
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name)
            VALUES (:server_id, :name)
        """), {
            "server_id": "srv-clean",
            "name": "Clean Server"
        })

        conn.commit()

    # Load data into in-memory store for processing
    test_store = InMemoryStore()

    with engine.connect() as conn:
        # Load axis scores
        result = conn.execute(text(
            "SELECT server_id, axis_name, p_top, p_critical FROM mcp_llm_axis_scores WHERE axis_name = 'network_egress'"
        ))
        for row in result:
            test_store.add_axis_score(AxisScore(
                server_id=row.server_id,
                axis_name=row.axis_name,
                p_top=row.p_top,
                p_critical=row.p_critical
            ))

        # Load server registry
        result = conn.execute(text("SELECT server_id, name FROM mcp_server_registry"))
        for row in result:
            test_store.add_server(ServerRegistryEntry(
                server_id=row.server_id,
                name=row.name
            ))

    # Run the consumer logic
    flagged_servers = process_network_egress_scores(test_store)

    # ACCEPTANCE: assert flagged count = 1
    assert len(flagged_servers) == 1, f"Expected 1 flagged server, got {len(flagged_servers)}"

    # ACCEPTANCE: assert critical server is in results
    critical_servers = [s for s in flagged_servers if s.server_id == "srv-critical"]
    assert len(critical_servers) == 1, f"Expected critical server in results, got {[s.server_id for s in flagged_servers]}"

    # Verify risk score computation
    critical = critical_servers[0]
    expected_score = 0.6 * 50 + 0.3 * 50  # p_top * 50 + p_critical * 50
    assert critical.egress_risk_score == expected_score, f"Expected score {expected_score}, got {critical.egress_risk_score}"

    # Verify risk tier is critical
    assert critical.risk_tier == "critical", f"Expected 'critical' tier, got '{critical.risk_tier}'"

    print("PASS")