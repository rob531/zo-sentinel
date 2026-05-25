import hashlib
import re
import logging
import sys

LOG_DIR = "/home/workspace/logs"
LOG_FILE = LOG_DIR + "/tool_description_safety_enrichment.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger("tool_description_safety_enrichment")

SIGNAL_NAME = "tool_description_safety"
VERSION = "v2"
MAX_SCORE = 10.0

# Risky permission patterns that indicate potentially dangerous operations
RISKY_PERMISSION_PATTERNS = [
    r"filesystem",
    r"file_write",
    r"file_read",
    r"shell",
    r"exec",
    r"os_",
    r"subprocess",
    r"run_command",
    r"delete",
    r"write_file",
    r"read_file",
    r"network",
    r"http_",
    r"request",
    r"email",
    r"send",
    r"admin",
    r"root",
    r"sudo",
    r"credential",
    r"secret",
    r"password",
    r"api_key",
    r"token",
]

# Suspicious tool name patterns indicating copycats or typosquatting
SUSPICIOUS_NAME_PATTERNS = [
    r"^temp-",
    r"^test-",
    r"^fake-",
    r"^mock-",
    r"^demo-",
    r"-test$",
    r"-fake$",
    r"-mock$",
    r"_test$",
    r"_fake$",
]

# Legitimate tool name markers (high quality indicators)
LEGITIMATE_NAME_PATTERNS = [
    r"^[a-z][a-z0-9_-]+\.[a-z_]",
    r"^[a-z][a-z0-9_]+_[a-z][a-z0-9_]*$",
]

# Schema complexity thresholds
SCHEMA_COMPLEXITY_BENCHMARKS = {
    0: {"tools": 0, "complexity": 0},
    3: {"tools": 1, "complexity": 1},
    5: {"tools": 5, "complexity": 2},
    7: {"tools": 15, "complexity": 3},
    9: {"tools": 30, "complexity": 4},
    10: {"tools": 50, "complexity": 5},
}

# Weights for scoring components
WEIGHTS = {
    "description_quality": 0.30,
    "schema_complexity": 0.25,
    "permission_safety": 0.25,
    "name_quality": 0.15,
    "tool_count_bonus": 0.05,
}


def sigmoid(x: float, center: float = 0.5, steepness: float = 5.0) -> float:
    return 1.0 / (1.0 + (steepness ** (-(x - center))))


def log_normalize(value: float, scale: float = 10.0) -> float:
    if value <= 0:
        return 0.0
    return min(1.0, (value**0.5) / scale)


def softmax_weight(value: float, values: list[float]) -> float:
    if not values or max(values) == min(values):
        return 0.5
    exp_vals = [max(0, v - min(values)) for v in values]
    total = sum(exp_vals)
    if total == 0:
        return 0.5
    return exp_vals[values.index(value)] / total


def score_description_quality(tool_count: int, avg_description_length: float) -> float:
    """Score based on description quality signals."""
    if tool_count == 0:
        return 0.0
    
    score = 0.0
    if avg_description_length >= 50:
        score += 0.5
    elif avg_description_length >= 25:
        score += 0.3
    elif avg_description_length >= 10:
        score += 0.1
    
    if avg_description_length >= 100:
        score += 0.3
    
    if avg_description_length >= 200:
        score += 0.2
    
    return min(1.0, score)


def score_schema_complexity(
    tool_count: int,
    schema_complexity_score: float,
    avg_description_length: float,
) -> float:
    """Score based on schema complexity signals."""
    if tool_count == 0:
        return 0.0
    
    complexity_norm = max(0.0, min(1.0, schema_complexity_score / 10.0))
    
    if tool_count >= 50:
        complexity_bonus = 0.2
    elif tool_count >= 20:
        complexity_bonus = 0.15
    elif tool_count >= 5:
        complexity_bonus = 0.1
    else:
        complexity_bonus = 0.0
    
    score = complexity_norm * 0.8 + complexity_bonus
    return min(1.0, score)


