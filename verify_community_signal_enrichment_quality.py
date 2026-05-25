#!/usr/bin/env python3
"""
ZO-SENTINEL: Community Signal Enrichment Quality Verification
Quality verification harness for community_signal_enrichment.py
"""

import sys
import os
import logging
from datetime import datetime, timezone
from typing import Any

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

# Constants
SERVICE_NAME = "community_signal_verifier"
SERVICE_PORT = 8772
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(SERVICE_NAME)


def ws_write(table: str, rows: dict, wait: bool = True) -> dict:
    """Write to write service."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': wait},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Write failed for table {table}: {e}")
        raise


def send_heartbeat() -> None:
    """Send service heartbeat."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def generate_synthetic_corpus() -> list[dict[str, Any]]:
    """Generate synthetic metadata corpus with varied field distributions."""
    corpus = []
    
    # Base varied samples (20+ core samples)
    base_samples = [
        # Low engagement
        {'stars': 0, 'registry_source': 'npm', 'download_count': 100, 'dependency_count': 0},
        {'stars': 1, 'registry_source': 'pypi', 'download_count': 250, 'dependency_count': 1},
        {'stars': 5, 'registry_source': 'github', 'download_count': 500, 'dependency_count': 2},
        {'stars': 10, 'registry_source': 'npm', 'download_count': 1000, 'dependency_count': 3},
        
        # Medium engagement
        {'stars': 50, 'registry_source': 'pypi', 'download_count': 10000, 'dependency_count': 10},
        {'stars': 100, 'registry_source': 'npm', 'download_count': 50000, 'dependency_count': 15},
        {'stars': 150, 'registry_source': 'github', 'download_count': 75000, 'dependency_count': 20},
        {'stars': 200, 'registry_source': 'pypi', 'download_count': 100000, 'dependency_count': 25},
        {'stars': 300, 'registry_source': 'npm', 'download_count': 150000, 'dependency_count': 30},
        {'stars': 400, 'registry_source': 'github', 'download_count': 200000, 'dependency_count': 40},
        {'stars': 500, 'registry_source': 'pypi', 'download_count': 250000, 'dependency_count': 45},
        {'stars': 600, 'registry_source': 'npm', 'download_count': 300000, 'dependency_count': 50},
        {'stars': 700, 'registry_source': 'github', 'download_count': 350000, 'dependency_count': 55},
        {'stars': 800, 'registry_source': 'pypi', 'download_count': 400000, 'dependency_count': 60},
        {'stars': 900, 'registry_source': 'npm', 'download_count': 450000, 'dependency_count': 65},
        
        # High engagement
        {'stars': 1000, 'registry_source': 'github', 'download_count': 500000, 'dependency_count': 70},
        {'stars': 2000, 'registry_source': 'npm', 'download_count': 1000000, 'dependency_count': 100},
        {'stars': 5000, 'registry_source': 'pypi', 'download_count': 2500000, 'dependency_count': 150},
        {'stars': 10000, 'registry_source': 'github', 'download_count': 5000000, 'dependency_count': 200},
        {'stars': 25000, 'registry_source': 'npm', 'download_count': 10000000, 'dependency_count': 300},
        {'stars': 50000, 'registry_source': 'pypi', 'download_count': 50000000, 'dependency_count': 500},
    ]
    corpus.extend(base_samples)
    
    # Edge cases with varied combinations
    edge_cases = [
        {'stars': 0, 'registry_source': 'github', 'download_count': 0, 'dependency_count': 0},
        {'stars': 1, 'registry_source': 'npm', 'download_count': 1, 'dependency_count': 1},
        {'stars': 100, 'registry_source': 'pypi', 'download_count': 10, 'dependency_count': 5},
        {'stars': 1000, 'registry_source': 'npm', 'download_count': 100, 'dependency_count': 50},
        {'stars': 1000, 'registry_source': 'github', 'download_count': 10000000, 'dependency_count': 10},
        {'stars': 50, 'registry_source': 'github', 'download_count': 10000000, 'dependency_count': 5},
    ]
    corpus.extend(edge_cases)
    
    # Additional granular samples for fine-grained score differentiation
    for stars in [20, 30, 40, 60, 70, 80, 90, 110, 120, 130, 140, 160, 180]:
        for source in ['npm', 'pypi', 'github']:
            corpus.append({
                'stars': stars,
                'registry_source': source,
                'download_count': stars * 500,
                'dependency_count': stars // 5
            })
    
    return corpus


def main() -> bool:
    """Run quality verification."""
    corpus_size = 0
    distinct_scores = 0
    
    try:
        from community_signal_enrichment import compute_score
        
        log.info("Loading community_signal_enrichment module...")
        
        corpus = generate_synthetic_corpus()
        corpus_size = len(corpus)
        log.info(f"Generated synthetic corpus of {corpus_size} metadata samples")
        
        scores = []
        for idx, metadata in enumerate(corpus):
            try:
                score = compute_score(metadata)
                scores.append(score)
            except Exception as e:
                log.error(f"compute_score failed for sample {idx}: {e}")
                raise
        
        distinct_scores = len(set(scores))
        log.info(f"Computed {len(scores)} scores with {distinct_scores} distinct values")
        
        score_counts = {}
        for s in scores:
            score_counts[s] = score_counts.get(s, 0) + 1
        
        log.info("Score frequency distribution:")
        for score_val in sorted(score_counts.keys())[:20]:
            log.info(f"  Score {score_val}: {score_counts[score_val]} samples")
        if len(score_counts) > 20:
            log.info(f"  ... and {len(score_counts) - 20} more unique scores")
        
        QUALITY_FLOOR = 15
        assert distinct_scores >= QUALITY_FLOOR, (
            f"Signal quality floor not met: {distinct_scores} distinct values < {QUALITY_FLOOR} required"
        )
        
        min_score = min(scores)
        max_score = max(scores)
        log.info(f"Score range: [{min_score}, {max_score}]")
        
        assert distinct_scores > 1, "All scores identical - enrichment is degenerate"
        
        high_star_samples = [(m, s) for m, s in zip(corpus, scores) if m.get('stars', 0) >= 1000]
        low_star_samples = [(m, s) for m, s in zip(corpus, scores) if m.get('stars', 0) < 100]
        
        if high_star_samples and low_star_samples:
            avg_high = sum(s for _, s in high_star_samples) / len(high_star_samples)
            avg_low = sum(s for _, s in low_star_samples) / len(low_star_samples)
            log.info(f"High star avg score: {avg_high:.2f}, Low star avg score: {avg_low:.2f}")
            assert avg_high >= avg_low, "Enrichment should favor high-star packages over low-star"
        
        log.info("Quality verification PASSED")
        ws_write('verification_results', {
            'test': 'community_signal_enrichment_quality',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'corpus_size': corpus_size,
            'distinct_scores': distinct_scores,
            'quality_passed': True,
            'min_score': min_score,
            'max_score': max_score
        })
        return True
        
    except AssertionError as e:
        log.error(f"Quality verification FAILED: {e}")
        ws_write('verification_results', {
            'test': 'community_signal_enrichment_quality',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'corpus_size': corpus_size,
            'distinct_scores': distinct_scores,
            'quality_passed': False,
            'error': str(e)
        })
        return False
        
    except ImportError as e:
        log.error(f"Module import failed: {e}")
        return False
        
    except Exception as e:
        log.error(f"Verification error: {e}")
        return False


def run():
    """Daemon entry point."""
    log.info(f"Starting {SERVICE_NAME}...")
    send_heartbeat()
    
    success = main()
    
    send_heartbeat()
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    run()