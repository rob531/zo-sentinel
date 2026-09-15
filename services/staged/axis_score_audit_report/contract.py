"""
Axis Score Audit Report Service

Provides audit reporting for axis scoring distributions,
consistency metrics (entropy), and server axis coverage validation.
"""

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore

# =============================================================================
# PYDANTIC RESPONSE MODELS
# =============================================================================

class LabelDistribution(BaseModel):
    """Label counts for an axis."""
    label_counts: dict[str, int] = Field(default_factory=dict)


class AxisStats(BaseModel):
    """Statistics for a single axis."""
    label_distribution: LabelDistribution
    entropy: float


class VersionStats(BaseModel):
    """Statistics for a model version."""
    model_version: str
    adapter_sha256: str | None
    server_count: int
    mean_p_top: float
    mean_entropy: float


class AuditReportResponse(BaseModel):
    """Response model for axis precision audit report."""
    generated_at: str
    total_servers: int
    servers_missing_axes: int
    axis_distribution: dict[str, AxisStats]
    version_stats: list[VersionStats]


# =============================================================================
# BUSINESS LOGIC
# =============================================================================

def compute_entropy(label_indices: list[int]) -> float:
    """
    Compute Shannon entropy of label index distribution.
    Lower entropy indicates more consistent labeling across servers.
    """
    if not label_indices:
        return 0.0
    
    counts: dict[int, int] = defaultdict(int)
    total = len(label_indices)
    
    for idx in label_indices:
        counts[idx] += 1
    
    entropy = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    
    return round(entropy, 6)


def get_axis_distribution_data(session: Session) -> dict[str, dict[str, Any]]:
    """
    Query all rows from mcp_llm_axis_scores and compute per-axis label distribution.
    Returns dict mapping axis_name to {labels: {label: count}, indices: [label_indices]}
    """
    stmt = select(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
        McpLlmAxisScore.label_index
    ).order_by(McpLlmAxisScore.axis_name)
    
    results = session.execute(stmt).fetchall()
    
    axis_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"labels": defaultdict(int), "indices": []}
    )
    
    for row in results:
        axis_data[row.axis_name]["labels"][row.label] += 1
        axis_data[row.axis_name]["indices"].append(row.label_index)
    
    return dict(axis_data)


def get_version_statistics(session: Session) -> list[dict[str, Any]]:
    """
    Compute per-version statistics including:
    - server_count: unique servers using this version
    - mean_p_top: average p_top probability
    - mean_entropy: average entropy of label indices
    """
    stmt = select(
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.adapter_sha256,
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.label_index
    ).order_by(McpLlmAxisScore.model_version)
    
    results = session.execute(stmt).fetchall()
    
    # Group by (model_version, adapter_sha256)
    version_data: dict[tuple, dict[str, Any]] = defaultdict(
        lambda: {"servers": set(), "p_top_values": [], "indices": []}
    )
    
    for row in results:
        key = (row.model_version, row.adapter_sha256)
        version_data[key]["servers"].add(row.server_id)
        version_data[key]["p_top_values"].append(row.p_top)
        version_data[key]["indices"].append(row.label_index)
    
    stats: list[dict[str, Any]] = []
    for (version, adapter), data in version_data.items():
        p_top_values = data["p_top_values"]
        mean_p_top = sum(p_top_values) / len(p_top_values) if p_top_values else 0.0
        
        stats.append({
            "model_version": version,
            "adapter_sha256": adapter,
            "server_count": len(data["servers"]),
            "mean_p_top": round(mean_p_top, 6),
            "mean_entropy": compute_entropy(data["indices"])
        })
    
    return stats


def count_servers_missing_axes(session: Session, min_axes: int = 7) -> int:
    """
    Count servers with fewer than 7 distinct axis_name values.
    These servers are flagged as having missing/incomplete axis coverage.
    """
    stmt = text("""
        SELECT server_id
        FROM mcp_llm_axis_scores
        GROUP BY server_id
        HAVING COUNT(DISTINCT axis_name) < :min_axes
    """)
    
    result = session.execute(stmt, {"min_axes": min_axes})
    return len(result.fetchall())


def get_total_unique_servers(session: Session) -> int:
    """Get count of unique server_id values."""
    stmt = select(func.count(func.distinct(McpLlmAxisScore.server_id)))
    return session.execute(stmt).scalar() or 0


def build_audit_report(session: Session) -> dict[str, Any]:
    """
    Build complete audit report for axis scoring precision.
    
    Computes:
    - Per-axis label distribution and entropy
    - Per-version consistency metrics
    - Servers with incomplete axis coverage
    """
    # Get all axis distribution data
    axis_data = get_axis_distribution_data(session)
    
    # Build axis_distribution response
    axis_distribution: dict[str, dict[str, Any]] = {}
    for axis_name, data in axis_data.items():
        entropy = compute_entropy(data["indices"])
        axis_distribution[axis_name] = {
            "label_distribution": {"label_counts": dict(data["labels"])},
            "entropy": entropy
        }
    
    # Get version statistics
    version_stats = get_version_statistics(session)
    
    # Count servers missing axes
    servers_missing = count_servers_missing_axes(session)
    
    # Get total server count
    total_servers = get_total_unique_servers(session)
    
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": total_servers,
        "servers_missing_axes": servers_missing,
        "axis_distribution": axis_distribution,
        "version_stats": version_stats
    }


# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

app = FastAPI(
    title="Axis Score Audit Report",
    description="Audit reporting for axis scoring distributions and consistency",
    version="1.0.0"
)


@app.get("/api/scoring/audit/axis-precision", response_model=AuditReportResponse)
async def get_axis_precision_audit(
    session: Session = Depends(get_session)
) -> AuditReportResponse:
    """
    Generate audit report for axis precision scoring.
    
    Reads all rows from mcp_llm_axis_scores and computes:
    - Per-axis label distribution (counts per label)
    - Per-axis entropy (consistency of label_index values)
    - Per-version statistics (server count, mean p_top, mean entropy)
    - Servers with missing axes (fewer than 7 axis_name values)
    
    Returns structured audit report with all computed metrics.
    """
    report_data = build_audit_report(session)
    return AuditReportResponse(**report_data)


# =============================================================================
# SELF-TEST INFRASTRUCTURE
# =============================================================================

def create_in_memory_engine():
    """Create SQLite in-memory engine for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return engine


def create_test_tables(engine):
    """Create test tables mirroring production schema."""
    from sqlalchemy import Table, Column, Integer, String, Float, DateTime, MetaData
    
    metadata = MetaData()
    
    Table(
        "mcp_llm_axis_scores",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("server_id", String(255), nullable=False, index=True),
        Column("axis_name", String(100), nullable=False, index=True),
        Column("label", String(100), nullable=False),
        Column("label_index", Integer, nullable=False),
        Column("p_top", Float, nullable=False),
        Column("p_critical", Float, nullable=False),
        Column("p_danger", Float, nullable=False),
        Column("model_version", String(100), nullable=False),
        Column("adapter_sha256", String(64), nullable=True),
        Column("scored_at", DateTime, nullable=False),
    )
    
    metadata.create_all(engine)


def seed_test_data(session_factory):
    """
    Seed test database with 3 servers:
    - server-001: Complete 7-axis coverage, consistent labels
    - server-002: Complete 7-axis coverage, mixed labels
    - server-003: INCOMPLETE - only 5 axes (missing throughput, reliability)
    """
    Session = session_factory()
    session = Session()
    
    try:
        now = datetime.now(timezone.utc)
        
        # Server 1: Complete coverage, mostly "high" labels (label_index=0)
        server1_data = [
            ("accuracy", "high", 0, 0.95, 0.03, 0.02),
            ("precision", "high", 0, 0.90, 0.07, 0.03),
            ("recall", "high", 0, 0.88, 0.08, 0.04),
            ("f1_score", "high", 0, 0.92, 0.05, 0.03),
            ("latency", "low", 2, 0.15, 0.25, 0.60),
            ("throughput", "medium", 1, 0.55, 0.30, 0.15),
            ("reliability", "high", 0, 0.91, 0.06, 0.03),
        ]
        
        for axis_name, label, label_index, p_top, p_critical, p_danger in server1_data:
            session.execute(
                text("""
                    INSERT INTO mcp_llm_axis_scores 
                    (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, model_version, adapter_sha256, scored_at)
                    VALUES (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical, :p_danger, :model_version, :adapter_sha256, :scored_at)
                """),
                {
                    "server_id": "server-001",
                    "axis_name": axis_name,
                    "label": label,
                    "label_index": label_index,
                    "p_top": p_top,
                    "p_critical": p_critical,
                    "p_danger": p_danger,
                    "model_version": "v1.0.0",
                    "adapter_sha256": "sha256abc123",
                    "scored_at": now
                }
            )
        
        # Server 2: Complete coverage, mixed labels
        server2_data = [
            ("accuracy", "medium", 1, 0.70, 0.20, 0.10),
            ("precision", "low", 2, 0.30, 0.40, 0.30),
            ("recall", "medium", 1, 0.65, 0.25, 0.10),
            ("f1_score", "medium", 1, 0.60, 0.30, 0.10),
            ("latency", "high", 0, 0.80, 0.15, 0.05),
            ("throughput", "low", 2, 0.25, 0.35, 0.40),
            ("reliability", "medium", 1, 0.50, 0.35, 0.15),
        ]
        
        for axis_name, label, label_index, p_top, p_critical, p_danger in server2_data:
            session.execute(
                text("""
                    INSERT INTO mcp_llm_axis_scores 
                    (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, model_version, adapter_sha256, scored_at)
                    VALUES (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical, :p_danger, :model_version, :adapter_sha256, :scored_at)
                """),
                {
                    "server_id": "server-002",
                    "axis_name": axis_name,
                    "label": label,
                    "label_index": label_index,
                    "p_top": p_top,
                    "p_critical": p_critical,
                    "p_danger": p_danger,
                    "model_version": "v1.0.0",
                    "adapter_sha256": "sha256abc123",
                    "scored_at": now
                }
            )
        
        # Server 3: INCOMPLETE - only 5 axes (missing throughput, reliability)
        server3_data = [
            ("accuracy", "high", 0, 0.93, 0.05, 0.02),
            ("precision", "medium", 1, 0.65, 0.25, 0.10),
            ("recall", "high", 0, 0.88, 0.08, 0.04),
            ("f1_score", "medium", 1, 0.70, 0.20, 0.10),
            ("latency", "medium", 1, 0.50, 0.35, 0.15),
        ]
        
        for axis_name, label, label_index, p_top, p_critical, p_danger in server3_data:
            session.execute(
                text("""
                    INSERT INTO mcp_llm_axis_scores 
                    (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, model_version, adapter_sha256, scored_at)
                    VALUES (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical, :p_danger, :model_version, :adapter_sha256, :scored_at)
                """),
                {
                    "server_id": "server-003",
                    "axis_name": axis_name,
                    "label": label,
                    "label_index": label_index,
                    "p_top": p_top,
                    "p_critical": p_critical,
                    "p_danger": p_danger,
                    "model_version": "v2.0.0",
                    "adapter_sha256": "sha256xyz789",
                    "scored_at": now
                }
            )
        
        session.commit()
        
    finally:
        session.close()


# =============================================================================
# SELF-TEST RUNNER
# =============================================================================

def run_self_test() -> bool:
    """
    Run self-test with in-memory SQLite database.
    
    Seeds 3 servers with known axis distributions:
    - 2 servers with complete 7-axis coverage
    - 1 server with only 5 axes (missing 2)
    
    Asserts:
    - HTTP 200 response
    - total_servers == 3
    - servers_missing_axes == 1
    - axis_distribution contains all 7 expected axes
    """
    # Create in-memory test database
    engine = create_in_memory_engine()
    create_test_tables(engine)
    
    # Create session factory
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    
    # Seed test data
    seed_test_data(session_factory)
    
    # Override the real get_session dependency
    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    # Create test client and make request
    client = TestClient(app)
    response = client.get("/api/scoring/audit/axis-precision")
    
    # Check status code
    if response.status_code != 200:
        print(f"FAIL: Status code {response.status_code}, expected 200")
        print(response.text)
        app.dependency_overrides.clear()
        return False
    
    data = response.json()
    
    # Assert total_servers
    if data["total_servers"] != 3:
        print(f"FAIL: total_servers={data['total_servers']}, expected 3")
        app.dependency_overrides.clear()
        return False
    
    # Assert servers_missing_axes (server-003 has only 5 axes)
    if data["servers_missing_axes"] != 1:
        print(f"FAIL: servers_missing_axes={data['servers_missing_axes']}, expected 1")
        app.dependency_overrides.clear()
        return False
    
    # Assert axis_distribution keys
    expected_axes = {
        "accuracy", "precision", "recall", "f1_score",
        "latency", "throughput", "reliability"
    }
    actual_axes = set(data["axis_distribution"].keys())
    
    if expected_axes != actual_axes:
        print(f"FAIL: axis_distribution keys mismatch")
        print(f"Expected: {expected_axes}")
        print(f"Got: {actual_axes}")
        app.dependency_overrides.clear()
        return False
    
    # Validate structure of each axis
    for axis_name, axis_stats in data["axis_distribution"].items():
        if "label_distribution" not in axis_stats:
            print(f"FAIL: Missing label_distribution for axis '{axis_name}'")
            app.dependency_overrides.clear()
            return False
        if "entropy" not in axis_stats:
            print(f"FAIL: Missing entropy for axis '{axis_name}'")
            app.dependency_overrides.clear()
            return False
        if "label_counts" not in axis_stats["label_distribution"]:
            print(f"FAIL: Missing label_counts for axis '{axis_name}'")
            app.dependency_overrides.clear()
            return False
    
    # Validate version_stats structure
    if "version_stats" not in data:
        print("FAIL: Missing version_stats in response")
        app.dependency_overrides.clear()
        return False
    
    if len(data["version_stats"]) == 0:
        print("FAIL: version_stats is empty")
        app.dependency_overrides.clear()
        return False
    
    for version_stat in data["version_stats"]:
        required_fields = ["model_version", "adapter_sha256", "server_count", "mean_p_top", "mean_entropy"]
        for field in required_fields:
            if field not in version_stat:
                print(f"FAIL: Missing field '{field}' in version_stats")
                app.dependency_overrides.clear()
                return False
    
    # Validate generated_at format
    if "generated_at" not in data:
        print("FAIL: Missing generated_at in response")
        app.dependency_overrides.clear()
        return False
    
    app.dependency_overrides.clear()
    print("PASS")
    return True


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        success = run_self_test()
        sys.exit(0 if success else 1)
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)