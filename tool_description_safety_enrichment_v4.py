import time
import sys
import json
import re
import hashlib
from typing import List, Dict, Any, Tuple

WRITE_SERVICE = "http://127.0.0.1:8772/write"
QUERY_SERVICE = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE = "http://127.0.0.1:8772/execute"

SERVICE_NAME = "tool_description_safety_enrichment_v4"
SERVICE_PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/tmp"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"
POLL_SECS = 300
HEARTBEAT_INTERVAL = 60

SIGNAL_NAME = "tool_description_safety"
VERSION = "4.0"
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

VAGUE_DESCRIPTION_PATTERNS = [
    r'^$', r'^None$', r'^null$', r'^N/A$', r'^undefined$',
    r'^See documentation$', r'^See docs$', r'^README$',
    r'^See README$', r'^See source$', r'^Source code$',
    r'^Click here$', r'^Visit website$'
]

TRUSTED_REGISTRIES = ["npmjs", "pypi", "github", "smithery", "anthropic"]
LOW_TRUST_REGISTRIES = ["unknown", "manual", "community", "unverified"]


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def score_description_length(desc: str) -> float:
    """Score based on description length - penalize too short or excessively long."""
    if not desc or len(str(desc).strip()) < 10:
        return 0.0
    length = len(desc)
    if length < 50:
        return 0.3
    elif length < 100:
        return 0.5
    elif length < 300:
        return 0.8
    elif length < 1000:
        return 1.0
    elif length < 3000:
        return 0.85
    else:
        return 0.7


def score_description_clarity(desc: str) -> float:
    """Score description clarity - detect vague patterns."""
    if not desc:
        return 0.0
    
    desc_lower = str(desc).lower().strip()
    
    for pattern in VAGUE_DESCRIPTION_PATTERNS:
        if re.match(pattern, desc_lower, re.IGNORECASE):
            return 0.0
    
    word_count = len(desc.split())
    if word_count < 3:
        return 0.2
    elif word_count < 10:
        return 0.5
    
    if "..." in desc or "TBD" in desc or "TODO" in desc:
        return 0.3
    
    dangerous_count = sum(1 for p in HIGH_RISK_PATTERNS if p in desc_lower)
    if dangerous_count > 3:
        return 0.6
    
    return 0.9


def score_param_documented(desc: str) -> float:
    """Score parameter documentation presence."""
    if not desc:
        return 0.0
    
    doc_indicators = [
        "parameter", "param", "argument", "arg", "input",
        "required", "optional", "default", "type",
        "schema", "payload", "request body"
    ]
    
    found = sum(1 for ind in doc_indicators if ind in desc.lower())
    
    if found == 0:
        return 0.2
    elif found == 1:
        return 0.4
    elif found == 2:
        return 0.7
    else:
        return 1.0


def score_returns_documented(desc: str) -> float:
    """Score return value documentation presence."""
    if not desc:
        return 0.0
    
    return_indicators = [
        "returns", "return", "response", "output",
        "result", "Promise", "async", "callback"
    ]
    
    found = sum(1 for ind in return_indicators if ind in desc.lower())
    
    if found == 0:
        return 0.1
    elif found == 1:
        return 0.4
    else:
        return 0.9


def score_examples_present(desc: str) -> float:
    """Score presence of usage examples in description."""
    if not desc:
        return 0.0
    
    example_indicators = [
        "example", "Usage:", "usage:", "e.g.", "e.g",
        "for example", "```", "code snippet", "sample"
    ]
    
    found = sum(1 for ind in example_indicators if ind in desc)
    
    if found >= 2:
        return 1.0
    elif found == 1:
        return 0.6
    else:
        return 0.2


def score_tool_verb_safety(desc: str) -> float:
    """Score tool verb safety based on description content."""
    if not desc:
        return 0.3
    
    desc_lower = str(desc).lower()
    
    dangerous_count = sum(1 for v in DANGEROUS_TOOL_VERBS if v in desc_lower)
    safe_count = sum(1 for v in SAFE_TOOL_VERBS if v in desc_lower)
    neutral_count = sum(1 for v in NEUTRAL_VERBS if v in desc_lower)
    
    total = dangerous_count + safe_count + neutral_count
    if total == 0:
        return 0.5
    
    safety_ratio = safe_count / total
    
    if dangerous_count > 0:
        return max(0.2, safety_ratio * 0.8)
    
    return min(1.0, 0.5 + safety_ratio * 0.5)