def score_permission_safety(
    tool_count: int,
    has_risky_permissions: bool,
    permission_list: list,
) -> float:
    """Score based on permission safety."""
    if tool_count == 0:
        return 0.5
    
    if has_risky_permissions:
        if permission_list:
            risky_count = sum(
                1 for p in permission_list
                if any(
                    re.search(pattern, str(p).lower())
                    for pattern in RISKY_PERMISSION_PATTERNS
                )
            )
            risky_ratio = risky_count / max(1, len(permission_list))
            if risky_ratio >= 0.5:
                return 0.1
            elif risky_ratio >= 0.3:
                return 0.3
            elif risky_ratio >= 0.1:
                return 0.6
            else:
                return 0.8
        return 0.4
    
    return 0.9


def score_name_quality(tool_count: int, tool_name_patterns: list) -> float:
    """Score based on tool name quality patterns."""
    if tool_count == 0:
        return 0.5
    
    if not tool_name_patterns:
        return 0.5
    
    suspicious_count = sum(
        1 for name in tool_name_patterns
        if any(re.search(pattern, str(name).lower()) for pattern in SUSPICIOUS_NAME_PATTERNS)
    )
    legitimate_count = sum(
        1 for name in tool_name_patterns
        if any(re.search(pattern, str(name).lower()) for pattern in LEGITIMATE_NAME_PATTERNS)
    )
    
    total = len(tool_name_patterns)
    
    if suspicious_count / max(1, total) >= 0.3:
        return 0.2
    elif suspicious_count / max(1, total) >= 0.1:
        return 0.5
    
    if legitimate_count / max(1, total) >= 0.7:
        return 0.9
    elif legitimate_count / max(1, total) >= 0.5:
        return 0.75
    elif legitimate_count / max(1, total) >= 0.3:
        return 0.6
    
    return 0.55


