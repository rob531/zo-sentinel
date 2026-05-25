import logging
import requests
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple
from collections import defaultdict

# Service Configuration
SERVICE_NAME = "diagnose_temporal_stability_enrichment"
SERVICE_PORT = 8772
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(SERVICE_NAME)


def send_heartbeat():
    """Send service heartbeat to write service."""
    try:
        payload = {
            'table': 'service_health',
            'rows': {
                'service': SERVICE_NAME,
                'last_heartbeat': datetime.utcnow().isoformat()
            },
            'wait': True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def ws_write(table: str, rows: Dict[str, Any]):
    """Write data to write service."""
    try:
        payload = {'table': table, 'rows': rows, 'wait': True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Write failed for {table}: {e}")
        return None


def ws_query(query: str) -> List[Dict[str, Any]]:
    """Query data via write service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={'table': '__query__', 'query': query, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json().get('results', [])
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []


def ws_execute(query: str) -> bool:
    """Execute statement via write service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={'table': '__execute__', 'query': query, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Execute failed: {e}")
        return False


def compute_score(metadata: Dict[str, Any]) -> float:
    """Replica of temporal_stability_enrichment compute_score function for testing."""
    first_seen = metadata.get('first_seen')
    last_updated = metadata.get('last_updated')
    last_assessed = metadata.get('last_assessed')
    age_days = metadata.get('age_days', 0)
    update_frequency = metadata.get('update_frequency', 0)
    
    if not all([first_seen, last_updated, last_assessed]):
        return 0.5
    
    try:
        if isinstance(first_seen, str):
            first_seen = datetime.fromisoformat(first_seen.replace('Z', '+00:00'))
        if isinstance(last_updated, str):
            last_updated = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
        if isinstance(last_assessed, str):
            last_assessed = datetime.fromisoformat(last_assessed.replace('Z', '+00:00'))
    except Exception:
        return 0.5
    
    now = datetime.utcnow()
    
    age_score = min(age_days / 365.0, 1.0) * 0.3
    
    days_since_update = (now - last_updated).days
    update_recency = max(0, 1 - (days_since_update / 30.0))
    update_score = update_recency * 0.25
    
    days_since_assess = (now - last_assessed).days
    assess_recency = max(0, 1 - (days_since_assess / 14.0))
    assess_score = assess_recency * 0.2
    
    freq_score = min(update_frequency / 30.0, 1.0) * 0.25
    
    total_score = age_score + update_score + assess_score + freq_score
    
    return round(total_score, 2)


def generate_test_cases() -> List[Tuple[Dict[str, Any], str]]:
    """Generate comprehensive test cases for temporal stability scoring."""
    test_cases = []
    now = datetime.utcnow()
    base_date = now - timedelta(days=365)
    
    for age_days in [0, 30, 90, 180, 365, 730]:
        for update_freq in [0, 7, 14, 30, 60]:
            for days_since_update in [0, 7, 14, 30, 60, 90]:
                for days_since_assess in [0, 3, 7, 14, 30]:
                    first_seen = (now - timedelta(days=age_days)).isoformat()
                    last_updated = (now - timedelta(days=days_since_update)).isoformat()
                    last_assessed = (now - timedelta(days=days_since_assess)).isoformat()
                    
                    metadata = {
                        'first_seen': first_seen,
                        'last_updated': last_updated,
                        'last_assessed': last_assessed,
                        'age_days': age_days,
                        'update_frequency': update_freq
                    }
                    
                    desc = f"age={age_days}d,uf={update_freq}d,dsu={days_since_update}d,dsa={days_since_assess}d"
                    test_cases.append((metadata, desc))
    
    return test_cases


def analyze_score_distribution(scores: List[float]) -> Dict[str, Any]:
    """Analyze the distribution of computed scores."""
    unique_scores = set(scores)
    score_counts = defaultdict(int)
    
    for score in scores:
        score_counts[round(score, 2)] += 1
    
    score_order = sorted(score_counts.keys())
    ranges = []
    for i, score in enumerate(score_order):
        count = score_counts[score]
        ranges.append(f"{score:.2f}:{count}")
    
    return {
        'total_cases': len(scores),
        'distinct_scores': len(unique_scores),
        'score_counts': dict(score_counts),
        'score_range_summary': ranges,
        'min_score': min(scores) if scores else 0,
        'max_score': max(scores) if scores else 0,
        'avg_score': sum(scores) / len(scores) if scores else 0
    }


def identify_flatttening_behavior(scores: List[float], test_descs: List[str]) -> Dict[str, Any]:
    """Identify where score flattening occurs."""
    score_to_cases = defaultdict(list)
    for score, desc in zip(scores, test_descs):
        score_to_cases[round(score, 2)].append(desc)
    
    flattening_analysis = {}
    
    for score in sorted(score_to_cases.keys()):
        cases = score_to_cases[score]
        flattening_analysis[f"score_{score:.2f}"] = {
            'count': len(cases),
            'sample_cases': cases[:5]
        }
    
    return flattening_analysis


def run_diagnosis():
    """Run the temporal stability enrichment diagnosis."""
    log.info("Starting temporal stability enrichment diagnosis")
    send_heartbeat()
    
    test_cases = generate_test_cases()
    log.info(f"Generated {len(test_cases)} test cases")
    
    results = []
    for metadata, desc in test_cases:
        score = compute_score(metadata)
        results.append((score, desc, metadata))
    
    scores = [r[0] for r in results]
    
    distribution = analyze_score_distribution(scores)
    log.info(f"Score distribution: {json.dumps(distribution, indent=2)}")
    
    flattening = identify_flatttening_behavior(scores, [r[1] for r in results])
    
    findings = {
        'diagnosis_timestamp': datetime.utcnow().isoformat(),
        'service_name': SERVICE_NAME,
        'total_test_cases': len(test_cases),
        'distinct_scores_found': distribution['distinct_scores'],
        'distribution': distribution,
        'flattening_analysis': flattening,
        'findings': []
    }
    
    if distribution['distinct_scores_found'] <= 4:
        findings['findings'].append({
            'severity': 'HIGH',
            'issue': 'CRITICAL_SCORE_COLLAPSE',
            'description': f'Only {distribution["distinct_scores_found"]} distinct scores produced from {len(test_cases)} test cases',
            'expected': f'At least 20 distinct scores for varied input parameters',
            'impact': 'Temporal stability enrichment provides minimal discrimination'
        })
    
    score_counts = distribution['score_counts']
    if score_counts:
        max_bucket_count = max(score_counts.values())
        if max_bucket_count > len(test_cases) * 0.4:
            findings['findings'].append({
                'severity': 'HIGH',
                'issue': 'SCORE_BUCKETING',
                'description': f'Single score bucket contains {max_bucket_count} cases ({max_bucket_count/len(test_cases)*100:.1f}%)',
                'impact': 'Scores collapse into too few buckets for effective discrimination'
            })
    
    score_order = sorted(score_counts.keys())
    if len(score_order) > 1:
        gaps = []
        for i in range(len(score_order) - 1):
            gap = score_order[i+1] - score_order[i]
            if gap > 0.1:
                gaps.append(f"{score_order[i]:.2f}->{score_order[i+1]:.2f} (gap={gap:.2f})")
        if gaps:
            findings['findings'].append({
                'severity': 'MEDIUM',
                'issue': 'SCORE_CLUSTERING',
                'description': f'Score clustering with large gaps: {gaps[:5]}',
                'impact': 'Score distribution is non-uniform with clustering'
            })
    
    for score_level in [0.0, 0.5, 1.0]:
        if score_level in score_counts and score_counts[score_level] > 0:
            cases_at_level = [r for r in results if r[0] == score_level][:3]
            findings['findings'].append({
                'severity': 'INFO',
                'issue': f'FLAT_SCORE_{score_level}',
                'description': f'{score_counts[score_level]} cases score exactly {score_level}',
                'example_inputs': [r[2] for r in cases_at_level]
            })
    
    log.info("Diagnosis complete, writing findings to write service")
    
    ws_write('diagnosis_findings', {
        'service': SERVICE_NAME,
        'timestamp': datetime.utcnow().isoformat(),
        'total_cases': len(test_cases),
        'distinct_scores': distribution['distinct_scores'],
        'min_score': distribution['min_score'],
        'max_score': distribution['max_score'],
        'findings_json': json.dumps(findings['findings'])
    })
    
    log.info(f"DIAGNOSIS FINDINGS: {json.dumps(findings, indent=2, default=str)}")
    
    return findings


def run():
    """Daemon entry point."""
    log.info(f"{SERVICE_NAME} daemon starting on port {SERVICE_PORT}")
    send_heartbeat()
    
    findings = run_diagnosis()
    
    log.info(f"Diagnosis completed. Distinct scores: {findings['distinct_scores_found']}")
    log.info(f"Findings: {len(findings['findings'])} issues identified")
    
    return findings


if __name__ == '__main__':
    run()