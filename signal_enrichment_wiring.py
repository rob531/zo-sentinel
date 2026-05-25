import os
import sys
import time
import signal
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

sys.path.insert(0, '/home/workspace/zo_sentinel')

SERVICE_NAME = "signal_enrichment_wiring"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
PID_FILE = "/tmp/signal_enrichment_wiring.pid"
LOG_FILE = "/home/workspace/logs/signal_enrichment_wiring.log"
POLL_SECS = 300
BATCH_SIZE = 50

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)

try:
    import community_signal_enrichment as cse_module
    import tool_description_safety_enrichment as tds_module
    import permission_scope_enrichment as pse_module
    import temporal_stability_enrichment as tse_module
    ENRICHMENT_MODULES_AVAILABLE = True
    log.info("All enrichment modules imported successfully")
except ImportError as e:
    ENRICHMENT_MODULES_AVAILABLE = False
    log.warning(f"Enrichment modules import failed: {e}")

ENRICHMENT_SIGNALS = {
    "community_signal": {
        "module": "community_signal_enrichment",
        "signal_name": "community_signal_score",
        "version": "v5"
    },
    "tool_description_safety": {
        "module": "tool_description_safety_enrichment",
        "signal_name": "tool_description_safety_score",
        "version": "v1.0.0"
    },
    "permission_scope": {
        "module": "permission_scope_enrichment",
        "signal_name": "permission_scope_score",
        "version": "4.0.0"
    },
    "temporal_stability": {
        "module": "temporal_stability_enrichment",
        "signal_name": "temporal_stability_score",
        "version": "1.0.0"
    }
}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        import requests
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows},
            timeout=60
        )
        if resp.status_code == 200:
            return True
        log.warning(f"Write failed [{resp.status_code}]: {resp.text[:200]}")
        return False
    except Exception as e:
        log.error(f"Write error: {e}")
        return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        import requests
        resp = requests.post(f"{QUERY_SERVICE_URL}/query", json={"sql": sql}, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('rows', [])
        else:
            log.warning(f"Query failed [{resp.status_code}]: {sql[:100]}")
            return None
    except Exception as e:
        log.error(f"Query error: {e}")
        return None


def ws_execute(sql: str) -> bool:
    try:
        import requests
        resp = requests.post(f"{EXECUTE_SERVICE_URL}/execute", json={"sql": sql}, timeout=60)
        if resp.status_code == 200:
            return True
        log.warning(f"Execute failed [{resp.status_code}]: {sql[:100]}")
        return False
    except Exception as e:
        log.error(f"Execute error: {e}")
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def check_single_instance() -> bool:
    pid = os.getpid()
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log.error(f"Another instance is running (PID {old_pid})")
                return False
            except OSError:
                log.info(f"Stale PID file found, replacing")
        with open(PID_FILE, 'w') as f:
            f.write(str(pid))
        return True
    except Exception as e:
        log.error(f"PID file error: {e}")
        return False


def remove_pid_file() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum: int, frame) -> None:
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def ensure_enrichments_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        server_id VARCHAR,
        enrichment_type VARCHAR,
        score DOUBLE,
        evidence VARCHAR,
        computed_at TIMESTAMPTZ,
        metadata VARCHAR,
        PRIMARY KEY (server_id, enrichment_type)
    )
    """
    return ws_execute(sql)


def get_unscored_servers(signal_type: str) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT r.server_id, r.name, r.description, r.registry_source,
           r.Trust_Score, r.first_seen, r.last_updated,
           r.stars, r.forks, r.contributors,
           e.manifest_tools, e.tool_count, e.manifest_permissions,
           e.ecosystem, e.download_count, e.dependency_count,
           e.publisher_verified, e.age_days
    FROM mcp_server_registry r
    LEFT JOIN mcp_ecosystems_metadata e ON r.server_id = e.server_id
    LEFT JOIN mcp_signal_enrichments enf ON r.server_id = enf.server_id
        AND enf.enrichment_type = '{signal_type}'
    WHERE enf.server_id IS NULL
      AND r.registry_source IS NOT NULL
    LIMIT {BATCH_SIZE}
    """
    return ws_query(sql) or []


