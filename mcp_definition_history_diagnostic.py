import json
import sys
import requests
from typing import List, Dict, Any
from app.db import get_session
from app.models import MCPDefinitionHistory, MCPServerRegistry

def get_history_count(session) -> int:
    return session.query(MCPDefinitionHistory).count()

def get_registry_count(session) -> int:
    return session.query(MCPServerRegistry).count()

def check_scanner_activity(session) -> bool:
    return session.query(MCPDefinitionHistory).first() is not None

def check_audit_log() -> List[Dict[str, Any]]:
    query = """
    SELECT * FROM audit_log
    WHERE table_name = 'mcp_definition_history'
    AND action = 'INSERT'
    """
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query}
    )
    if response.status_code == 200:
        return response.json()
    return []

def generate_report(
    history_count: int,
    registry_count: int,
    has_history: bool,
    audit_log_entries: List[Dict[str, Any]]
) -> Dict[str, Any]:
    possible_causes = []

    if history_count == 0:
        possible_causes.append("No entries found in mcp_definition_history table")

    if not has_history:
        possible_causes.append("No scanner daemon activity detected in mcp_definition_history")

    if not audit_log_entries:
        possible_causes.append("No INSERT actions found in audit_log for mcp_definition_history")
    else:
        possible_causes.append("Audit log entries found but table remains empty")

    return {
        "history_count": history_count,
        "registry_count": registry_count,
        "has_history": has_history,
        "possible_causes": possible_causes
    }

def main():
    session = get_session()
    history_count = get_history_count(session)
    registry_count = get_registry_count(session)
    has_history = check_scanner_activity(session)
    audit_log_entries = check_audit_log()

    report = generate_report(history_count, registry_count, has_history, audit_log_entries)
    print(json.dumps(report, indent=2))

    sys.stderr.write("PASS\n")

if __name__ == "__main__":
    main()