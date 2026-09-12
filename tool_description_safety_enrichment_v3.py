import time
import sys
import json
import hashlib
import re
from typing import List, Dict, Any, Tuple

WRITE_SERVICE = "http://127.0.0.1:8772/write"
QUERY_SERVICE = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE = "http://127.0.0.1:8772/execute"

SERVICE_NAME = "tool_description_safety_enrichment_v3"
SERVICE_PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/tmp"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"
POLL_SECS = 300
HEARTBEAT_INTERVAL = 60

SIGNAL_NAME = "tool_description_safety"
VERSION = "3.0"
MAX_SCORE = 1.0

HIGH_RISK_PATTERNS = [
    "sudo", "rm -rf", "eval(", "exec(", "os.system", "subprocess",
    "shell=True", "credential", "password", "api_key", "secret",
    "aws_key", "token", "auth", "bypass", "inject", "exploit",
    "root", "admin", "delete_all", "destroy_all"
]

DANGEROUS_TOOL_VERBS = [
    "delete", "destroy", "rm", "format", "drop", "truncate",
    "shutdown", "reboot", "halt", "kill", "terminate", "cancel",
    "purge", "wipe", "erase", "remove_all"
]

SAFE_TOOL_VERBS = [
    "get", "fetch", "list", "read", "query", "search", "find",
    "create", "post", "update", "edit", "modify", "append",
    "analyze", "parse", "extract", "convert", "transform",
    "calculate", "compute", "summarize", "aggregate"
]

