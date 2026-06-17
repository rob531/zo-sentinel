import logging
import hashlib
from datetime import datetime, timezone

import requests

SERVICE_NAME = "populate_mcp_signal_enrichments_first_batch"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772"
LOG_PATH = f"/home/workspace/logs/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

BATCH_SIZE = 500
ENRICHMENT_VERSION = "v1"


def ws_query(sql: str) -> list:
    payload = {"sql": sql}
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: list) -> None:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=60)
    resp.raise_for_status()


def compute_deterministic_id(server_id: str, signal_name: str) -> str:
    content = f"{server_id}:{signal_name}:{ENRICHMENT_VERSION}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + (2.718281828 ** (-x)))


def score_domain_trust(server: dict) -> float:
    source = server.get("registry_source", "unknown")
    trust_map = {
        "npm": 0.90, "github": 0.85, "smithery": 0.70,
        "openmcp": 0.65, "mcp_guild": 0.60, "unknown": 0.30
    }
    base = trust_map.get(source, 0.30)
    age_days = 0
    created = server.get("created_at") or server.get("first_seen") or ""
    if created:
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created_dt).days
        except Exception:
            age_days = 0
    age_score = min(age_days / 365.0, 1.0) * 0.10
    return min(base + age_score, 1.0)


def score_community_signal(server: dict) -> float:
    stars = float(server.get("stars", 0) or 0)
    downloads = float(server.get("download_count", 0) or 0)
    version_count = float(server.get("version_count", 0) or 0)
    stars_score = min(stars / 10000.0, 1.0) * 0.50
    dl_score = min(downloads / 1000000.0, 1.0) * 0.30
    ver_score = min(version_count / 50.0, 1.0) * 0.20
    raw = stars_score + dl_score + ver_score
    return sigmoid(raw * 8.0)


def score_temporal_stability(server: dict) -> float:
    created = server.get("created_at") or server.get("first_seen") or ""
    updated = server.get("last_updated") or server.get("last_seen") or ""
    if not created:
        return 0.3
    try:
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else datetime.now(timezone.utc)
        age_days = max((updated_dt - created_dt).days, 1)
        update_freq = age_days / max((datetime.now(timezone.utc) - created_dt).days, 1)
        stability = min(age_days / 180.0, 1.0) * 0.50 + min(update_freq, 1.0) * 0.50
        return sigmoid(stability * 6.0)
    except Exception:
        return 0.5


def score_permission_scope(server: dict) -> float:
    permissions_raw = server.get("permissions") or server.get("permission_list") or []
    if isinstance(permissions_raw, str):
        try:
            import json
            permissions_raw = json.loads(permissions_raw)
        except Exception:
            permissions_raw = [p.strip() for p in permissions_raw.split(",")]
    if not permissions_raw:
        return 0.70
    dangerous = {"admin", "root", "sudo", "write_all", "delete", "execute_root", "system", "full_access"}
    safe = {"read", "read_only", "list", "query", "search"}
    perm_set = {p.lower() for p in permissions_raw}
    if perm_set & dangerous:
        return 0.20
    if perm_set & safe and not (perm_set & dangerous):
        return 0.85
    return 0.55


def score_tool_description_safety(server: dict) -> float:
    description = server.get("description", "") or ""
    name = server.get("name", "") or ""
    if not description or len(description) < 10:
        return 0.40
    injection_patterns = ["ignore previous", "disregard", "new instruction", "system prompt", "{INJECTION"]
    has_injection = any(pat.lower() in description.lower() for pat in injection_patterns)
    if has_injection:
        return 0.15
    credential_patterns = ["api_key", "secret", "password", "token=", "bearer"]
    has_creds = any(pat in description.lower() for pat in credential_patterns)
    if has_creds:
        return 0.35
    length_score = min(len(description) / 200.0, 1.0) * 0.40
    name_score = 0.30 if len(name) > 3 else 0.10
    return min(length_score + name_score + 0.30, 1.0)


def score_context_efficiency(server: dict) -> float:
    description = server.get("description", "") or ""
    tool_count = int(server.get("tool_count", 0) or 0)
    if tool_count == 0:
        return 0.50
    desc_len = len(description) if description else 0
    efficiency = desc_len / max(tool_count, 1)
    efficiency_score = min(efficiency / 50.0, 1.0)
    return sigmoid(efficiency_score * 10.0)


def score_supply_chain(server: dict) -> float:
    source = server.get("registry_source", "unknown")
    verified_publisher = server.get("verified_publisher", False)
    dependency_count = int(server.get("dependency_count", 0) or 0)
    base_score = 0.50
    if source in ("npm", "github"):
        base_score += 0.25
    if verified_publisher:
        base_score += 0.15
    dep_penalty = min(dependency_count / 20.0, 1.0) * 0.10
    return max(base_score - dep_penalty, 0.05)


