"""Family concentration scoring consumer service."""

import math
import re
from urllib.parse import urlparse

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy import select


def extract_family_id(server_name: str) -> str:
    """Extract family_id from server name (URL host or org prefix)."""
    if not server_name:
        return "unknown"
    
    if "://" in server_name:
        try:
            parsed = urlparse(server_name)
            host = parsed.netloc or parsed.path
            parts = host.split(".")
            if len(parts) >= 2:
                return parts[0]
            return host
        except Exception:
            pass
    
    match = re.match(r'^([a-zA-Z0-9_-]+)', server_name)
    if match:
        return match.group(1)
    
    return server_name.split("_")[0].split("-")[0]


def family_concentration_score(rows: list[dict]) -> float:
    """Calculate concentration score (0-100) from family risk_tier counts."""
    if not rows:
        return 0.0
    
    family_counts: dict[str, int] = {}
    for row in rows:
        family_id = row.get("family_id", "unknown")
        family_counts[family_id] = family_counts.get(family_id, 0) + 1
    
    if len(family_counts) < 2:
        return 0.0
    
    counts = list(family_counts.values())
    mean = sum(counts) / len(counts)
    
    variance = sum((c - mean) ** 2 for c in counts) / len(counts)
    std_dev = math.sqrt(variance)
    
    if mean == 0:
        return 0.0
    
    normalized = (std_dev / mean) * 50
    return min(100.0, max(0.0, normalized))


def get_scoring_data() -> list[dict]:
    """Fetch data from joined McpLlmAxisScore and McpServerRegistry."""
    with get_session() as session:
        stmt = select(
            McpLlmAxisScore.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier
        ).join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id
        )
        results = session.execute(stmt).fetchall()
        return [dict(row._mapping) for row in results]


def run() -> dict:
    """Compute family concentration score and return results."""
    rows = get_scoring_data()
    
    for row in rows:
        row["family_id"] = extract_family_id(row.get("name", ""))
    
    score = family_concentration_score(rows)
    
    family_counts: dict[str, int] = {}
    for row in rows:
        fid = row.get("family_id", "unknown")
        family_counts[fid] = family_counts.get(fid, 0) + 1
    
    counts = list(family_counts.values())
    mean = sum(counts) / len(counts) if counts else 0
    variance = sum((c - mean) ** 2 for c in counts) / len(counts) if counts else 0
    std_dev = math.sqrt(variance)
    concentration_ratio = std_dev / mean if mean > 0 else 0
    
    result = {
        "score": score,
        "family_counts": family_counts,
        "concentration_ratio": concentration_ratio
    }
    
    return result


if __name__ == "__main__":
    seed_data = [
        {"server_id": "s1", "name": "ai-provider", "family_id": "ai", "risk_tier": "high"},
        {"server_id": "s2", "name": "ai-gateway", "family_id": "ai", "risk_tier": "high"},
        {"server_id": "s3", "name": "ai-service", "family_id": "ai", "risk_tier": "medium"},
        {"server_id": "s4", "name": "ai-platform", "family_id": "ai", "risk_tier": "low"},
        {"server_id": "s5", "name": "cloud-vendor", "family_id": "cloud", "risk_tier": "high"},
        {"server_id": "s6", "name": "cloud-provider", "family_id": "cloud", "risk_tier": "medium"},
        {"server_id": "s7", "name": "cloud-service", "family_id": "cloud", "risk_tier": "medium"},
        {"server_id": "s8", "name": "dev-tool", "family_id": "dev", "risk_tier": "low"},
        {"server_id": "s9", "name": "dev-ide", "family_id": "dev", "risk_tier": "low"},
    ]
    
    score = family_concentration_score(seed_data)
    
    family_counts: dict[str, int] = {}
    for row in seed_data:
        fid = row.get("family_id", "unknown")
        family_counts[fid] = family_counts.get(fid, 0) + 1
    
    counts = list(family_counts.values())
    mean = sum(counts) / len(counts)
    variance = sum((c - mean) ** 2 for c in counts) / len(counts)
    std_dev = math.sqrt(variance)
    concentration_ratio = std_dev / mean if mean > 0 else 0
    
    assert score is not None and score > 0, f"Expected non-zero score, got {score}"
    print(f"concentration_score={score}, family_counts={family_counts}, concentration_ratio={concentration_ratio}")
    print("PASS")