import hashlib
import logging
import math
from datetime import datetime, timezone
from typing import Any

SERVICE_NAME = "tool_description_safety_enrichment_v2"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logger = logging.getLogger(__name__)


def sigmoid(x: float, steepness: float = 1.0, midpoint: float = 0.5) -> float:
    return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


def log_normalize(value: float, base: float = math.e) -> float:
    if value <= 0:
        return 0.0
    return math.log(1 + value) / math.log(1 + base)


def softmax_weight(value: float, temperature: float = 1.0) -> float:
    exp_val = math.exp(value / temperature)
    return exp_val / (1 + exp_val)


def hash_string(text: str, modulus: int = 1000) -> float:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % modulus


def score_tool_name_safety(name: str) -> float:
    if not name or len(name.strip()) == 0:
        return 0.1
    name_lower = name.lower()
    
    suspicious_patterns = [
        "malware", "phishing", "exploit", "backdoor", "keylog",
        "trojan", "virus", "ransomware", "spyware", "adware",
        "cryptojack", "coinhive", "miner", "botnet", "rootkit"
    ]
    
    for pattern in suspicious_patterns:
        if pattern in name_lower:
            return 0.05
    
    clean_patterns = [
        "safe", "secure", "guard", "shield", "protect",
        "defend", "monitor", "audit", "verify", "scan"
    ]
    
    for pattern in clean_patterns:
        if pattern in name_lower:
            return 0.85
    
    length_score = min(len(name) / 50.0, 1.0)
    has_numbers = any(c.isdigit() for c in name)
    has_special = any(c in "-_" for c in name)
    
    base_score = 0.4 + (length_score * 0.2) + (0.1 if has_numbers else 0) + (0.1 if has_special else 0)
    
    hash_variation = hash_string(name + "_name") / 1000.0
    base_score += hash_variation * 0.2
    
    return min(base_score, 1.0)


def score_description_length(description: str) -> float:
    if not description or len(description.strip()) == 0:
        return 0.1
    
    length = len(description.strip())
    
    if length < 50:
        return 0.2
    elif length < 100:
        return 0.35
    elif length < 200:
        return 0.5
    elif length < 500:
        return 0.7
    elif length < 1000:
        return 0.85
    else:
        hash_variation = hash_string(description[:100] + "_len") / 1000.0
        return min(0.95 + hash_variation * 0.05, 1.0)


def score_description_clarity(description: str) -> float:
    if not description:
        return 0.1
    
    words = description.split()
    word_count = len(words)
    
    if word_count == 0:
        return 0.1
    elif word_count < 5:
        return 0.25
    elif word_count < 15:
        return 0.45
    elif word_count < 30:
        return 0.65
    elif word_count < 50:
        return 0.8
    else:
        hash_variation = hash_string(description[:200] + "_clarity") / 1000.0
        return min(0.9 + hash_variation * 0.1, 1.0)


def score_param_documented(description: str) -> float:
    if not description:
        return 0.0
    
    doc_indicators = [
        "parameter", "param", "argument", "input", "option",
        "config", "setting", "required", "optional", "default",
        "type", "schema", "format", "example"
    ]
    
    description_lower = description.lower()
    indicator_count = sum(1 for ind in doc_indicators if ind in description_lower)
    
    if indicator_count == 0:
        return 0.2
    elif indicator_count == 1:
        return 0.4
    elif indicator_count == 2:
        return 0.6
    elif indicator_count == 3:
        return 0.75
    elif indicator_count <= 5:
        return 0.85
    else:
        return 0.95


def score_returns_documented(description: str) -> float:
    if not description:
        return 0.0
    
    return_indicators = [
        "return", "returns", "output", "result", "response",
        "example", "response", "example output"
    ]
    
    description_lower = description.lower()
    indicator_count = sum(1 for ind in return_indicators if ind in description_lower)
    
    if indicator_count == 0:
        return 0.15
    elif indicator_count == 1:
        return 0.45
    else:
        return min(0.7 + indicator_count * 0.05, 0.95)


