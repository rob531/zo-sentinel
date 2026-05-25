import sys
import os
import logging
import json
from datetime import datetime, timedelta
from collections import Counter
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from temporal_stability_enrichment import compute_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("verify_temporal_stability")


def generate_synthetic_corpus(count: int = 25) -> list[dict]:
    """Generate synthetic metadata dicts with varied temporal fields."""
    now = datetime.now()
    corpus = []
    
    base_templates = [
        {"name": "brand_new_recent_verdict", "age_days": 1, "first_seen": (now - timedelta(days=1)).isoformat(), "last_updated": (now - timedelta(hours=2)).isoformat(), "last_verdict": "malicious"},
        {"name": "old_stale_unknown", "age_days": 365, "first_seen": (now - timedelta(days=365)).isoformat(), "last_updated": (now - timedelta(days=300)).isoformat(), "last_verdict": "unknown"},
        {"name": "moderate_fresh_verdict", "age_days": 30, "first_seen": (now - timedelta(days=30)).isoformat(), "last_updated": (now - timedelta(days=5)).isoformat(), "last_verdict": "suspicious"},
        {"name": "ancient_never_updated", "age_days": 730, "first_seen": (now - timedelta(days=730)).isoformat(), "last_updated": (now - timedelta(days=730)).isoformat(), "last_verdict": "benign"},
        {"name": "recent_updated_frequently", "age_days": 14, "first_seen": (now - timedelta(days=14)).isoformat(), "last_updated": (now - timedelta(minutes=30)).isoformat(), "last_verdict": "malicious"},
        {"name": "very_old_recent_verdict", "age_days": 500, "first_seen": (now - timedelta(days=500)).isoformat(), "last_updated": (now - timedelta(hours=1)).isoformat(), "last_verdict": "malicious"},
        {"name": "mid_age_stale_verdict", "age_days": 90, "first_seen": (now - timedelta(days=90)).isoformat(), "last_updated": (now - timedelta(days=85)).isoformat(), "last_verdict": "unknown"},
        {"name": "new_never_updated", "age_days": 3, "first_seen": (now - timedelta(days=3)).isoformat(), "last_updated": (now - timedelta(days=3)).isoformat(), "last_verdict": "unknown"},
        {"name": "old_frequently_updated", "age_days": 400, "first_seen": (now - timedelta(days=400)).isoformat(), "last_updated": (now - timedelta(hours=6)).isoformat(), "last_verdict": "suspicious"},
        {"name": "fresh_malicious", "age_days": 7, "first_seen": (now - timedelta(days=7)).isoformat(), "last_updated": (now - timedelta(days=1)).isoformat(), "last_verdict": "malicious"},
    ]
    
    for i, template in enumerate(base_templates):
        corpus.append(template.copy())
    
    for i in range(count - len(base_templates)):
        age_days = random.randint(1, 730)
        staleness = random.randint(1, max(1, age_days))
        verdict = random.choice(["malicious", "suspicious", "benign", "unknown", "none"])
        
        metadata = {
            "name": f"synthetic_{i}",
            "age_days": age_days,
            "first_seen": (now - timedelta(days=age_days)).isoformat(),
            "last_updated": (now - timedelta(days=staleness)).isoformat(),
            "last_verdict": verdict
        }
        corpus.append(metadata)
    
    return corpus


def verify_temporal_stability_enrichment_quality() -> bool:
    """Verify temporal_stability_enrichment produces quality signal diversity."""
    logger.info("Starting temporal stability enrichment quality verification")
    
    corpus = generate_synthetic_corpus(count=25)
    logger.info(f"Generated corpus of {len(corpus)} synthetic metadata entries")
    
    scores = []
    for idx, metadata in enumerate(corpus):
        try:
            score = compute_score(metadata)
            scores.append(score)
            logger.debug(f"[{idx}] {metadata['name']} -> score={score}")
        except Exception as e:
            logger.error(f"compute_score failed for {metadata.get('name', idx)}: {e}")
            scores.append(None)
    
    valid_scores = [s for s in scores if s is not None]
    logger.info(f"Valid scores computed: {len(valid_scores)}/{len(corpus)}")
    
    if not valid_scores:
        logger.error("No valid scores produced - enricher may be broken")
        return False
    
    distinct_values = len(set(valid_scores))
    logger.info(f"Distinct score values: {distinct_values}")
    
    min_distinct_required = 10
    if distinct_values < min_distinct_required:
        logger.error(f"Insufficient signal diversity: {distinct_values} < {min_distinct_required}")
        return False
    
    score_distribution = Counter(valid_scores)
    logger.info(f"Score distribution (top 10): {dict(score_distribution.most_common(10))}")
    
    min_score = min(valid_scores)
    max_score = max(valid_scores)
    avg_score = sum(valid_scores) / len(valid_scores)
    logger.info(f"Score range: min={min_score}, max={max_score}, avg={avg_score:.4f}")
    
    score_percentiles = [
        ("p10", sorted(valid_scores)[int(len(valid_scores) * 0.1)]),
        ("p25", sorted(valid_scores)[int(len(valid_scores) * 0.25)]),
        ("p50", sorted(valid_scores)[int(len(valid_scores) * 0.5)]),
        ("p75", sorted(valid_scores)[int(len(valid_scores) * 0.75)]),
        ("p90", sorted(valid_scores)[int(len(valid_scores) * 0.9)]),
    ]
    for label, value in score_percentiles:
        logger.info(f"  {label} = {value}")
    
    logger.info("QUALITY VERIFICATION PASSED")
    return True


def run():
    """Main entry point for daemon execution."""
    logger.info("ZO-SENTINEL Temporal Stability Enrichment Quality Verifier")
    logger.info("=" * 60)
    
    try:
        result = verify_temporal_stability_enrichment_quality()
        if result:
            logger.info("Verification SUCCEEDED")
            sys.exit(0)
        else:
            logger.error("Verification FAILED")
            sys.exit(1)
    except Exception as e:
        logger.exception(f"Verification crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run()