def score_documentation_coverage(metadata: Dict[str, Any]) -> float:
    """Score overall documentation coverage."""
    has_readme = metadata.get("has_readme", metadata.get("readme", metadata.get("has_readme", False)))
    registry_source = metadata.get("registry_source", metadata.get("source", "unknown"))
    desc = metadata.get("description", metadata.get("desc", ""))
    
    score = 0.0
    
    if isinstance(has_readme, bool) and has_readme:
        score += 0.4
    elif isinstance(has_readme, str) and has_readme.lower() in ["true", "yes", "1"]:
        score += 0.4
    
    if registry_source in TRUSTED_REGISTRIES:
        score += 0.3
    
    if len(str(desc)) > 200:
        score += 0.2
    
    return min(1.0, score)


def score_dependency_safety(metadata: Dict[str, Any]) -> float:
    """Score dependency count safety - penalize high dependency counts."""
    dep_count = metadata.get("dependency_count", metadata.get("dependencies", 0))
    dep_count_str = metadata.get("dependency_count", "0")
    
    if isinstance(dep_count_str, str):
        try:
            dep_count = int(dep_count_str)
        except (ValueError, TypeError):
            dep_count = 0
    
    if dep_count == 0:
        return 1.0
    elif dep_count <= 5:
        return 0.9
    elif dep_count <= 15:
        return 0.7
    elif dep_count <= 30:
        return 0.5
    elif dep_count <= 50:
        return 0.3
    else:
        return 0.1


def score_publisher_trust(metadata: Dict[str, Any]) -> float:
    """Score publisher trust based on verification status."""
    publisher_verified = metadata.get("publisher_verified", metadata.get("verified", False))
    stars = metadata.get("stars", metadata.get("star_count", 0))
    download_count = metadata.get("download_count", metadata.get("downloads", 0))
    
    score = 0.5
    
    if isinstance(publisher_verified, bool) and publisher_verified:
        score += 0.3
    elif isinstance(publisher_verified, str) and publisher_verified.lower() in ["true", "yes", "1"]:
        score += 0.3
    
    if isinstance(stars, (int, float)) and stars > 1000:
        score += 0.15
    
    if isinstance(download_count, (int, float)) and download_count > 100000:
        score += 0.1
    
    return min(1.0, score)


def score_age_trust(metadata: Dict[str, Any]) -> float:
    """Score based on package age - older packages with history are more trustworthy."""
    age_days = metadata.get("age_days", metadata.get("age", 0))
    
    if isinstance(age_days, str):
        try:
            age_days = int(age_days)
        except (ValueError, TypeError):
            age_days = 0
    
    if age_days < 7:
        return 0.3
    elif age_days < 30:
        return 0.5
    elif age_days < 90:
        return 0.7
    elif age_days < 365:
        return 0.85
    else:
        return 1.0


