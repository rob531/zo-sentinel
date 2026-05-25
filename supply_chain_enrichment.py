import logging

SERVICE_NAME = "supply_chain_enrichment"

logger = logging.getLogger(__name__)

TRUSTED_REGISTRIES = frozenset({
    'npm', 'npmjs', 'node', 'pypi', 'pypi.org', 'PyPI',
    'rubygems', 'gem', 'nuget', 'maven', 'central',
    'conda', 'crates.io', 'cocoapods'
})


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute supply chain risk score from package metadata.
    
    Pure function: same input always produces same output.
    No DB writes, no network calls.
    
    Args:
        metadata: dict with keys:
            - registry_source: str (optional)
            - age_days: int (optional)
            - download_count: int (optional)
            - dependency_count: int (optional)
            - publisher_verified: bool (optional)
            - stars: int (optional)
    
    Returns:
        tuple: (score 0.0-100.0, evidence dict)
               Higher score = more trustworthy supply chain
    """
    score = 50.0
    evidence = {}
    score_wrapper = [score]  # Mutable container for helper functions
    
    _score_registry(metadata, score_wrapper, evidence)
    _score_age(metadata, score_wrapper, evidence)
    _score_downloads(metadata, score_wrapper, evidence)
    _score_dependencies(metadata, score_wrapper, evidence)
    _score_publisher(metadata, score_wrapper, evidence)
    _score_stars(metadata, score_wrapper, evidence)
    
    score = score_wrapper[0]
    score = max(0.0, min(100.0, score))
    evidence['final_score'] = round(score, 2)
    
    return round(score, 2), evidence


def _score_registry(m: dict, s: list, e: dict) -> None:
    """Registry source trust scoring"""
    source = m.get('registry_source', '')
    if source in TRUSTED_REGISTRIES:
        s[0] += 15
        e['registry_trusted'] = source
    elif not source:
        s[0] -= 15
        e['registry_missing'] = True
    else:
        s[0] -= 10
        e['registry_unknown'] = source


def _score_age(m: dict, s: list, e: dict) -> None:
    """Package age trust scoring"""
    age = m.get('age_days', 0)
    if age >= 730:
        s[0] += 15
        e['age'] = f'mature({age}d)'
    elif age >= 365:
        s[0] += 10
        e['age'] = f'established({age}d)'
    elif age >= 90:
        s[0] += 5
        e['age'] = f'reasonable({age}d)'
    elif age >= 30:
        s[0] -= 5
        e['age'] = f'new({age}d)'
    else:
        s[0] -= 15
        e['age'] = f'very_new({age}d)'


def _score_downloads(m: dict, s: list, e: dict) -> None:
    """Download count trust scoring"""
    downloads = m.get('download_count', 0)
    if downloads >= 10000000:
        s[0] += 15
        e['downloads'] = f'widespread({downloads:,})'
    elif downloads >= 1000000:
        s[0] += 10
        e['downloads'] = f'popular({downloads:,})'
    elif downloads >= 100000:
        s[0] += 5
        e['downloads'] = f'moderate({downloads:,})'
    elif downloads > 0:
        s[0] -= 5
        e['downloads'] = f'low({downloads:,})'
    else:
        e['downloads'] = 'none'


def _score_dependencies(m: dict, s: list, e: dict) -> None:
    """Dependency count risk scoring"""
    deps = m.get('dependency_count', 0)
    if deps == 0:
        s[0] += 5
        e['deps'] = 'minimal'
    elif deps <= 10:
        s[0] += 10
        e['deps'] = f'lean({deps})'
    elif deps <= 30:
        s[0] += 5
        e['deps'] = f'moderate({deps})'
    elif deps <= 100:
        e['deps'] = f'high({deps})'
    else:
        s[0] -= 20
        e['deps'] = f'excessive({deps})'


def _score_publisher(m: dict, s: list, e: dict) -> None:
    """Publisher verification trust scoring"""
    verified = m.get('publisher_verified', False)
    if verified is True:
        s[0] += 20
        e['publisher_verified'] = True
    else:
        s[0] -= 20
        e['publisher_verified'] = False


def _score_stars(m: dict, s: list, e: dict) -> None:
    """GitHub stars community trust scoring"""
    stars = m.get('stars', 0)
    if stars >= 10000:
        s[0] += 10
        e['stars'] = f'well-known({stars:,})'
    elif stars >= 1000:
        s[0] += 5
        e['stars'] = f'established({stars:,})'
    elif stars > 0:
        e['stars'] = f'emerging({stars:,})'
    else:
        e['stars'] = 'none'


if __name__ == '__main__':
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
    )
    
    test_cases = [
        {
            'name': 'trusted_pypi_package',
            'metadata': {
                'registry_source': 'pypi',
                'age_days': 1500,
                'download_count': 5000000,
                'dependency_count': 15,
                'publisher_verified': True,
                'stars': 5000
            }
        },
        {
            'name': 'unverified_unknown_source',
            'metadata': {
                'registry_source': 'unknown-registry',
                'age_days': 10,
                'download_count': 50,
                'dependency_count': 150,
                'publisher_verified': False,
                'stars': 0
            }
        },
        {
            'name': 'minimal_metadata',
            'metadata': {}
        }
    ]
    
    all_passed = True
    for tc in test_cases:
        score, evidence = compute_score(tc['metadata'])
        logger.info(f"Test '{tc['name']}': score={score}, evidence={evidence}")
        if not (0.0 <= score <= 100.0):
            logger.error(f"FAIL: score {score} out of bounds [0,100]")
            all_passed = False
    
    if all_passed:
        logger.info("All tests passed")
        sys.exit(0)
    else:
        logger.error("Some tests failed")
        sys.exit(1)