NEUTRAL_VERBS = [
    "send", "push", "pull", "copy", "move", "duplicate",
    "execute", "run", "start", "stop", "enable", "disable"
]


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ws_query(sql: str) -> Dict[str, Any]:
    import requests
    resp = requests.post(QUERY_SERVICE, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    import requests
    resp = requests.post(WRITE_SERVICE, json={"table": table, "rows": rows}, timeout=30)
    resp.raise_for_status()
    return True


def ws_execute(sql: str) -> bool:
    import requests
    resp = requests.post(EXECUTE_SERVICE, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return True


def check_single_instance() -> bool:
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            log(f"Stale PID file, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    import os
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame) -> None:
    log(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat() -> None:
    try:
        ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": time.strftime("%Y-%m-%d %H:%M:%S")}])
    except Exception as e:
        log(f"Heartbeat failed: {e}")


def get_unscored_servers(limit: int = 50) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT server_id, name, description, url, registry_source
    FROM mcp_server_registry
    WHERE server_id NOT IN (
        SELECT DISTINCT server_id 
        FROM mcp_signal_enrichments 
        WHERE signal_name = '{SIGNAL_NAME}' AND version = '{VERSION}'
    )
    LIMIT {limit}
    """
    result = ws_query(sql)
    return result.get("rows", [])


def fetch_server_tools(server_id: str) -> List[Dict[str, Any]]:
    # `mcp_tool_schemas` exists on no plane. The bus table holding a server's
    # raw tool list is `mcp_tool_hashes`, whose `tools_raw` column is the same
    # payload this function's caller already json.loads()es -- so it is aliased
    # back to `tools` and the caller is unchanged. Refs #4080.
    sql = f"SELECT server_id, tools_raw AS tools FROM mcp_tool_hashes WHERE server_id = '{server_id}'"
    result = ws_query(sql)
    return result.get("rows", [])


def count_documented_params(schema: Dict[str, Any]) -> int:
    documented = 0
    params = schema.get("parameters", schema.get("properties", {}))
    if isinstance(params, dict):
        for param_name, param_def in params.items():
            if isinstance(param_def, dict):
                if "description" in param_def and param_def["description"]:
                    documented += 1
                elif "type" in param_def:
                    documented += 1
    return documented


def score_description_length(description: str) -> float:
    if not description:
        return 0.0
    length = len(description.strip())
    if length < 25:
        return 0.0
    elif length < 50:
        return 1.0
    elif length < 75:
        return 2.0
    elif length < 100:
        return 3.0
    elif length < 150:
        return 4.0
    elif length < 200:
        return 5.0
    elif length < 300:
        return 6.0
    elif length < 400:
        return 7.0
    elif length < 500:
        return 8.0
    elif length < 650:
        return 9.0
    elif length < 800:
        return 10.0
    elif length < 1000:
        return 11.0
    else:
        return 12.0


def score_param_documentation(param_count: int, documented_count: int) -> float:
    if param_count == 0:
        return 2.0
    doc_ratio = documented_count / param_count if param_count > 0 else 1.0
    if doc_ratio >= 1.0:
        return 4.0 + (min(param_count, 10) * 1.5)
    elif doc_ratio >= 0.8:
        return 3.0 + (min(param_count, 10) * 1.2)
    elif doc_ratio >= 0.6:
        return 2.0 + (min(param_count, 10) * 1.0)
    elif doc_ratio >= 0.4:
        return 1.0 + (min(param_count, 10) * 0.8)
    else:
        return min(documented_count * 1.5, 6.0)


def score_returns_documented(schema: Dict[str, Any]) -> float:
    returns = schema.get("returns", schema.get("response", schema.get("output", None)))
    if returns:
        if isinstance(returns, dict):
            if "description" in returns and returns["description"]:
                return 3.0
            elif "type" in returns:
                return 2.0
    if "responseSchema" in schema or "response_schema" in schema:
        return 2.5
    return 0.0


def score_examples(description: str, schema: Dict[str, Any]) -> float:
    example_bonus = 0.0
    desc_lower = description.lower() if description else ""
    example_count = desc_lower.count("example")
    if example_count >= 3:
        example_bonus = 4.0
    elif example_count == 2:
        example_bonus = 3.0
    elif example_count == 1:
        example_bonus = 2.0
    if "examples" in schema:
        ex_list = schema["examples"]
        if isinstance(ex_list, list):
            example_bonus += min(len(ex_list) * 1.5, 4.5)
    if "$example" in schema or "example" in str(schema.get("x-", {})):
        example_bonus += 2.0
    return min(example_bonus, 6.0)


def score_deprecation_notice(schema: Dict[str, Any]) -> float:
    if schema.get("deprecated", False):
        return -4.0
    if schema.get("deprecationMessage"):
        return -3.0
    return 0.0


def score_schema_complexity(schema: Dict[str, Any]) -> float:
    complexity = 0.0
    required = schema.get("required", [])
    if isinstance(required, list):
        complexity += min(len(required) * 0.5, 3.0)
    properties = schema.get("properties", schema.get("parameters", {}))
    if isinstance(properties, dict):
        complexity += min(len(properties) * 0.3, 2.0)
        for prop_def in properties.values():
            if isinstance(prop_def, dict):
                if "enum" in prop_def:
                    complexity += 0.3
                if "default" in prop_def:
                    complexity += 0.2
                if "minimum" in prop_def or "maximum" in prop_def:
                    complexity += 0.2
    if "allOf" in schema or "anyOf" in schema or "oneOf" in schema:
        complexity += 2.0
    return min(complexity, 5.0)


def score_schema_validation(schema: Dict[str, Any]) -> float:
    validation_bonus = 0.0
    if "x-validate" in schema or schema.get("validate", False):
        validation_bonus += 1.5
    if "x-sanitize" in schema:
        validation_bonus += 1.0
    properties = schema.get("properties", schema.get("parameters", {}))
    if isinstance(properties, dict):
        for prop_def in properties.values():
            if isinstance(prop_def, dict):
                if "pattern" in prop_def:
                    validation_bonus += 0.5
                if "format" in prop_def:
                    validation_bonus += 0.3
                if "minLength" in prop_def or "maxLength" in prop_def:
                    validation_bonus += 0.4
    return min(validation_bonus, 4.0)


def score_security_patterns(description: str) -> float:
    penalty = 0.0
    if not description:
        return 0.0
    desc_lower = description.lower()
    for pattern in HIGH_RISK_PATTERNS:
        if pattern in desc_lower:
            penalty -= 2.0
    return max(penalty, -8.0)


def score_verb_safety(tool_name: str) -> float:
    if not tool_name:
        return 0.0
    name_lower = tool_name.lower()
    if any(name_lower.startswith(verb) for verb in SAFE_TOOL_VERBS):
        return 3.0
    elif any(name_lower.startswith(verb) for verb in NEUTRAL_VERBS):
        return 1.0
    elif any(name_lower.startswith(verb) for verb in DANGEROUS_TOOL_VERBS):
        return -3.0
    return 0.5


def score_readme_quality(description: str) -> float:
    if not description:
        return 0.0
    bonus = 0.0
    desc_lower = description.lower()
    if "purpose" in desc_lower or "function" in desc_lower:
        bonus += 1.0
    if "usage" in desc_lower or "how to" in desc_lower:
        bonus += 1.0
    if "parameter" in desc_lower or "argument" in desc_lower:
        bonus += 1.0
    if "return" in desc_lower or "result" in desc_lower:
        bonus += 0.5
    if "note" in desc_lower or "warning" in desc_lower:
        bonus += 0.5
    if description.count(".") >= 3:
        bonus += 1.0
    return min(bonus, 4.0)


def compute_score(metadata: Dict[str, Any]) -> float:
    description = metadata.get("description", "")
    schema = metadata.get("schema", {})
    
    desc_len_score = score_description_length(description)
    
    schema_params = schema.get("parameters", schema.get("properties", {}))
    param_count = len(schema_params) if isinstance(schema_params, dict) else 0
    
    documented_params = count_documented_params(schema)
    param_doc_score = score_param_documentation(param_count, documented_params)
    
    returns_score = score_returns_documented(schema)
    examples_score = score_examples(description, schema)
    deprecation_penalty = score_deprecation_notice(schema)
    complexity_score = score_schema_complexity(schema)
    validation_score = score_schema_validation(schema)
    security_penalty = score_security_patterns(description)
    
    tool_name = metadata.get("tool_name", "")
    verb_safety_score = score_verb_safety(tool_name)
    
    readme_quality_score = score_readme_quality(description)
    
    total_raw = (
        desc_len_score +
        param_doc_score +
        returns_score +
        examples_score +
        deprecation_penalty +
        complexity_score +
        validation_score +
        security_penalty +
        verb_safety_score +
        readme_quality_score
    )
    
    raw_min = -16.0
    raw_max = 52.5
    normalized = (total_raw - raw_min) / (raw_max - raw_min) if raw_max != raw_min else 0.5
    normalized = max(0.0, min(1.0, normalized))
    
    return round(normalized, 3)


def ensure_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        server_id VARCHAR,
        signal_name VARCHAR,
        version VARCHAR,
        score DOUBLE,
        metadata JSON,
        computed_at TIMESTAMP,
        PRIMARY KEY (server_id, signal_name, version)
    )
    """
    try:
        ws_execute(sql)
    except Exception:
        pass


def compute_batch_scores(servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []
    for server in servers:
        server_id = server["server_id"]
        description = server.get("description", "")
        tool_name = server.get("name", "")
        
        tools = fetch_server_tools(server_id)
        
        if tools:
            for tool in tools:
                try:
                    schema = tool.get("tools", {})
                    if isinstance(schema, str):
                        schema = json.loads(schema)
                    if isinstance(schema, list) and len(schema) > 0:
                        schema = schema[0] if isinstance(schema[0], dict) else {}
                    
                    metadata = {
                        "server_id": server_id,
                        "description": description,
                        "tool_name": tool_name,
                        "schema": schema
                    }
                    
                    score = compute_score(metadata)
                    
                    results.append({
                        "server_id": server_id,
                        "signal_name": SIGNAL_NAME,
                        "version": VERSION,
                        "score": score,
                        "metadata": json.dumps(metadata),
                        "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")
                    })
                except Exception as e:
                    log(f"Error processing tool for {server_id}: {e}")
        else:
            schema = {"parameters": {}, "properties": {}}
            metadata = {
                "server_id": server_id,
                "description": description,
                "tool_name": tool_name,
                "schema": schema
            }
            
            score = compute_score(metadata)
            
            results.append({
                "server_id": server_id,
                "signal_name": SIGNAL_NAME,
                "version": VERSION,
                "score": score,
                "metadata": json.dumps(metadata),
                "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })
    
    return results


def get_score_band(score: float) -> str:
    if score >= 0.9:
        return "excellent"
    elif score >= 0.75:
        return "good"
    elif score >= 0.6:
        return "fair"
    elif score >= 0.4:
        return "poor"
    else:
        return "very_poor"


def run() -> None:
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        sys.exit(1)
    
    log(f"Starting {SERVICE_NAME} v{VERSION}")
    
    ensure_table()
    send_heartbeat()
    
    while True:
        try:
            servers = get_unscored_servers(limit=30)
            if not servers:
                log("No unscored servers found, sleeping")
                time.sleep(POLL_SECS)
                send_heartbeat()
                continue
            
            log(f"Processing {len(servers)} servers")
            
            results = compute_batch_scores(servers)
            
            if results:
                ws_write("mcp_signal_enrichments", results)
                log(f"Wrote {len(results)} enrichment records")
            
            send_heartbeat()
            time.sleep(5)
            
        except Exception as e:
            log(f"Error in cycle: {e}")
            time.sleep(30)
            send_heartbeat()


def main() -> None:
    log(f"Running {SERVICE_NAME} v{VERSION} - manual execution")
    ensure_table()
    servers = get_unscored_servers(limit=100)
    log(f"Found {len(servers)} unscored servers")
    
    results = compute_batch_scores(servers)
    
    distinct_scores = len(set(r["score"] for r in results))
    log(f"Computed scores for {len(results)} servers with {distinct_scores} distinct values")
    
    if results:
        ws_write("mcp_signal_enrichments", results)
        log(f"Wrote {len(results)} enrichment records to database")
    
    score_dist = {}
    for r in results:
        band = get_score_band(r["score"])
        score_dist[band] = score_dist.get(band, 0) + 1
    
    for band, count in sorted(score_dist.items()):
        log(f"  {band}: {count}")
    
    return distinct_scores >= 20


if __name__ == "__main__":
    run()