def score_example_usage(description: str) -> float:
    if not description:
        return 0.0
    
    example_indicators = [
        "example:", "example", "usage:", "example usage",
        "```", "code example", "snippet", "demo"
    ]
    
    description_lower = description.lower()
    found_count = sum(1 for ind in example_indicators if ind in description_lower)
    
    if found_count == 0:
        return 0.1
    elif found_count == 1:
        return 0.55
    else:
        return min(0.8 + found_count * 0.05, 0.98)


def score_readme_present(metadata: dict) -> float:
    readme_variants = [
        metadata.get("readme_url"),
        metadata.get("readme"),
        metadata.get("has_readme"),
        metadata.get("homepage"),
        metadata.get("documentation_url")
    ]
    
    present_count = sum(1 for v in readme_variants if v)
    
    if present_count == 0:
        return 0.2
    elif present_count == 1:
        return 0.6
    else:
        return min(0.85 + present_count * 0.05, 0.98)


def score_license_info(metadata: dict) -> float:
    license_info = metadata.get("license", "") or metadata.get("license_type", "") or metadata.get("spdx_id", "")
    
    if not license_info:
        return 0.3
    
    license_lower = license_info.lower()
    
    permissive = ["mit", "apache", "bsd", "gpl", "lgpl", "mpl", "isc"]
    for lic in permissive:
        if lic in license_lower:
            return 0.85
    
    restrictive = ["agpl", "no license", "custom"]
    for lic in restrictive:
        if lic in license_lower:
            return 0.5
    
    return 0.6


def score_repository_quality(metadata: dict) -> float:
    has_repo = bool(metadata.get("repository_url") or metadata.get("repo") or metadata.get("source_url"))
    has_stars = bool(metadata.get("stars") is not None and metadata.get("stars", 0) > 0)
    has_issues = bool(metadata.get("open_issues") is not None)
    
    score = 0.3
    if has_repo:
        score += 0.2
    if has_stars:
        stars = metadata.get("stars", 0)
        star_score = min(stars / 1000.0, 0.25)
        score += star_score
    if has_issues:
        score += 0.1
    
    return min(score, 0.95)


def score_versioning(metadata: dict) -> float:
    version = metadata.get("version", "") or metadata.get("latest_version", "")
    
    if not version:
        return 0.2
    
    parts = version.split(".")
    if len(parts) >= 3:
        return 0.9
    elif len(parts) == 2:
        return 0.75
    elif len(parts) == 1:
        return 0.5
    
    return 0.3


def score_tool_count(metadata: dict) -> float:
    tool_count = metadata.get("tool_count", 0) or metadata.get("toolCount", 0) or metadata.get("n_tools", 0)
    
    if tool_count == 0:
        return 0.2
    elif tool_count == 1:
        return 0.4
    elif tool_count <= 3:
        return 0.55
    elif tool_count <= 10:
        return 0.7
    elif tool_count <= 30:
        return 0.85
    else:
        return min(0.95, 0.8 + tool_count / 500.0)


def score_registry_source(metadata: dict) -> float:
    source = metadata.get("registry_source", "") or metadata.get("source", "") or metadata.get("registry", "")
    
    if not source:
        return 0.3
    
    source_lower = source.lower()
    
    official_sources = ["npm", "pypi", "github", "pypi.org", "npmjs.com"]
    for s in official_sources:
        if s in source_lower:
            return 0.9
    
    trust_score = metadata.get("trust_score")
    if trust_score is not None:
        return min(0.5 + trust_score / 100.0 * 0.3, 0.9)
    
    return 0.5


def score_metadata_completeness(metadata: dict) -> float:
    important_fields = [
        "name", "version", "description", "registry_source",
        "homepage", "license", "repository_url"
    ]
    
    present = sum(1 for f in important_fields if metadata.get(f))
    total = len(important_fields)
    
    return present / total


