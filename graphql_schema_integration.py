import os
import sys
import time
import signal
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from enum import Enum

SERVICE_NAME = "graphql_schema_integration"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
LOG_FILE = "/home/workspace/logs/graphql_schema_integration.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)


class VerdictTier(str, Enum):
    """All 6 verdict tiers plus INSUFFICIENT for un-scored MCPs."""
    TRUSTED_RESEARCH = "TRUSTED_RESEARCH"
    ENTERPRISE_CONTROLLED = "ENTERPRISE_CONTROLLED"
    AMBER_UNVERIFIED = "AMBER_UNVERIFIED"
    CAUTION_LIMITED = "CAUTION_LIMITED"
    HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
    KNOWN_THREAT = "KNOWN_THREAT"
    INSUFFICIENT = "INSUFFICIENT"


VERDICT_THRESHOLDS: Dict[str, float] = {
    "TRUSTED_RESEARCH": 0.85,
    "ENTERPRISE_CONTROLLED": 0.75,
    "AMBER_UNVERIFIED": 0.50,
    "CAUTION_LIMITED": 0.30,
    "HIGH_RISK_ISOLATED": 0.15,
    "KNOWN_THREAT": 0.0,
    "INSUFFICIENT": -1.0,
}

VERDICT_EXPIRY_DAYS: Dict[str, int] = {
    "TRUSTED_RESEARCH": 90,
    "ENTERPRISE_CONTROLLED": 60,
    "AMBER_UNVERIFIED": 30,
    "CAUTION_LIMITED": 14,
    "HIGH_RISK_ISOLATED": 7,
    "KNOWN_THREAT": 3,
    "INSUFFICIENT": 0,
}

VERDICT_COLORS: Dict[str, str] = {
    "TRUSTED_RESEARCH": "#22c55e",
    "ENTERPRISE_CONTROLLED": "#16a34a",
    "AMBER_UNVERIFIED": "#eab308",
    "CAUTION_LIMITED": "#f97316",
    "HIGH_RISK_ISOLATED": "#ef4444",
    "KNOWN_THREAT": "#991b1b",
    "INSUFFICIENT": "#6b7280",
}


@dataclass
class SignalSchema:
    """Schema for a single trust signal."""
    signal_name: str
    signal_type: str
    min_score: float
    max_score: float
    weight: float
    description: str


@dataclass
class VerdictSchema:
    """Schema for a verdict tier definition."""
    tier: str
    threshold: float
    expiry_days: int
    color: str
    description: str


@dataclass
class TableSchema:
    """Schema for a database table."""
    table_name: str
    columns: List[Dict[str, Any]]
    primary_key: Optional[str] = None
    indexes: List[str] = field(default_factory=list)


@dataclass
class MCPServerSchema:
    """Schema definition for MCP server registry entry."""
    server_id: str
    name: str
    url: str
    description: Optional[str]
    trust_score: Optional[float]
    verdict: str
    registry_source: str
    scan_count: int
    first_seen: str
    last_seen: str
    last_assessed: Optional[str]


@dataclass
class SignalScoreSchema:
    """Schema for signal score record."""
    server_id: str
    signal_name: str
    score: float
    evidence: Optional[str]
    scored_at: str


@dataclass
class AttestationSchema:
    """Schema for attestation record."""
    server_id: str
    attestation_id: str
    attested_by: str
    attested_at: str
    evidence_url: Optional[str]
    scope: str
    expires_at: Optional[str]