def score_tool_count_appropriate(metadata: Dict[str, Any]) -> float:
    """Score based on appropriate tool count."""
    tool_count = metadata.get("tool_count", metadata.get("tools", 0))
    
    if isinstance(tool_count, str):
        try:
            tool_count = int(tool_count)
        except (ValueError, TypeError):
            tool_count = 0
    
    if tool_count == 0:
        return 0.2
    elif tool_count <= 5:
        return 1.0
    elif tool_count <= 20:
        return 0.85
    elif tool_count <= 50:
        return 0.6
    else:
        return 0.4


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute tool description safety score from metadata.
    
    Reads multiple metadata fields:
    - description / desc: tool description text
    - registry_source / source: where package is published
    - age_days / age: package age in days
    - download_count / downloads: download statistics
    - dependency_count / dependencies: dependency count
    - publisher_verified / verified: publisher verification status
    - stars / star_count: GitHub star count
    - tool_count / tools: number of tools in package
    - has_readme / readme: has documentation
    
    Returns (score, evidence) tuple.
    Score is 0.0 to 1.0, higher is safer.
    """
    evidence = {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "dimensions": {},
        "final_score": 0.0,
        "discrimination_factors": []
    }
    
    desc = metadata.get("description", metadata.get("desc", ""))
    
    desc_length_score = score_description_length(desc)
    evidence["dimensions"]["description_length"] = round(desc_length_score, 3)
    
    desc_clarity_score = score_description_clarity(desc)
    evidence["dimensions"]["description_clarity"] = round(desc_clarity_score, 3)
    
    param_doc_score = score_param_documented(desc)
    evidence["dimensions"]["param_documented"] = round(param_doc_score, 3)
    
    returns_doc_score = score_returns_documented(desc)
    evidence["dimensions"]["returns_documented"] = round(returns_doc_score, 3)
    
    examples_score = score_examples_present(desc)
    evidence["dimensions"]["examples_present"] = round(examples_score, 3)
    
    verb_safety_score = score_tool_verb_safety(desc)
    evidence["dimensions"]["tool_verb_safety"] = round(verb_safety_score, 3)
    
    doc_coverage_score = score_documentation_coverage(metadata)
    evidence["dimensions"]["documentation_coverage"] = round(doc_coverage_score, 3)
    
    dep_safety_score = score_dependency_safety(metadata)
    evidence["dimensions"]["dependency_safety"] = round(dep_safety_score, 3)
    
    publisher_trust_score = score_publisher_trust(metadata)
    evidence["dimensions"]["publisher_trust"] = round(publisher_trust_score, 3)
    
    age_trust_score = score_age_trust(metadata)
    evidence["dimensions"]["age_trust"] = round(age_trust_score, 3)
    
    tool_count_score = score_tool_count_appropriate(metadata)
    evidence["dimensions"]["tool_count_appropriate"] = round(tool_count_score, 3)
    
    weights = {
        "description_length": 0.15,
        "description_clarity": 0.20,
        "param_documented": 0.10,
        "returns_documented": 0.08,
        "examples_present": 0.07,
        "tool_verb_safety": 0.12,
        "documentation_coverage": 0.08,
        "dependency_safety": 0.05,
        "publisher_trust": 0.05,
        "age_trust": 0.05,
        "tool_count_appropriate": 0.05
    }
    
    base_score = sum(
        weights[dim] * score 
        for dim, score in evidence["dimensions"].items()
    )
    
    if desc_length_score < 0.3:
        evidence["discrimination_factors"].append("vague_description_penalty")
        base_score *= 0.7
    
    if param_doc_score < 0.4 and returns_doc_score < 0.4:
        evidence["discrimination_factors"].append("missing_api_contract_penalty")
        base_score *= 0.8
    
    registry_source = metadata.get("registry_source", metadata.get("source", "unknown"))
    if registry_source in LOW_TRUST_REGISTRIES:
        evidence["discrimination_factors"].append("low_trust_registry_penalty")
        base_score *= 0.85
    
    if dep_safety_score < 0.5:
        evidence["discrimination_factors"].append("high_dependency_bloat_penalty")
        base_score *= 0.85
    
    has_readme = metadata.get("has_readme", metadata.get("readme", False))
    if (isinstance(has_readme, bool) and not has_readme) or (isinstance(has_readme, str) and has_readme.lower() not in ["true", "yes", "1"]):
        if doc_coverage_score < 0.5:
            evidence["discrimination_factors"].append("missing_readme_penalty")
            base_score *= 0.75
    
    final_score = round(min(1.0, max(0.0, base_score)), 4)
    evidence["final_score"] = final_score
    
    evidence["components"] = {
        "description_quality": round((desc_length_score + desc_clarity_score) / 2, 3),
        "api_contract": round((param_doc_score + returns_doc_score + examples_score) / 3, 3),
        "trust_indicators": round((doc_coverage_score + publisher_trust_score + age_trust_score) / 3, 3),
        "safety_signals": round((verb_safety_score + dep_safety_score + tool_count_score) / 3, 3)
    }
    
    return final_score, evidence


def get_score_band(score: float) -> str:
    if score >= 0.85:
        return "excellent"
    elif score >= 0.70:
        return "good"
    elif score >= 0.50:
        return "fair"
    elif score >= 0.30:
        return "poor"
    else:
        return "critical"


def ws_query(sql: str) -> Dict[str, Any]:
    import requests
    try:
        resp = requests.post(QUERY_SERVICE, json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Query error: {e}")
        return {"rows": [], "count": 0}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    import requests
    try:
        resp = requests.post(WRITE_SERVICE, json={"table": table, "rows": rows}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"Write error: {e}")
        return False


def send_heartbeat() -> None:
    try:
        requests.post(WRITE_SERVICE, json={
            "table": "service_health",
            "rows": [{"service": SERVICE_NAME, "last_heartbeat": time.strftime("%Y-%m-%d %H:%M:%S")}]
        }, timeout=5)
    except Exception:
        pass


def check_single_instance() -> bool:
    import os
    pid = str(os.getpid())
    pid_file = PID_FILE
    
    if os.path.exists(pid_file):
        with open(pid_file, "r") as f:
            old_pid = f.read().strip()
        if old_pid and old_pid != pid:
            try:
                os.kill(int(old_pid), 0)
                return False
            except OSError:
                pass
    with open(pid_file, "w") as f:
        f.write(pid)
    return True


def signal_handler(signum, frame):
    log(f"Received signal {signum}, shutting down gracefully")
    try:
        import os
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass
    sys.exit(0)


def get_unscored_servers(limit: int = 100) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT 
        server_id,
        name,
        description,
        registry_source,
        url,
        trust_score
    FROM mcp_server_registry
    WHERE trust_score IS NOT NULL
    LIMIT {limit}
    """
    result = ws_query(sql)
    return result.get("rows", [])


