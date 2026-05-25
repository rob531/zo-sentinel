import logging
from datetime import datetime
from typing import Dict, Tuple, List, Any
import math

LOG = logging.getLogger(__name__)

SERVICE_NAME = "temporal_stability_enrichment_v4"
SIGNAL_NAME = "temporal_stability"
VERSION = "v4"
MAX_SCORE = 100.0


def parse_iso_date(date_str: str) -> datetime:
    """Parse ISO date string to datetime."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except Exception:
        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except Exception:
            return None


def compute_days_between(d1: str, d2: str) -> float:
    """Compute days between two ISO date strings."""
    dt1 = parse_iso_date(d1)
    dt2 = parse_iso_date(d2)
    if not dt1 or not dt2:
        return 0.0
    delta = abs((dt1 - dt2).total_seconds())
    return delta / 86400.0


def sigmoid(x: float, steepness: float = 0.05, midpoint: float = 180.0) -> float:
    """Sigmoid function for smooth scoring transitions."""
    try:
        return 1.0 / (1.0 + math.pow(math.e, -steepness * (x - midpoint)))
    except Exception:
        return 0.5


def softmax_weight(values: List[float]) -> List[float]:
    """Compute softmax weights for a list of values."""
    if not values:
        return []
    max_val = max(values)
    exp_vals = [math.exp(v - max_val) for v in values]
    sum_exp = sum(exp_vals)
    if sum_exp == 0:
        return [1.0 / len(values)] * len(values)
    return [e / sum_exp for e in exp_vals]


def score_age_days(age_days: int) -> float:
    """
    Score based on package age in days.
    Older established packages get moderate scores, new packages get lower.
    """
    if not age_days or age_days <= 0:
        return 15.0
    
    if age_days < 7:
        return 20.0
    elif age_days < 30:
        return 30.0 + (age_days - 7) * 0.5
    elif age_days < 90:
        return 45.0 + (age_days - 30) * 0.3
    elif age_days < 180:
        return 65.0 + (age_days - 90) * 0.2
    elif age_days < 365:
        return 80.0 + (age_days - 180) * 0.1
    elif age_days < 730:
        return 95.0 + min(5.0, (age_days - 365) * 0.027)
    else:
        return 100.0


def score_recency(last_updated: str, now_iso: str = None) -> float:
    """
    Score based on recency of last update.
    Recently updated packages score higher.
    """
    if not last_updated:
        return 10.0
    
    if not now_iso:
        now_iso = datetime.utcnow().isoformat() + 'Z'
    
    days_since = compute_days_between(last_updated, now_iso)
    
    if days_since <= 0:
        return 100.0
    elif days_since < 7:
        return 95.0 + min(5.0, (7 - days_since) * 0.7)
    elif days_since < 14:
        return 85.0 + (14 - days_since) * 1.4
    elif days_since < 30:
        return 70.0 + (30 - days_since) * 0.75
    elif days_since < 60:
        return 50.0 + (60 - days_since) * 0.67
    elif days_since < 90:
        return 30.0 + (90 - days_since) * 0.67
    elif days_since < 180:
        return 15.0 + (180 - days_since) * 0.25
    elif days_since < 365:
        return 5.0 + min(10.0, (365 - days_since) * 0.055)
    else:
        return 5.0


def score_first_seen(first_seen: str, now_iso: str = None) -> float:
    """
    Score based on when we first discovered this package.
    Long observation history is positive signal.
    """
    if not first_seen:
        return 25.0
    
    if not now_iso:
        now_iso = datetime.utcnow().isoformat() + 'Z'
    
    days_observed = compute_days_between(first_seen, now_iso)
    
    if days_observed < 1:
        return 20.0
    elif days_observed < 7:
        return 35.0 + days_observed * 2.0
    elif days_observed < 30:
        return 50.0 + (days_observed - 7) * 1.3
    elif days_observed < 90:
        return 80.0 + min(10.0, (days_observed - 30) * 0.33)
    else:
        return 90.0 + min(10.0, (days_observed - 90) * 0.05)


def score_commit_frequency(commit_frequency: Any) -> float:
    """
    Score based on commit frequency.
    Accepts numeric (commits per week) or string categories.
    """
    if commit_frequency is None:
        return 30.0
    
    if isinstance(commit_frequency, (int, float)):
        cf = float(commit_frequency)
        if cf <= 0:
            return 10.0
        elif cf < 1:
            return 25.0 + cf * 10.0
        elif cf < 3:
            return 40.0 + (cf - 1) * 15.0
        elif cf < 7:
            return 70.0 + (cf - 3) * 8.0
        elif cf < 14:
            return 95.0 + min(5.0, (cf - 7) * 0.5)
        else:
            return 100.0
    
    cf_str = str(commit_frequency).lower()
    if 'none' in cf_str or 'inactive' in cf_str:
        return 10.0
    elif 'low' in cf_str:
        return 30.0
    elif 'moderate' in cf_str or 'medium' in cf_str:
        return 55.0
    elif 'high' in cf_str:
        return 80.0
    elif 'very high' in cf_str or 'active' in cf_str:
        return 95.0
    else:
        return 40.0


def score_release_regularity(release_regularity: Any) -> float:
    """
    Score based on release regularity.
    Consistent release schedules indicate good maintenance.
    """
    if release_regularity is None:
        return 35.0
    
    if isinstance(release_regularity, (int, float)):
        rr = float(release_regularity)
        if rr <= 0:
            return 10.0
        elif rr < 0.25:
            return 20.0 + rr * 40.0
        elif rr < 1:
            return 35.0 + (rr - 0.25) * 40.0
        elif rr < 4:
            return 65.0 + (rr - 1) * 11.67
        else:
            return 100.0
    
    rr_str = str(release_regularity).lower()
    if 'none' in rr_str or 'irregular' in rr_str:
        return 15.0
    elif 'sporadic' in rr_str:
        return 30.0
    elif 'quarterly' in rr_str:
        return 50.0
    elif 'monthly' in rr_str:
        return 65.0
    elif 'biweekly' in rr_str:
        return 80.0
    elif 'weekly' in rr_str or 'regular' in rr_str:
        return 95.0
    else:
        return 40.0


def score_publisher_verified(publisher_verified: Any) -> float:
    """
    Score based on publisher verification status.
    Verified publishers are more trustworthy.
    """
    if publisher_verified is None:
        return 40.0
    
    if isinstance(publisher_verified, bool):
        return 90.0 if publisher_verified else 30.0
    
    pv_str = str(publisher_verified).lower()
    if pv_str in ('true', '1', 'yes', 'verified', 'confirmed'):
        return 90.0
    elif pv_str in ('false', '0', 'no', 'unverified', 'unconfirmed'):
        return 30.0
    else:
        return 50.0


def score_version_count(version_count: Any) -> float:
    """
    Score based on number of versions.
    Too few might indicate new/unstable, too many might be chaos.
    """
    if version_count is None:
        return 40.0
    
    try:
        vc = int(version_count) if not isinstance(version_count, int) else version_count
    except (ValueError, TypeError):
        return 40.0
    
    if vc <= 0:
        return 10.0
    elif vc == 1:
        return 25.0
    elif vc < 5:
        return 40.0 + (vc - 1) * 10.0
    elif vc < 20:
        return 70.0 + min(15.0, (vc - 5) * 0.67)
    elif vc < 50:
        return 85.0 + min(10.0, (vc - 20) * 0.33)
    else:
        return 95.0 + min(5.0, (vc - 50) * 0.1)


def score_download_count(download_count: Any) -> float:
    """
    Score based on download count.
    Popular packages with traction are more trustworthy.
    """
    if download_count is None:
        return 35.0
    
    try:
        dc = int(download_count) if not isinstance(download_count, int) else download_count
    except (ValueError, TypeError):
        return 35.0
    
    if dc <= 0:
        return 10.0
    elif dc < 100:
        return 20.0 + dc * 0.2
    elif dc < 1000:
        return 40.0 + (dc - 100) * 0.067
    elif dc < 10000:
        return 60.0 + (dc - 1000) * 0.067
    elif dc < 100000:
        return 85.0 + min(10.0, (dc - 10000) * 0.0011)
    else:
        return 95.0 + min(5.0, (dc - 100000) * 0.00005)


def score_update_pattern(age_days: int, last_updated: str, first_seen: str) -> float:
    """
    Score based on update pattern - comparing age, last update, and first seen.
    Consistent updates across the package lifetime indicate good maintenance.
    """
    if not age_days or age_days <= 0:
        return 20.0
    
    age_score = score_age_days(age_days)
    recency_score = score_recency(last_updated)
    
    if not first_seen:
        return (age_score + recency_score) / 2.0
    
    observed_days = compute_days_between(first_seen, last_updated or datetime.utcnow().isoformat())
    if observed_days <= 0:
        return recency_score
    
    update_rate = observed_days / age_days if age_days > 0 else 0
    
    if update_rate >= 0.8:
        return 100.0
    elif update_rate >= 0.5:
        return 70.0 + (update_rate - 0.5) * 60.0
    elif update_rate >= 0.2:
        return 40.0 + (update_rate - 0.2) * 100.0
    else:
        return 20.0 + update_rate * 100.0


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute temporal stability score using MULTIPLE metadata fields.
    Designed to produce 20+ distinct values across diverse package types.
    
    Scoring dimensions (with granular weighting):
    1. Package age (15%): How long the package has existed
    2. Recency of last update (18%): When was it last touched
    3. First seen observation (12%): How long have we watched it
    4. Commit frequency (12%): Development activity level
    5. Release regularity (10%): Consistency of releases
    6. Publisher verified (8%): Trust signal from registry
    7. Version count (8%): Release history depth
    8. Download count (7%): Community adoption
    9. Update pattern (10%): Cross-dimensional pattern analysis
    
    Returns: (score, evidence_dict)
    """
    evidence = {}
    raw_scores = {}
    
    age_days = metadata.get('age_days') or metadata.get('age')
    if age_days is not None:
        try:
            age_days = int(age_days)
        except (ValueError, TypeError):
            age_days = None
    
    first_seen = metadata.get('first_seen')
    last_updated = metadata.get('last_updated') or metadata.get('updated_at')
    commit_frequency = metadata.get('commit_frequency') or metadata.get('commits_per_week')
    release_regularity = metadata.get('release_regularity') or metadata.get('release_schedule')
    publisher_verified = metadata.get('publisher_verified') or metadata.get('verified')
    version_count = metadata.get('version_count') or metadata.get('version_total')
    download_count = metadata.get('download_count') or metadata.get('downloads')
    
    raw_scores['age'] = score_age_days(age_days)
    raw_scores['recency'] = score_recency(last_updated)
    raw_scores['first_seen'] = score_first_seen(first_seen)
    raw_scores['commit_frequency'] = score_commit_frequency(commit_frequency)
    raw_scores['release_regularity'] = score_release_regularity(release_regularity)
    raw_scores['publisher_verified'] = score_publisher_verified(publisher_verified)
    raw_scores['version_count'] = score_version_count(version_count)
    raw_scores['download_count'] = score_download_count(download_count)
    raw_scores['update_pattern'] = score_update_pattern(age_days, last_updated, first_seen)
    
    weights = {
        'age': 0.15,
        'recency': 0.18,
        'first_seen': 0.12,
        'commit_frequency': 0.12,
        'release_regularity': 0.10,
        'publisher_verified': 0.08,
        'version_count': 0.08,
        'download_count': 0.07,
        'update_pattern': 0.10
    }
    
    raw_scores_list = list(raw_scores.values())
    weights_list = list(weights.values())
    
    total_weight = sum(weights_list)
    normalized_weights = [w / total_weight for w in weights_list]
    
    weighted_score = sum(
        score * weight 
        for score, weight in zip(raw_scores_list, normalized_weights)
    )
    
    combined_boost = sigmoid(weighted_score, steepness=0.08, midpoint=60.0) * 10.0
    
    final_score = min(100.0, max(0.0, weighted_score + combined_boost))
    
    final_score = round(final_score, 2)
    
    evidence = {
        'signal_name': SIGNAL_NAME,
        'version': VERSION,
        'score': final_score,
        'age_score': round(raw_scores['age'], 2),
        'recency_score': round(raw_scores['recency'], 2),
        'first_seen_score': round(raw_scores['first_seen'], 2),
        'commit_frequency_score': round(raw_scores['commit_frequency'], 2),
        'release_regularity_score': round(raw_scores['release_regularity'], 2),
        'publisher_verified_score': round(raw_scores['publisher_verified'], 2),
        'version_count_score': round(raw_scores['version_count'], 2),
        'download_count_score': round(raw_scores['download_count'], 2),
        'update_pattern_score': round(raw_scores['update_pattern'], 2),
        'age_days': age_days,
        'last_updated': last_updated,
        'first_seen': first_seen,
        'commit_frequency': commit_frequency,
        'release_regularity': release_regularity,
        'publisher_verified': publisher_verified,
        'version_count': version_count,
        'download_count': download_count
    }
    
    return final_score, evidence


