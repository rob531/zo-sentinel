import logging
import os
import sys
from pathlib import Path

import requests
from datetime import datetime, timezone

LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "risk_tier_transitions_scoring_consumer_contract.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger(__name__)

SERVICE_NAME = "risk_tier_transitions_scoring_consumer_contract"
PORT = 8785
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
QUERY_TIMEOUT = 30
WRITE_TIMEOUT = 30
EXECUTE_TIMEOUT = 30

SIGNAL_TYPES = [
    "supply_chain",
    "community_signal",
    "temporal_stability",
    "permission_scope",
    "tool_description_safety",
    "context_efficiency",
    "domain_trust",
    "injection_resilience",
    "evidence_density",
    "registry_breadth",
    "vendor_concentration",
]

RISK_TIERS = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
VERDICT_STATES = ["TRUSTED", "AMBER", "UNTRUSTED", "UNKNOWN", "KNOWN_THREAT"]


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=QUERY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error(f"Query failed: {sql[:100]}... Error: {e}")
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=WRITE_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Write to {table} failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=EXECUTE_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Execute failed: {sql[:100]}... Error: {e}")
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_transition_id(server_id: str, from_tier: str, to_tier: str, transition_ts: str) -> str:
    content = f"{server_id}:{from_tier}:{to_tier}:{transition_ts}"
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def get_scoring_server_ids(limit: int = 100) -> list:
    sql = f"""
    SELECT DISTINCT server_id
    FROM mcp_signal_scores
    WHERE server_id IS NOT NULL
    LIMIT {limit}
    """
    rows = ws_query(sql)
    return [r.get("server_id") for r in rows if r.get("server_id")]


def get_server_risk_tier(server_id: str) -> str:
    sql = f"SELECT risk_tier FROM mcp_risk_register WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    if rows:
        return rows[0].get("risk_tier", "UNKNOWN")
    return "UNKNOWN"


def get_server_verdict(server_id: str) -> str:
    sql = f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    if rows:
        return rows[0].get("verdict", "UNKNOWN")
    return "UNKNOWN"


def get_signal_scores_for_server(server_id: str) -> dict:
    sql = f"""
    SELECT signal_name, score, evidence
    FROM mcp_signal_scores
    WHERE server_id = '{server_id}'
    AND scored_at IS NOT NULL
    ORDER BY scored_at DESC
    """
    rows = ws_query(sql)
    scores = {}
    for row in rows:
        signal = row.get("signal_name")
        if signal and signal not in scores:
            scores[signal] = {
                "score": row.get("score", 0.0),
                "evidence": row.get("evidence", ""),
            }
    return scores


def compute_composite_trust_score(signal_scores: dict) -> float:
    if not signal_scores:
        return 0.0
    total = 0.0
    count = 0
    for signal, data in signal_scores.items():
        score = data.get("score", 0.0) if isinstance(data, dict) else 0.0
        total += score
        count += 1
    return total / count if count > 0 else 0.0


def score_transition_confidence(
    from_tier: str, to_tier: str, composite_score: float, signal_count: int
) -> float:
    tier_order = {"CRITICAL": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "INFO": 1}
    from_val = tier_order.get(from_tier, 0)
    to_val = tier_order.get(to_tier, 0)
    tier_delta = abs(to_val - from_val)
    
    confidence = 0.3
    confidence += (composite_score * 0.4)
    confidence += (min(signal_count / 10.0, 1.0) * 0.2)
    confidence += (min(tier_delta / 5.0, 1.0) * 0.1)
    
    return min(confidence, 1.0)


def detect_tier_transition(server_id: str) -> dict:
    current_tier = get_server_risk_tier(server_id)
    verdict = get_server_verdict(server_id)
    
    if verdict == "KNOWN_THREAT":
        computed_tier = "CRITICAL"
    elif verdict == "UNTRUSTED":
        computed_tier = "HIGH"
    elif verdict == "AMBER":
        computed_tier = "MEDIUM"
    elif verdict == "TRUSTED":
        computed_tier = "LOW"
    else:
        computed_tier = "INFO"
    
    if current_tier != computed_tier and current_tier != "UNKNOWN":
        transition_ts = utc_now_iso()
        transition_id = compute_transition_id(server_id, current_tier, computed_tier, transition_ts)
        signal_scores = get_signal_scores_for_server(server_id)
        composite_score = compute_composite_trust_score(signal_scores)
        confidence = score_transition_confidence(
            current_tier, computed_tier, composite_score, len(signal_scores)
        )
        
        return {
            "transition_id": transition_id,
            "server_id": server_id,
            "from_tier": current_tier,
            "to_tier": computed_tier,
            "transition_ts": transition_ts,
            "confidence": round(confidence, 4),
            "composite_score": round(composite_score, 4),
            "signal_count": len(signal_scores),
            "verdict": verdict,
            "status": "pending",
        }
    
    return None


def record_transition(transition: dict) -> bool:
    table = "risk_tier_transitions"
    return ws_write(table, [transition])


def update_risk_register(server_id: str, new_tier: str) -> bool:
    sql = f"""
    INSERT INTO mcp_risk_register (server_id, risk_tier, computed_at)
    VALUES ('{server_id}', '{new_tier}', '{utc_now_iso()}')
    """
    return ws_execute(sql)


