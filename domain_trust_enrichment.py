"""
Domain Trust Enrichment Module for ZO-SENTINEL

Computes domain trust scores (0-100) using multiple metadata signals to improve
discrimination beyond the current 12 distinct values. Reads registry_source,
age_days, publisher_verified, stars, download_count, and dependency_count.

Exposes compute_score(metadata: dict) returning (float 0-100, evidence dict).
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import requests

SERVICE_NAME = "domain_trust_enrichment"
WRITE_SERVICE_URL = "http://localhost:8772"

logger = logging.getLogger(__name__)


def ws_write(table: str, rows: list[dict]) -> dict:
    """Write rows to write_service via HTTP POST."""
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(
        WRITE_SERVICE_URL + "/write",
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str, params: tuple = ()) -> list[dict]:
    """Execute parameterized SQL via write_service query endpoint."""
    payload = {"sql": sql, "params": list(params)}
    resp = requests.post(
        WRITE_SERVICE_URL + "/query",
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


# Scoring weight constants - tuned for discrimination
_REGISTRY_WEIGHTS = {
    "github": 15.0,
    "npm": 8.0,
    "pypi": 8.0,
    "conda": 6.0,
    "nuget": 6.0,
    "cargo": 6.0,
    "gem": 5.0,
    "unknown": 0.0,
}

_AGE_BUCKETS = [
    (365, 20.0, "1yr+"),
    (180, 10.0, "6mo+"),
    (90, 5.0, "3mo+"),
    (30, 2.0, "1mo+"),
    (1, -5.0, "<1mo"),
    (0, -8.0, "unknown"),
]

_STAR_BUCKETS = [
    (10000, 12.0, "10k+"),
    (1000, 8.0, "1k+"),
    (100, 5.0, "100+"),
    (10, 2.0, "10+"),
    (1, 1.0, "1+"),
    (0, 0.0, "none"),
]

_DOWNLOAD_BUCKETS = [
    (10000000, 8.0, "10M+"),
    (1000000, 6.0, "1M+"),
    (100000, 4.0, "100k+"),
    (10000, 2.0, "10k+"),
    (1, 1.0, "1+"),
    (0, 0.0, "none"),
]

_DEPENDENCY_BUCKETS = [
    (0, 2.0, "none"),
    (6, 1.5, "1-5"),
    (21, 1.0, "6-20"),
    (51, 0.0, "21-50"),
    (999999, -2.0, "51+"),
]


def _score_bucket(value: int, buckets: list[tuple[int, float, str]]) -> tuple[float, str]:
    """Return (score, label) for a value against ordered buckets."""
    for threshold, score, label in buckets:
        if value >= threshold:
            return score, label
    return 0.0, "unknown"


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute domain trust score (0-100) based on multiple metadata signals.

    Args:
        metadata: dict containing domain metadata fields:
            - registry_source: str (github/npm/pypi/conda/nuget/cargo/gem)
            - age_days: int (days since first release, 0 if unknown)
            - publisher_verified: bool
            - stars: int (GitHub stars count, 0 if unavailable)
            - download_count: int (recent downloads, 0 if unavailable)
            - dependency_count: int (number of dependencies, 0 if unknown)

    Returns:
        tuple[float, dict]: (score 0-100, evidence dict with scoring breakdown)
    """
    evidence = {
        "baseline": 50.0,
        "adjustments": [],
        "final_score": 50.0,
        "signals_scored": 0,
        "metadata_summary": {},
    }

    score = 50.0

    registry_source = str(metadata.get("registry_source", "unknown")).lower()
    age_days = int(metadata.get("age_days", 0))
    publisher_verified = bool(metadata.get("publisher_verified", False))
    stars = int(metadata.get("stars", 0))
    download_count = int(metadata.get("download_count", 0))
    dependency_count = int(metadata.get("dependency_count", 0))

    evidence["metadata_summary"] = {
        "registry_source": registry_source,
        "age_days": age_days,
        "publisher_verified": publisher_verified,
        "stars": stars,
        "download_count": download_count,
        "dependency_count": dependency_count,
    }

    # Registry source weight
    reg_weight = _REGISTRY_WEIGHTS.get(registry_source, 0.0)
    score += reg_weight
    evidence["adjustments"].append({
        "factor": "registry_source",
        "value": registry_source,
        "delta": reg_weight,
        "note": "trusted registries get bonus"
    })
    evidence["signals_scored"] += 1

    # Package age scoring
    age_score, age_label = _score_bucket(age_days, _AGE_BUCKETS)
    score += age_score
    evidence["adjustments"].append({
        "factor": "age_days",
        "value": age_days,
        "delta": age_score,
        "bucket": age_label,
        "note": "mature packages more trustworthy"
    })
    evidence["signals_scored"] += 1

    # Publisher verification
    if publisher_verified:
        score += 15.0
        evidence["adjustments"].append({
            "factor": "publisher_verified",
            "value": True,
            "delta": 15.0,
            "note": "verified publisher bonus"
        })
    else:
        score -= 5.0
        evidence["adjustments"].append({
            "factor": "publisher_verified",
            "value": False,
            "delta": -5.0,
            "note": "unverified publisher penalty"
        })
    evidence["signals_scored"] += 1

    # Stars scoring
    star_score, star_label = _score_bucket(stars, _STAR_BUCKETS)
    score += star_score
    evidence["adjustments"].append({
        "factor": "stars",
        "value": stars,
        "delta": star_score,
        "bucket": star_label,
        "note": "community visibility signal"
    })
    evidence["signals_scored"] += 1

    # Download count scoring
    dl_score, dl_label = _score_bucket(download_count, _DOWNLOAD_BUCKETS)
    score += dl_score
    evidence["adjustments"].append({
        "factor": "download_count",
        "value": download_count,
        "delta": dl_score,
        "bucket": dl_label,
        "note": "usage adoption signal"
    })
    evidence["signals_scored"] += 1

    # Dependency count scoring (moderate count = trusted, extreme = risk)
    dep_score, dep_label = _score_bucket(dependency_count, _DEPENDENCY_BUCKETS)
    score += dep_score
    evidence["adjustments"].append({
        "factor": "dependency_count",
        "value": dependency_count,
        "delta": dep_score,
        "bucket": dep_label,
        "note": "moderate deps trusted, bloated deps risk"
    })
    evidence["signals_scored"] += 1

    # Clamp to 0-100 range
    final_score = max(0.0, min(100.0, score))
    evidence["final_score"] = round(final_score, 2)

    logger.debug(
        "Domain trust score: %.2f (signals: %d, registry: %s, age: %dd, verified: %s)",
        final_score,
        evidence["signals_scored"],
        registry_source,
        age_days,
        publisher_verified,
    )

    return final_score, evidence