def get_all_servers_for_enrichment() -> List[Dict[str, Any]]:
    sql = """
    SELECT r.server_id, r.name, r.description, r.registry_source,
           r.trust_score, r.first_seen, r.last_updated,
           r.stars, r.forks, r.contributors,
           e.manifest_tools, e.tool_count, e.manifest_permissions,
           e.ecosystem, e.download_count, e.dependency_count,
           e.publisher_verified, e.age_days,
           e.update_frequency, e.release_cadence,
           e.maintenance_engagement, e.contributor_diversity,
           e.issue_resolution_time, e.recent_activity_ratio,
           f.fingerprint_hash, f.tool_signatures
    FROM mcp_server_registry r
    LEFT JOIN mcp_ecosystems_metadata e ON r.server_id = e.server_id
    LEFT JOIN mcp_fingerprints f ON r.server_id = f.server_id
    WHERE r.registry_source IS NOT NULL
    LIMIT 200
    """
    return ws_query(sql) or []


def compute_community_signal(metadata: Dict[str, Any]) -> Optional[float]:
    if not ENRICHMENT_MODULES_AVAILABLE:
        return None
    try:
        if hasattr(cse_module, 'compute_score'):
            return cse_module.compute_score(metadata)
        elif hasattr(cse_module, 'score_stars'):
            stars = metadata.get('stars') or metadata.get('stargazers_count') or 0
            forks = metadata.get('forks') or 0
            contributors = metadata.get('contributors') or metadata.get('contributor_count') or 0
            return cse_module.score_stars(stars, 1.0)
    except Exception as e:
        log.debug(f"Community signal compute error: {e}")
    return None


def compute_tool_description_safety(metadata: Dict[str, Any]) -> Optional[float]:
    if not ENRICHMENT_MODULES_AVAILABLE:
        return None
    try:
        if hasattr(tds_module, 'compute_score'):
            return tds_module.compute_score(metadata)
        elif hasattr(tds_module, 'calculate_schema_quality'):
            manifest_tools = metadata.get('manifest_tools')
            if manifest_tools:
                try:
                    tools_json = json.loads(manifest_tools) if isinstance(manifest_tools, str) else manifest_tools
                    return tds_module.calculate_schema_quality(tools_json)
                except:
                    pass
    except Exception as e:
        log.debug(f"Tool description safety compute error: {e}")
    return None


def compute_permission_scope(metadata: Dict[str, Any]) -> Optional[float]:
    if not ENRICHMENT_MODULES_AVAILABLE:
        return None
    try:
        if hasattr(pse_module, 'compute_score'):
            return pse_module.compute_score(metadata)
        elif hasattr(pse_module, 'normalize_permission_name'):
            manifest_perms = metadata.get('manifest_permissions')
            if manifest_perms:
                try:
                    perms_list = json.loads(manifest_perms) if isinstance(manifest_perms, str) else manifest_perms
                    if isinstance(perms_list, list):
                        normalized = [pse_module.normalize_permission_name(p) for p in perms_list]
                        return sum(normalized) / len(normalized) if normalized else 50.0
                except:
                    pass
            return 75.0
    except Exception as e:
        log.debug(f"Permission scope compute error: {e}")
    return None


def compute_temporal_stability(metadata: Dict[str, Any]) -> Optional[float]:
    if not ENRICHMENT_MODULES_AVAILABLE:
        return None
    try:
        if hasattr(tse_module, 'compute_score'):
            return tse_module.compute_score(metadata)
        elif hasattr(tse_module, 'parse_iso_date'):
            first_seen = metadata.get('first_seen')
            last_updated = metadata.get('last_updated')
            age_days = metadata.get('age_days')
            update_frequency = metadata.get('update_frequency')
            release_cadence = metadata.get('release_cadence')
            if age_days:
                try:
                    age_val = float(age_days)
                    return tse_module.score_age_days(age_val)
                except:
                    pass
            return 50.0
    except Exception as e:
        log.debug(f"Temporal stability compute error: {e}")
    return None