def score_injection_resilience(server: dict) -> float:
    description = server.get("description", "") or ""
    name = server.get("name", "") or ""
    base = 0.60
    injection_keywords = ["ignore", "forget", "new rules", "override", "{SYSTEM}", "[SYSTEM]"]
    if any(kw.lower() in description.lower() for kw in injection_keywords):
        base -= 0.30
    if name and len(name) > 5:
        base += 0.10
    return max(min(base, 1.0), 0.05)


SIGNAL_SCORES = {
    "domain_trust": score_domain_trust,
    "community_signal": score_community_signal,
    "temporal_stability": score_temporal_stability,
    "permission_scope": score_permission_scope,
    "tool_description_safety": score_tool_description_safety,
    "context_efficiency": score_context_efficiency,
    "supply_chain": score_supply_chain,
    "injection_resilience": score_injection_resilience,
}


def ensure_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        enrichment_id VARCHAR,
        server_id VARCHAR NOT NULL,
        signal_type VARCHAR NOT NULL,
        score DOUBLE NOT NULL,
        metadata VARCHAR,
        evidence VARCHAR,
        computed_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (enrichment_id)
    )
    """
    payload = {"sql": sql}
    resp = requests.post(WRITE_SERVICE_URL + "/execute", json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        log.warning("Table creation returned %s: %s", resp.status_code, resp.text)


def get_servers_needing_enrichment(offset: int, limit: int) -> list:
    sql = f"""
    SELECT 
        r.server_id,
        r.name,
        r.description,
        r.registry_source,
        r.created_at,
        r.first_seen,
        r.last_seen,
        r.stars,
        r.download_count,
        r.version_count,
        r.permissions,
        r.permission_list,
        r.tool_count,
        r.verified_publisher,
        r.dependency_count,
        r.last_updated
    FROM mcp_server_registry r
    WHERE r.server_id IS NOT NULL
      AND r.server_id != ''
    LIMIT {limit}
    OFFSET {offset}
    """
    return ws_query(sql)


def get_existing_enrichment_ids(signal_types: list) -> set:
    types_str = ", ".join(f"'{t}'" for t in signal_types)
    sql = f"SELECT enrichment_id FROM mcp_signal_enrichments WHERE signal_type IN ({types_str})"
    rows = ws_query(sql)
    return {r["enrichment_id"] for r in rows if r.get("enrichment_id")}


def populate_batch(batch: list, signal_types: list) -> int:
    if not batch:
        return 0
    existing_ids = get_existing_enrichment_ids(signal_types)
    now = datetime.now(timezone.utc).isoformat() + "Z"
    rows_to_write = []
    for server in batch:
        server_id = server.get("server_id", "")
        if not server_id:
            continue
        for signal_name, score_fn in SIGNAL_SCORES.items():
            enrichment_id = compute_deterministic_id(server_id, signal_name)
            if enrichment_id in existing_ids:
                continue
            score = score_fn(server)
            score = max(0.0, min(1.0, score))
            rows_to_write.append({
                "enrichment_id": enrichment_id,
                "server_id": server_id,
                "signal_type": signal_name,
                "score": round(score, 6),
                "metadata": "{}",
                "evidence": "{}",
                "computed_at": now,
            })
    if rows_to_write:
        ws_write("mcp_signal_enrichments", rows_to_write)
        log.info("Wrote %d enrichment rows", len(rows_to_write))
    return len(rows_to_write)


def run() -> None:
    log.info("Starting %s", SERVICE_NAME)
    ensure_table()
    signal_types = list(SIGNAL_SCORES.keys())
    total_processed = 0
    total_offset = 0
    cycle = 0
    while True:
        cycle += 1
        batch = get_servers_needing_enrichment(total_offset, BATCH_SIZE)
        if not batch:
            log.info("No more servers to process at offset %d. Total written: %d", total_offset, total_processed)
            break
        written = populate_batch(batch, signal_types)
        total_processed += written
        total_offset += len(batch)
        log.info("Cycle %d: processed %d servers, offset now %d, total rows written %d",
                 cycle, len(batch), total_offset, total_processed)
        if len(batch) < BATCH_SIZE:
            break
    log.info("Completed. Total enrichment rows written: %d", total_processed)
    now = datetime.now(timezone.utc).isoformat() + "Z"
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": now,
            "status": "completed",
            "ts": now,
            "meta": f'{{"rows_written": {total_processed}}}',
        }])
    except Exception as e:
        log.warning("Failed to write completion heartbeat: %s", e)


if __name__ == "__main__":
    run()