def ws_query(sql: str) -> Dict[str, Any]:
    """Query write_service for read operations."""
    import requests
    try:
        r = requests.post(
            QUERY_SERVICE_URL + "/query",
            json={"sql": sql},
            timeout=30
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return {"rows": [], "count": 0}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write to write_service."""
    import requests
    try:
        r = requests.post(
            WRITE_SERVICE_URL + "/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Write failed for {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    import requests
    try:
        r = requests.post(
            EXECUTE_SERVICE_URL + "/execute",
            json={"sql": sql},
            timeout=30
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Execute failed: {e}")
        return False


def get_verdict_tiers() -> List[str]:
    """Return list of all valid verdict tier names."""
    return [t.value for t in VerdictTier]


def get_verdict_schema() -> List[VerdictSchema]:
    """Return full schema definitions for all verdict tiers."""
    return [
        VerdictSchema(
            tier=tier.value,
            threshold=VERDICT_THRESHOLDS.get(tier.value, -1.0),
            expiry_days=VERDICT_EXPIRY_DAYS.get(tier.value, 0),
            color=VERDICT_COLORS.get(tier.value, "#6b7280"),
            description=_get_verdict_description(tier.value)
        )
        for tier in VerdictTier
    ]


def _get_verdict_description(tier: str) -> str:
    """Return human-readable description for verdict tier."""
    descriptions = {
        "TRUSTED_RESEARCH": "Extensively reviewed, community-tested, high trust score",
        "ENTERPRISE_CONTROLLED": "Organization-managed, vetted, within enterprise control",
        "AMBER_UNVERIFIED": "Moderate risk, limited review, needs verification",
        "CAUTION_LIMITED": "Significant concerns, limited evidence, use caution",
        "HIGH_RISK_ISOLATED": "Elevated risk indicators, isolate from sensitive systems",
        "KNOWN_THREAT": "Confirmed malicious or heavily compromised",
        "INSUFFICIENT": "Not yet scored, insufficient data for assessment",
    }
    return descriptions.get(tier, "Unknown tier")


def score_to_verdict(score: Optional[float]) -> str:
    """Convert numeric trust score to verdict tier."""
    if score is None:
        return "INSUFFICIENT"
    for tier in VerdictTier:
        if tier == VerdictTier.INSUFFICIENT:
            continue
        if score >= VERDICT_THRESHOLDS.get(tier.value, -1.0):
            return tier.value
    return VerdictTier.KNOWN_THREAT.value


def verdict_to_color(verdict: str) -> str:
    """Return hex color for verdict tier."""
    return VERDICT_COLORS.get(verdict, "#6b7280")


def get_table_schemas() -> Dict[str, TableSchema]:
    """Return schema definitions for all sentinel tables."""
    return {
        "mcp_server_registry": TableSchema(
            table_name="mcp_server_registry",
            columns=[
                {"name": "server_id", "type": "VARCHAR", "nullable": False},
                {"name": "name", "type": "VARCHAR", "nullable": False},
                {"name": "url", "type": "VARCHAR", "nullable": False},
                {"name": "description", "type": "VARCHAR", "nullable": True},
                {"name": "trust_score", "type": "DOUBLE", "nullable": True},
                {"name": "verdict", "type": "VARCHAR", "nullable": False, "default": "INSUFFICIENT"},
                {"name": "registry_source", "type": "VARCHAR", "nullable": True},
                {"name": "scan_count", "type": "INTEGER", "nullable": False, "default": 0},
                {"name": "first_seen", "type": "TIMESTAMPTZ", "nullable": False},
                {"name": "last_seen", "type": "TIMESTAMPTZ", "nullable": False},
                {"name": "last_assessed", "type": "TIMESTAMPTZ", "nullable": True},
            ],
            primary_key="server_id",
            indexes=["verdict", "trust_score", "registry_source"]
        ),
        "mcp_signal_scores": TableSchema(
            table_name="mcp_signal_scores",
            columns=[
                {"name": "server_id", "type": "VARCHAR", "nullable": False},
                {"name": "signal_name", "type": "VARCHAR", "nullable": False},
                {"name": "score", "type": "DOUBLE", "nullable": False},
                {"name": "evidence", "type": "VARCHAR", "nullable": True},
                {"name": "scored_at", "type": "TIMESTAMPTZ", "nullable": False},
            ],
            primary_key=None,
            indexes=["server_id", "signal_name"]
        ),
        "mcp_attestations": TableSchema(
            table_name="mcp_attestations",
            columns=[
                {"name": "attestation_id", "type": "VARCHAR", "nullable": False},
                {"name": "server_id", "type": "VARCHAR", "nullable": False},
                {"name": "attested_by", "type": "VARCHAR", "nullable": False},
                {"name": "attested_at", "type": "TIMESTAMPTZ", "nullable": False},
                {"name": "evidence_url", "type": "VARCHAR", "nullable": True},
                {"name": "scope", "type": "VARCHAR", "nullable": False},
                {"name": "expires_at", "type": "TIMESTAMPTZ", "nullable": True},
            ],
            primary_key="attestation_id",
            indexes=["server_id", "attested_by"]
        ),
        "mcp_threat_associations": TableSchema(
            table_name="mcp_threat_associations",
            columns=[
                {"name": "server_id", "type": "VARCHAR", "nullable": False},
                {"name": "threat_type", "type": "VARCHAR", "nullable": False},
                {"name": "severity", "type": "VARCHAR", "nullable": False},
                {"name": "evidence", "type": "VARCHAR", "nullable": True},
                {"name": "reported_at", "type": "TIMESTAMPTZ", "nullable": False},
            ],
            primary_key=None,
            indexes=["server_id", "threat_type", "severity"]
        ),
        "mcp_risk_register": TableSchema(
            table_name="mcp_risk_register",
            columns=[
                {"name": "server_id", "type": "VARCHAR", "nullable": False},
                {"name": "risk_tier", "type": "VARCHAR", "nullable": False},
                {"name": "risk_rank", "type": "INTEGER", "nullable": False},
                {"name": "threat_count", "type": "INTEGER", "nullable": False},
                {"name": "computed_at", "type": "TIMESTAMPTZ", "nullable": False},
            ],
            primary_key="server_id",
            indexes=["risk_tier", "risk_rank"]
        ),
        "audit_log": TableSchema(
            table_name="audit_log",
            columns=[
                {"name": "id", "type": "VARCHAR", "nullable": False},
                {"name": "target_server_id", "type": "VARCHAR", "nullable": True},
                {"name": "event_type", "type": "VARCHAR", "nullable": False},
                {"name": "actor", "type": "VARCHAR", "nullable": False},
                {"name": "detail", "type": "VARCHAR", "nullable": True},
                {"name": "created_at", "type": "TIMESTAMPTZ", "nullable": False},
            ],
            primary_key="id",
            indexes=["event_type", "actor", "created_at"]
        ),
        "auth_tokens": TableSchema(
            table_name="auth_tokens",
            columns=[
                {"name": "token_id", "type": "VARCHAR", "nullable": False},
                {"name": "action", "type": "VARCHAR", "nullable": False},
                {"name": "mcp_name", "type": "VARCHAR", "nullable": True},
                {"name": "submission_id", "type": "VARCHAR", "nullable": True},
                {"name": "admin_email", "type": "VARCHAR", "nullable": False},
                {"name": "expires_at", "type": "TIMESTAMPTZ", "nullable": False},
                {"name": "used", "type": "BOOLEAN", "nullable": False, "default": False},
                {"name": "used_at", "type": "TIMESTAMPTZ", "nullable": True},
            ],
            primary_key="token_id",
            indexes=["admin_email", "expires_at"]
        ),
        "service_health": TableSchema(
            table_name="service_health",
            columns=[
                {"name": "service", "type": "VARCHAR", "nullable": False},
                {"name": "last_heartbeat", "type": "TIMESTAMPTZ", "nullable": False},
            ],
            primary_key="service",
            indexes=[]
        ),
    }


def get_signal_schemas() -> List[SignalSchema]:
    """Return schema definitions for all trust signals."""
    return [
        SignalSchema(
            signal_name="injection_resilience",
            signal_type="security",
            min_score=0.0,
            max_score=1.0,
            weight=0.20,
            description="Resistance to prompt injection attacks"
        ),
        SignalSchema(
            signal_name="supply_chain_health",
            signal_type="provenance",
            min_score=0.0,
            max_score=1.0,
            weight=0.20,
            description="Health of dependencies and publishing chain"
        ),
        SignalSchema(
            signal_name="community_signal",
            signal_type="social",
            min_score=0.0,
            max_score=1.0,
            weight=0.15,
            description="GitHub stars, forks, contributor diversity"
        ),
        SignalSchema(
            signal_name="temporal_stability",
            signal_type="reliability",
            min_score=0.0,
            max_score=1.0,
            weight=0.15,
            description="Age and maintenance consistency"
        ),
        SignalSchema(
            signal_name="permission_scope",
            signal_type="security",
            min_score=0.0,
            max_score=1.0,
            weight=0.15,
            description="Appropriateness of requested permissions"
        ),
        SignalSchema(
            signal_name="tool_description_safety",
            signal_type="security",
            min_score=0.0,
            max_score=1.0,
            weight=0.15,
            description="Quality and safety of tool schema documentation"
        ),
    ]


def export_schema_as_dict() -> Dict[str, Any]:
    """Export entire schema as a dictionary for serialization."""
    return {
        "version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "verdict_tiers": [asdict(v) for v in get_verdict_schema()],
        "verdict_thresholds": VERDICT_THRESHOLDS,
        "verdict_expiry_days": VERDICT_EXPIRY_DAYS,
        "verdict_colors": VERDICT_COLORS,
        "tables": {name: asdict(schema) for name, schema in get_table_schemas().items()},
        "signals": [asdict(s) for s in get_signal_schemas()],
    }


def verify_verdict_tiers() -> bool:
    """Self-smoke test: verify all 7 verdict tiers are defined."""
    required_tiers = {
        "TRUSTED_RESEARCH",
        "ENTERPRISE_CONTROLLED",
        "AMBER_UNVERIFIED",
        "CAUTION_LIMITED",
        "HIGH_RISK_ISOLATED",
        "KNOWN_THREAT",
        "INSUFFICIENT",
    }
    defined_tiers = set(get_verdict_tiers())
    
    missing = required_tiers - defined_tiers
    if missing:
        logger.error(f"Missing verdict tiers: {missing}")
        return False
    
    extra = defined_tiers - required_tiers
    if extra:
        logger.warning(f"Extra verdict tiers defined: {extra}")
    
    for tier in required_tiers:
        if tier not in VERDICT_THRESHOLDS:
            logger.error(f"Missing threshold for {tier}")
            return False
        if tier not in VERDICT_EXPIRY_DAYS:
            logger.error(f"Missing expiry days for {tier}")
            return False
        if tier not in VERDICT_COLORS:
            logger.error(f"Missing color for {tier}")
            return False
    
    logger.info(f"Verified all {len(required_tiers)} verdict tiers")
    return True


def verify_table_schemas() -> bool:
    """Verify all expected tables have valid schema definitions."""
    required_tables = {
        "mcp_server_registry",
        "mcp_signal_scores",
        "mcp_attestations",
        "mcp_threat_associations",
        "mcp_risk_register",
        "audit_log",
        "auth_tokens",
        "service_health",
    }
    defined_tables = set(get_table_schemas().keys())
    
    missing = required_tables - defined_tables
    if missing:
        logger.error(f"Missing table schemas: {missing}")
        return False
    
    logger.info(f"Verified {len(required_tables)} table schemas")
    return True


def verify_signal_schemas() -> bool:
    """Verify all expected signal schemas are defined."""
    required_signals = {
        "injection_resilience",
        "supply_chain_health",
        "community_signal",
        "temporal_stability",
        "permission_scope",
        "tool_description_safety",
    }
    defined_signals = {s.signal_name for s in get_signal_schemas()}
    
    missing = required_signals - defined_signals
    if missing:
        logger.error(f"Missing signal schemas: {missing}")
        return False
    
    logger.info(f"Verified {len(required_signals)} signal schemas")
    return True


def run_smoke_test() -> bool:
    """Run all self-smoke tests and return True if all pass."""
    logger.info("Starting graphql_schema_integration smoke test")
    
    results = []
    results.append(("verdict_tiers", verify_verdict_tiers()))
    results.append(("table_schemas", verify_table_schemas()))
    results.append(("signal_schemas", verify_signal_schemas()))
    
    all_passed = all(r[1] for r in results)
    
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        logger.info(f"  {name}: {status}")
    
    if all_passed:
        logger.info("All smoke tests passed")
    else:
        logger.error("Some smoke tests failed")
    
    return all_passed


def check_single_instance():
    """Ensure only one instance runs."""
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.warning(f"Instance already running with PID {old_pid}")
            return False
        except OSError:
            logger.info("Stale PID file removed")
            os.remove(pid_file)
    
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    """Clean up PID file."""
    try:
        os.remove(f"/tmp/{SERVICE_NAME}.pid")
    except FileNotFoundError:
        pass


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    """Send heartbeat to service_health table."""
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
    }
    ws_write("service_health", [row])


def run():
    """Main daemon loop."""
    if not check_single_instance():
        logger.error("Cannot acquire PID lock, exiting")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"{SERVICE_NAME} started")
    send_heartbeat()
    
    while True:
        try:
            schema = export_schema_as_dict()
            logger.info(f"Schema export available: {len(schema['verdict_tiers'])} tiers, "
                       f"{len(schema['tables'])} tables, {len(schema['signals'])} signals")
            send_heartbeat()
        except Exception as e:
            logger.error(f"Error in cycle: {e}")
        
        time.sleep(300)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke":
        success = run_smoke_test()
        sys.exit(0 if success else 1)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--verify-tiers":
        success = verify_verdict_tiers()
        print(f"VERDICT TIERS: {'PASS' if success else 'FAIL'}")
        sys.exit(0 if success else 1)
    
    run()