def ensure_contract_tables() -> bool:
    transitions_table = """
    CREATE TABLE IF NOT EXISTS risk_tier_transitions (
        transition_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        from_tier VARCHAR NOT NULL,
        to_tier VARCHAR NOT NULL,
        transition_ts TIMESTAMPTZ NOT NULL,
        confidence DOUBLE,
        composite_score DOUBLE,
        signal_count INTEGER,
        verdict VARCHAR,
        status VARCHAR DEFAULT 'pending',
        processed_at TIMESTAMPTZ
    )
    """
    
    scoring_contracts_table = """
    CREATE TABLE IF NOT EXISTS risk_tier_scoring_contracts (
        contract_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        signal_type VARCHAR NOT NULL,
        score DOUBLE,
        evidence TEXT,
        scored_at TIMESTAMPTZ,
        contract_version VARCHAR DEFAULT 'v1'
    )
    """
    
    transition_events_table = """
    CREATE TABLE IF NOT EXISTS risk_tier_transition_events (
        event_id VARCHAR PRIMARY KEY,
        transition_id VARCHAR NOT NULL,
        event_type VARCHAR NOT NULL,
        event_ts TIMESTAMPTZ NOT NULL,
        detail TEXT
    )
    """
    
    ok1 = ws_execute(transitions_table)
    ok2 = ws_execute(scoring_contracts_table)
    ok3 = ws_execute(transition_events_table)
    
    return ok1 and ok2 and ok3


def emit_transition_event(transition_id: str, event_type: str, detail: str) -> bool:
    import hashlib
    content = f"{transition_id}:{event_type}:{utc_now_iso()}"
    event_id = hashlib.sha256(content.encode()).hexdigest()[:32]
    
    event = {
        "event_id": event_id,
        "transition_id": transition_id,
        "event_type": event_type,
        "event_ts": utc_now_iso(),
        "detail": detail,
    }
    return ws_write("risk_tier_transition_events", [event])


def validate_contract_signature(contract_row: dict) -> bool:
    required_fields = [
        "contract_id",
        "server_id",
        "signal_type",
        "score",
        "scored_at",
    ]
    for field in required_fields:
        if field not in contract_row:
            logger.warning(f"Contract missing required field: {field}")
            return False
    return True


def score_server_for_transitions(server_id: str) -> list:
    signal_scores = get_signal_scores_for_server(server_id)
    contracts = []
    
    for signal_type, data in signal_scores.items():
        score = data.get("score", 0.0) if isinstance(data, dict) else 0.0
        evidence = data.get("evidence", "") if isinstance(data, dict) else ""
        
        import hashlib
        contract_content = f"{server_id}:{signal_type}:{utc_now_iso()}"
        contract_id = hashlib.sha256(contract_content.encode()).hexdigest()[:32]
        
        contract = {
            "contract_id": contract_id,
            "server_id": server_id,
            "signal_type": signal_type,
            "score": round(score, 4),
            "evidence": evidence,
            "scored_at": utc_now_iso(),
            "contract_version": "v1",
        }
        
        if validate_contract_signature(contract):
            contracts.append(contract)
    
    return contracts


def process_pending_transitions() -> dict:
    sql = """
    SELECT transition_id, server_id, from_tier, to_tier, transition_ts, confidence, composite_score, signal_count
    FROM risk_tier_transitions
    WHERE status = 'pending'
    ORDER BY transition_ts ASC
    LIMIT 50
    """
    pending = ws_query(sql)
    
    processed = 0
    failed = 0
    
    for row in pending:
        transition_id = row.get("transition_id")
        server_id = row.get("server_id")
        to_tier = row.get("to_tier")
        
        ok = update_risk_register(server_id, to_tier)
        if ok:
            sql_update = f"""
            UPDATE risk_tier_transitions
            SET status = 'processed', processed_at = '{utc_now_iso()}'
            WHERE transition_id = '{transition_id}'
            """
            ws_execute(sql_update)
            
            emit_transition_event(
                transition_id,
                "tier_applied",
                f"Risk tier updated from {row.get('from_tier')} to {to_tier}"
            )
            processed += 1
        else:
            failed += 1
    
    return {"processed": processed, "failed": failed, "total": len(pending)}


def health() -> dict:
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "timestamp": utc_now_iso(),
        "version": "v1",
    }


def main() -> None:
    logger.info(f"Starting {SERVICE_NAME}")
    
    ok = ensure_contract_tables()
    if not ok:
        logger.error("Failed to initialize contract tables")
        sys.exit(1)
    
    logger.info("Contract tables initialized successfully")
    
    servers = get_scoring_server_ids(limit=100)
    logger.info(f"Found {len(servers)} servers to process")
    
    transitions_found = 0
    contracts_created = 0
    
    for server_id in servers:
        contracts = score_server_for_transitions(server_id)
        if contracts:
            ws_write("risk_tier_scoring_contracts", contracts)
            contracts_created += len(contracts)
        
        transition = detect_tier_transition(server_id)
        if transition:
            if record_transition(transition):
                transitions_found += 1
                emit_transition_event(
                    transition["transition_id"],
                    "transition_detected",
                    f"Detected tier change from {transition['from_tier']} to {transition['to_tier']}"
                )
    
    result = process_pending_transitions()
    
    logger.info(
        f"Scan complete: {len(servers)} servers, "
        f"{transitions_found} transitions detected, "
        f"{contracts_created} contracts created, "
        f"{result['processed']} transitions processed"
    )
    
    sys.exit(0)


if __name__ == "__main__":
    main()