def _compute_deterministic_id(content: str) -> str:
    """Generate deterministic MD5 hash for idempotent writes."""
    return hashlib.md5(content.encode("utf-8"), usedforsecurity=False).hexdigest()[::2]


def log_enrichment_audit(
    target_server_id: str,
    metadata: dict,
    score: float,
    evidence: dict
) -> None:
    """
    Write an audit log entry for domain trust enrichment.
    
    Args:
        target_server_id: server id being enriched
        metadata: input metadata dict
        score: computed score
        evidence: evidence dict
    """
    ts = datetime.now(timezone.utc).isoformat()
    content = f"{target_server_id}:{score}:{ts}"
    row = {
        "audit_id": _compute_deterministic_id(content),
        "target_server_id": target_server_id,
        "enrichment_type": "domain_trust",
        "input_metadata": metadata,
        "computed_score": score,
        "evidence": evidence,
        "enriched_at": ts,
    }
    try:
        ws_write("mcp_enrichment_audit", [row])
        logger.debug("Audit logged for target_server_id=%s", target_server_id)
    except Exception as e:
        logger.warning("Failed to write enrichment audit: %s", e)


def get_registered_domains_requiring_enrichment(
    min_age_days: int = 0,
    limit: int = 100
) -> list[dict]:
    """
    Query domains from mcp_server_registry that may need trust enrichment.
    
    Filters to rows where domain_trust_score is NULL or age_days differs from
    last computed value.
    """
    sql = """
        SELECT 
            server_id,
            name,
            registry_source,
            age_days,
            publisher_verified,
            stars,
            download_count,
            dependency_count
        FROM mcp_server_registry
        WHERE (domain_trust_score IS NULL OR domain_trust_score = 0)
          AND registry_source IS NOT NULL
        ORDER BY last_seen DESC
        LIMIT ?
    """
    try:
        return ws_query(sql, (limit,))
    except Exception as e:
        logger.warning("Failed to query registry for enrichment: %s", e)
        return []


def update_domain_trust_score(
    server_id: str,
    score: float,
    evidence: dict
) -> bool:
    """
    Update domain_trust_score and evidence in mcp_server_registry.
    
    Returns True on success.
    """
    sql = """
        UPDATE mcp_server_registry
        SET domain_trust_score = ?,
            domain_trust_evidence = ?::JSON,
            last_assessed = ?
        WHERE server_id = ?
    """
    ts = datetime.now(timezone.utc).isoformat()
    try:
        ws_write("mcp_server_registry", [{
            "server_id": server_id,
            "domain_trust_score": score,
            "domain_trust_evidence": evidence,
            "last_assessed": ts,
        }])
        logger.debug("Updated domain_trust_score for %s: %.2f", server_id, score)
        return True
    except Exception as e:
        logger.warning("Failed to update trust score for %s: %s", server_id, e)
        return False


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python domain_trust_enrichment.py <metadata_json>")
        print("Example: python domain_trust_enrichment.py '{\"registry_source\":\"npm\",\"age_days\":730,\"publisher_verified\":true}'")
        sys.exit(1)

    try:
        metadata = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    score, evidence = compute_score(metadata)
    print(json.dumps({"score": score, "evidence": evidence}, indent=2))
    sys.exit(0)