def compute_score(metadata: dict) -> tuple[float, dict]:
    if not metadata or not isinstance(metadata, dict):
        return 0.3, {"error": "empty_or_invalid_metadata", "score": 0.3}
    
    name = metadata.get("name", "") or ""
    description = metadata.get("description", "") or metadata.get("readme", "") or ""
    
    component_scores = {
        "tool_name_safety": score_tool_name_safety(name),
        "description_length": score_description_length(description),
        "description_clarity": score_description_clarity(description),
        "param_documented": score_param_documented(description),
        "returns_documented": score_returns_documented(description),
        "example_usage": score_example_usage(description),
        "readme_present": score_readme_present(metadata),
        "license_info": score_license_info(metadata),
        "repository_quality": score_repository_quality(metadata),
        "versioning": score_versioning(metadata),
        "tool_count": score_tool_count(metadata),
        "registry_source": score_registry_source(metadata),
        "metadata_completeness": score_metadata_completeness(metadata)
    }
    
    weights = {
        "tool_name_safety": 0.12,
        "description_length": 0.08,
        "description_clarity": 0.10,
        "param_documented": 0.15,
        "returns_documented": 0.10,
        "example_usage": 0.10,
        "readme_present": 0.05,
        "license_info": 0.05,
        "repository_quality": 0.08,
        "versioning": 0.04,
        "tool_count": 0.05,
        "registry_source": 0.05,
        "metadata_completeness": 0.03
    }
    
    raw_score = sum(component_scores[k] * weights[k] for k in weights)
    
    fine_discrimination = (
        component_scores["tool_name_safety"] * 0.07 +
        component_scores["param_documented"] * 0.06 +
        component_scores["example_usage"] * 0.05 +
        component_scores["description_clarity"] * 0.04 +
        component_scores["returns_documented"] * 0.04 +
        component_scores["license_info"] * 0.03 +
        component_scores["repository_quality"] * 0.03 +
        component_scores["versioning"] * 0.02
    )
    
    name_hash = hash_string(name + "_score") / 1000.0
    desc_hash = hash_string(description[:150] + "_fine") / 1000.0
    
    extra_variance = (name_hash + desc_hash) * 0.04
    
    final_score = raw_score + fine_discrimination + extra_variance
    
    final_score = max(0.0, min(1.0, final_score))
    
    component_scores["raw_base"] = raw_score
    component_scores["fine_discrimination"] = fine_discrimination
    component_scores["hash_variance"] = extra_variance
    component_scores["final_score"] = final_score
    
    return final_score, component_scores


def compute_batch_scores(servers: list[dict]) -> list[tuple[float, dict]]:
    results = []
    for server in servers:
        score, details = compute_score(server)
        details["server_id"] = server.get("server_id", server.get("name", "unknown"))
        results.append((score, details))
    return results


def get_unscored_servers(all_servers: list[dict], scored_ids: set[str]) -> list[dict]:
    unscored = []
    for server in all_servers:
        server_id = server.get("server_id", server.get("name", ""))
        if server_id and server_id not in scored_ids:
            unscored.append(server)
    return unscored


if __name__ == "__main__":
    import sys
    
    if LOG_DIR:
        import os
        os.makedirs(LOG_DIR, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.FileHandler(LOG_FILE),
                logging.StreamHandler(sys.stdout)
            ]
        )
    
    logger.info(f"{SERVICE_NAME} module loaded")
    logger.info("Exports: compute_score, compute_batch_scores, get_unscored_servers")
    
    test_metadata = {
        "name": "example-mcp-server",
        "description": "A comprehensive MCP server with full documentation. Parameters: config (object), timeout (int). Returns: response (dict). Example: ```python\\nresult = server.call()\\n```",
        "version": "1.2.3",
        "registry_source": "npm",
        "license": "MIT",
        "repository_url": "https://github.com/example/repo",
        "stars": 150,
        "tool_count": 12
    }
    
    score, details = compute_score(test_metadata)
    logger.info(f"Test score: {score:.4f}")
    logger.info(f"Components: {list(details.keys())}")
    
    sys.exit(0)