def compute_batch_scores(metadatas: List[Dict[str, Any]]) -> List[Tuple[float, Dict[str, Any]]]:
    """
    Compute scores for a batch of server metadata records.
    Returns list of (score, evidence) tuples.
    """
    results = []
    for metadata in metadatas:
        results.append(compute_score(metadata))
    return results


def get_score_band(score: float) -> str:
    """Map score to risk band."""
    if score >= 85.0:
        return "EXCELLENT"
    elif score >= 70.0:
        return "GOOD"
    elif score >= 50.0:
        return "FAIR"
    elif score >= 30.0:
        return "POOR"
    else:
        return "CRITICAL"


def run():
    """Daemon entry point for temporal_stability_enrichment_v4."""
    import time
    import requests
    
    SERVICE_PORT = 8785
    WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
    QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
    EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
    PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
    LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
    POLL_SECS = 300
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ]
    )
    LOG.info(f"Starting {SERVICE_NAME}")
    
    def check_single_instance():
        import os
        if os.path.exists(PID_FILE):
            with open(PID_FILE, 'r') as f:
                old_pid = f.read().strip()
            try:
                os.kill(int(old_pid), 0)
                LOG.error(f"Another instance running with PID {old_pid}")
                return False
            except (OSError, ProcessLookupError, ValueError):
                LOG.warning(f"Stale PID file found: {old_pid}")
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        return True
    
    def remove_pid_file():
        import os
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
    
    def signal_handler(signum, frame):
        LOG.info(f"Received signal {signum}, shutting down")
        remove_pid_file()
        exit(0)
    
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        LOG.error("Cannot start - another instance is running")
        exit(1)
    
    def send_heartbeat():
        try:
            requests.post(WRITE_SERVICE_URL, json={
                'table': 'service_health',
                'rows': {'service': SERVICE_NAME, 'last_heartbeat': datetime.utcnow().isoformat()}
            }, timeout=5)
        except Exception as e:
            LOG.warning(f"Heartbeat failed: {e}")
    
    def ws_query(sql):
        try:
            resp = requests.post(QUERY_SERVICE_URL, json={'sql': sql}, timeout=30)
            data = resp.json()
            return data.get('rows', [])
        except Exception as e:
            LOG.error(f"Query failed: {e}")
            return []
    
    def ws_write(table, rows):
        try:
            requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows, 'wait': True}, timeout=30)
        except Exception as e:
            LOG.error(f"Write failed: {e}")
    
    def get_unscored_servers():
        sql = """
        SELECT DISTINCT r.server_id, r.name, r.description, r.url,
               r.scan_count, r.registry_source, r.created_at
        FROM mcp_server_registry r
        LEFT JOIN mcp_signal_scores s ON r.server_id = s.server_id 
            AND s.signal_name = 'temporal_stability'
        WHERE s.server_id IS NULL
        ORDER BY r.scan_count DESC
        LIMIT 100
        """
        return ws_query(sql)
    
    def get_metadata_for_server(server_id: int):
        sql = f"""
        SELECT 
            r.server_id,
            r.name,
            r.age_days,
            r.first_seen,
            r.last_updated,
            r.commit_frequency,
            r.release_regularity,
            r.publisher_verified,
            r.version_count,
            r.download_count,
            r.verified
        FROM mcp_server_registry r
        WHERE r.server_id = {server_id}
        """
        results = ws_query(sql)
        if results:
            row = results[0]
            metadata = {
                'age_days': row.get('age_days'),
                'first_seen': row.get('first_seen'),
                'last_updated': row.get('last_updated'),
                'commit_frequency': row.get('commit_frequency'),
                'release_regularity': row.get('release_regularity'),
                'publisher_verified': row.get('publisher_verified') or row.get('verified'),
                'version_count': row.get('version_count'),
                'download_count': row.get('download_count')
            }
            return metadata
        return {}
    
    def score_to_verdict(score: float) -> str:
        if score >= 80.0:
            return "TRUSTED"
        elif score >= 60.0:
            return "PROVISIONAL"
        elif score >= 40.0:
            return "REVIEW"
        else:
            return "REJECT"
    
    def process_server(server: Dict) -> bool:
        server_id = server.get('server_id')
        if not server_id:
            return False
        
        metadata = get_metadata_for_server(server_id)
        
        if not any(metadata.values()):
            LOG.debug(f"No temporal metadata for server {server_id}")
            return False
        
        score, evidence = compute_score(metadata)
        verdict = score_to_verdict(score)
        now = datetime.utcnow().isoformat()
        
        ws_write('mcp_signal_scores', {
            'server_id': server_id,
            'signal_name': SIGNAL_NAME,
            'score': score,
            'evidence': str(evidence),
            'scored_at': now,
            'version': VERSION
        })
        
        LOG.info(f"Server {server_id}: score={score}, verdict={verdict}")
        return True
    
    def heartbeat_loop():
        while True:
            try:
                send_heartbeat()
                servers = get_unscored_servers()
                processed = 0
                for server in servers:
                    if process_server(server):
                        processed += 1
                LOG.info(f"Cycle complete: {processed} servers enriched")
            except Exception as e:
                LOG.error(f"Cycle error: {e}")
            time.sleep(POLL_SECS)
    
    LOG.info(f"{SERVICE_NAME} daemon ready")
    heartbeat_loop()


if __name__ == '__main__':
    run()