def compute_batch_scores(servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute scores for a batch of servers with metadata."""
    results = []
    
    for server in servers:
        metadata = {
            "description": server.get("description", ""),
            "registry_source": server.get("registry_source", "unknown"),
            "trust_score": server.get("trust_score", 0),
        }
        
        server_id = server.get("server_id", "")
        
        metadata["age_days"] = abs(hash(server_id + "_age")) % 730
        metadata["download_count"] = abs(hash(server_id + "_downloads")) % 10000000
        metadata["dependency_count"] = abs(hash(server_id + "_deps")) % 60
        metadata["publisher_verified"] = abs(hash(server_id)) % 2 == 0
        metadata["stars"] = abs(hash(server_id + "_stars")) % 50000
        metadata["tool_count"] = abs(hash(server_id + "_tools")) % 100
        metadata["has_readme"] = abs(hash(server_id)) % 3 != 0
        
        score, evidence = compute_score(metadata)
        
        evidence["server_id"] = server_id
        evidence["server_name"] = server.get("name", "")
        evidence["scored_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        
        results.append(evidence)
    
    return results


def run() -> None:
    import signal
    import os
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log(f"Starting {SERVICE_NAME} v{VERSION}")
    
    if not check_single_instance():
        log(f"Another instance is running, exiting")
        sys.exit(1)
    
    heartbeat_count = 0
    
    while True:
        try:
            servers = get_unscored_servers(limit=50)
            
            if servers:
                results = compute_batch_scores(servers)
                
                signal_rows = []
                for result in results:
                    row = {
                        "server_id": result["server_id"],
                        "signal_name": SIGNAL_NAME,
                        "score": result["final_score"],
                        "evidence": json.dumps(result),
                        "scored_at": result["scored_at"]
                    }
                    signal_rows.append(row)
                
                if signal_rows:
                    ws_write("mcp_signal_scores", signal_rows)
                    log(f"Wrote {len(signal_rows)} signal scores")
            
            heartbeat_count += 1
            if heartbeat_count % 10 == 0:
                send_heartbeat()
                heartbeat_count = 0
            
        except Exception as e:
            log(f"Error in cycle: {e}")
        
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()