def process_enrichment_batch(servers: List[Dict[str, Any]]) -> int:
    if not servers:
        return 0
    
    rows = []
    ts = utc_now_iso()
    
    for server in servers:
        server_id = server.get('server_id')
        if not server_id:
            continue
        
        computed_scores = {}
        
        community_score = compute_community_signal(server)
        if community_score is not None:
            computed_scores['community_signal'] = community_score
        
        tool_safety_score = compute_tool_description_safety(server)
        if tool_safety_score is not None:
            computed_scores['tool_description_safety'] = tool_safety_score
        
        permission_score = compute_permission_scope(server)
        if permission_score is not None:
            computed_scores['permission_scope'] = permission_score
        
        temporal_score = compute_temporal_stability(server)
        if temporal_score is not None:
            computed_scores['temporal_stability'] = temporal_score
        
        for enrichment_type, score in computed_scores.items():
            signal_info = ENRICHMENT_SIGNALS.get(enrichment_type, {})
            evidence = {
                'server_name': server.get('name', ''),
                'registry_source': server.get('registry_source', ''),
                'source_fields': list(server.keys())
            }
            
            rows.append({
                'server_id': server_id,
                'enrichment_type': enrichment_type,
                'score': round(score, 4),
                'evidence': json.dumps(evidence),
                'computed_at': ts,
                'metadata': json.dumps({
                    'version': signal_info.get('version', 'unknown'),
                    'module': signal_info.get('module', enrichment_type)
                })
            })
    
    if rows:
        success = ws_write('mcp_signal_enrichments', rows)
        if success:
            log.info(f"Wrote {len(rows)} enrichment rows for {len(servers)} servers")
            return len(rows)
        else:
            log.warning(f"Failed to write enrichment rows")
    
    return 0


def send_heartbeat() -> None:
    ts = utc_now_iso()
    rows = [{
        'service': SERVICE_NAME,
        'last_heartbeat': ts,
        'status': 'running',
        'meta': json.dumps({
            'enrichment_signals': list(ENRICHMENT_SIGNALS.keys()),
            'modules_available': ENRICHMENT_MODULES_AVAILABLE,
            'batch_size': BATCH_SIZE,
            'poll_seconds': POLL_SECS
        })
    }]
    ws_write('service_health', rows)


def run_cycle() -> int:
    log.info("Starting enrichment cycle")
    
    if not ensure_enrichments_table():
        log.error("Failed to ensure enrichments table exists")
        return 0
    
    servers = get_all_servers_for_enrichment()
    
    if not servers:
        log.info("No servers available for enrichment")
        return 0
    
    total_enriched = 0
    for i in range(0, len(servers), BATCH_SIZE):
        batch = servers[i:i + BATCH_SIZE]
        count = process_enrichment_batch(batch)
        total_enriched += count
        
        if count > 0 and i + BATCH_SIZE < len(servers):
            time.sleep(0.5)
    
    log.info(f"Cycle complete: enriched {total_enriched} signal records")
    return total_enriched


def run() -> None:
    log.info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        log.error("Failed to acquire PID file, exiting")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        ensure_enrichments_table()
        log.info("Enrichments table verified")
    except Exception as e:
        log.error(f"Table initialization failed: {e}")
    
    cycle_count = 0
    while True:
        try:
            run_cycle()
            send_heartbeat()
            cycle_count += 1
            
            if cycle_count % 10 == 0:
                log.info(f"Completed {cycle_count} cycles")
                
        except Exception as e:
            log.error(f"Cycle error: {e}")
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()