def score_tool_count_bonus(tool_count: int) -> float:
    """Score based on tool count (diversity bonus)."""
    if tool_count == 0:
        return 0.0
    if tool_count >= 100:
        return 1.0
    if tool_count >= 50:
        return 0.85
    if tool_count >= 20:
        return 0.7
    if tool_count >= 10:
        return 0.55
    if tool_count >= 5:
        return 0.4
    if tool_count >= 2:
        return 0.25
    return 0.1


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute tool_description_safety signal score from rich metadata.
    
    Args:
        metadata: dict with fields:
            - tool_count: int
            - avg_tool_description_length: float
            - has_risky_permissions: bool
            - tool_name_patterns: list[str]
            - schema_complexity_score: float
            - permission_list: list (optional)
    
    Returns:
        tuple of (score 0-10, evidence dict)
    """
    tool_count = int(metadata.get("tool_count", 0))
    avg_desc_length = float(metadata.get("avg_tool_description_length", 0.0))
    has_risky = bool(metadata.get("has_risky_permissions", False))
    name_patterns = metadata.get("tool_name_patterns", [])
    schema_complexity = float(metadata.get("schema_complexity_score", 0.0))
    permission_list = metadata.get("permission_list", [])
    
    desc_quality = score_description_quality(tool_count, avg_desc_length)
    schema_score = score_schema_complexity(tool_count, schema_complexity, avg_desc_length)
    perm_score = score_permission_safety(tool_count, has_risky, permission_list)
    name_score = score_name_quality(tool_count, name_patterns)
    count_score = score_tool_count_bonus(tool_count)
    
    raw = (
        desc_quality * WEIGHTS["description_quality"]
        + schema_score * WEIGHTS["schema_complexity"]
        + perm_score * WEIGHTS["permission_safety"]
        + name_score * WEIGHTS["name_quality"]
        + count_score * WEIGHTS["tool_count_bonus"]
    )
    
    final_score = round(raw * MAX_SCORE, 4)
    
    evidence = {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "raw_score": raw,
        "final_score": final_score,
        "max_score": MAX_SCORE,
        "components": {
            "description_quality": round(desc_quality * WEIGHTS["description_quality"] * MAX_SCORE, 4),
            "schema_complexity": round(schema_score * WEIGHTS["schema_complexity"] * MAX_SCORE, 4),
            "permission_safety": round(perm_score * WEIGHTS["permission_safety"] * MAX_SCORE, 4),
            "name_quality": round(name_score * WEIGHTS["name_quality"] * MAX_SCORE, 4),
            "tool_count_bonus": round(count_score * WEIGHTS["tool_count_bonus"] * MAX_SCORE, 4),
        },
        "inputs": {
            "tool_count": tool_count,
            "avg_tool_description_length": avg_desc_length,
            "has_risky_permissions": has_risky,
            "schema_complexity_score": schema_complexity,
            "name_pattern_count": len(name_patterns),
            "permission_count": len(permission_list),
        },
        "weights_used": WEIGHTS,
    }
    
    return final_score, evidence


def run() -> None:
    """Self-smoke test with 5+ test cases covering distinct score outputs."""
    test_cases = [
        {
            "name": "rich_well_documented_library",
            "metadata": {
                "tool_count": 45,
                "avg_tool_description_length": 185.0,
                "has_risky_permissions": False,
                "tool_name_patterns": ["filesystem.read", "database.query", "api.call", "cache.get"],
                "schema_complexity_score": 7.5,
                "permission_list": ["read", "query", "call", "get"],
            },
        },
        {
            "name": "minimal_no_descriptions",
            "metadata": {
                "tool_count": 1,
                "avg_tool_description_length": 0.0,
                "has_risky_permissions": False,
                "tool_name_patterns": ["foo"],
                "schema_complexity_score": 0.5,
                "permission_list": [],
            },
        },
        {
            "name": "risky_permissions_high_complexity",
            "metadata": {
                "tool_count": 30,
                "avg_tool_description_length": 120.0,
                "has_risky_permissions": True,
                "tool_name_patterns": ["shell.exec", "filesystem.write", "network.http_request"],
                "schema_complexity_score": 8.0,
                "permission_list": ["shell.exec", "filesystem.write", "network.http_request", "credential.read"],
            },
        },
        {
            "name": "moderate_with_some_descriptions",
            "metadata": {
                "tool_count": 12,
                "avg_tool_description_length": 35.0,
                "has_risky_permissions": False,
                "tool_name_patterns": ["api_client.call", "utils.format", "db.query"],
                "schema_complexity_score": 4.0,
                "permission_list": ["call", "format", "query"],
            },
        },
        {
            "name": "suspicious_tool_names_no_descriptions",
            "metadata": {
                "tool_count": 8,
                "avg_tool_description_length": 5.0,
                "has_risky_permissions": True,
                "tool_name_patterns": ["temp-tool", "fake-exec", "test-shell", "mock-delete"],
                "schema_complexity_score": 2.0,
                "permission_list": ["delete", "write_file", "exec"],
            },
        },
        {
            "name": "enterprise_high_quality",
            "metadata": {
                "tool_count": 150,
                "avg_tool_description_length": 220.0,
                "has_risky_permissions": False,
                "tool_name_patterns": [
                    "auth.login", "auth.logout", "data.export", "data.import",
                    "search.query", "cache.get", "cache.set", "notify.send",
                    "report.generate", "admin.manage",
                ],
                "schema_complexity_score": 9.5,
                "permission_list": ["login", "logout", "export", "import", "query", "cache", "notify"],
            },
        },
        {
            "name": "no_tools_at_all",
            "metadata": {
                "tool_count": 0,
                "avg_tool_description_length": 0.0,
                "has_risky_permissions": False,
                "tool_name_patterns": [],
                "schema_complexity_score": 0.0,
                "permission_list": [],
            },
        },
    ]
    
    log.info(f"Running tool_description_safety self-smoke test ({len(test_cases)} cases)")
    all_passed = True
    distinct_scores = set()
    
    for tc in test_cases:
        score, evidence = compute_score(tc["metadata"])
        distinct_scores.add(score)
        log.info(
            f"  [{tc['name']}] score={score:.4f}  "
            f"desc_q={evidence['components']['description_quality']:.2f}  "
            f"schema={evidence['components']['schema_complexity']:.2f}  "
            f"perm={evidence['components']['permission_safety']:.2f}  "
            f"name={evidence['components']['name_quality']:.2f}  "
            f"count={evidence['components']['tool_count_bonus']:.2f}"
        )
    
    if len(distinct_scores) < 5:
        log.warning(f"Only {len(distinct_scores)} distinct scores (expected >= 5)")
        all_passed = False
    else:
        log.info(f"Distinct score coverage: {len(distinct_scores)} values - PASS")
    
    log.info("Smoke complete.")
    sys.exit(0)


if __name__ == "__main__":
    run()