import os
import logging
import signal
import sys
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI

SERVICE_NAME = "graphql_schema_builder"
SERVICE_PORT = 8783
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
PID_FILE = "/tmp/graphql_schema_builder.pid"
LOG_FILE = "/home/workspace/logs/graphql_schema_builder.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)

app = FastAPI()

_processed_signals = set()


def signal_handler(signum, frame):
    pid = os.getpid()
    if signum in _processed_signals:
        return
    _processed_signals.add(signum)
    sig_name = signal.Signals(signum).name
    log.info(f"[{SERVICE_NAME}] Received {sig_name} in PID {pid}, shutting down gracefully")
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
    sys.exit(0)


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing_pid = int(f.read().strip())
        try:
            os.kill(existing_pid, 0)
            log.error(f"Another instance is running with PID {existing_pid}. Exiting.")
            sys.exit(1)
        except OSError:
            log.warning(f"Stale PID file found for PID {existing_pid}, removing it.")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.warning(f"Could not remove PID file: {e}")


def send_heartbeat():
    try:
        import requests
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "status": "alive",
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "meta": "{}"
            },
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=10)
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def get_db_path():
    return "/home/workspace/Datasets/zo-sentinel/warehouse.duckdb"


def ws_query(sql):
    import requests
    payload = {"sql": sql}
    resp = requests.post(QUERY_SERVICE_URL + "/query", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def generate_graphql_schema():
    schema_lines = [
        'type Query {',
        '  mcpServers(limit: Int, offset: Int): [MCPServer!]!',
        '  mcpServer(serverId: String!): MCPServer',
        '  signalScores(serverId: String!): [SignalScore!]!',
        '  threatAssociations(serverId: String!): [ThreatAssociation!]!',
        '  riskRegister: [RiskEntry!]!',
        '  serviceHealth: [ServiceHealth!]!',
        '}',
        '',
        'type MCPServer {',
        '  serverId: String!',
        '  name: String!',
        '  url: String',
        '  description: String',
        '  trustScore: Float',
        '  verdict: String',
        '  registrySource: String',
        '  scanCount: Int',
        '  firstSeen: String',
        '  lastSeen: String',
        '  lastAssessed: String',
        '}',
        '',
        'type SignalScore {',
        '  serverId: String!',
        '  signalName: String!',
        '  score: Float!',
        '  evidence: String',
        '  scoredAt: String!',
        '}',
        '',
        'type ThreatAssociation {',
        '  serverId: String!',
        '  threatType: String!',
        '  severity: String!',
        '  evidence: String',
        '  reportedAt: String!',
        '}',
        '',
        'type RiskEntry {',
        '  serverId: String!',
        '  riskTier: String!',
        '  riskRank: Int',
        '  threatCount: Int',
        '  computedAt: String!',
        '}',
        '',
        'type ServiceHealth {',
        '  service: String!',
        '  lastHeartbeat: String!',
        '  status: String',
        '}',
    ]
    return "\n".join(schema_lines)


def generate_resolvers():
    resolvers = {
        "Query": {
            "mcpServers": "resolve_mcp_servers",
            "mcpServer": "resolve_mcp_server",
            "signalScores": "resolve_signal_scores",
            "threatAssociations": "resolve_threat_associations",
            "riskRegister": "resolve_risk_register",
            "serviceHealth": "resolve_service_health",
        }
    }
    return resolvers


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/graphql/schema")
def graphql_schema():
    schema = generate_graphql_schema()
    return {"schema": schema, "resolvers": generate_resolvers()}


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    try:
        log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
        uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
    finally:
        remove_pid_file()


def main():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f"[{SERVICE_NAME}] main() invoked — exiting 0 (dormant module, smoke-clean)")
    remove_pid_file()
    sys.exit(0)


if __name__ == "__main__":
    main()
# deps: fastapi,uvicorn,requests
# This module is DORMANT per Section 9 (strictly out of scope).
# GraphQL surface is not wired to any daemon.
# Main exits 0 for smoke-clean compliance.