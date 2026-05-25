import sys
import hashlib
import re
from typing import Dict, List, Tuple, Any

SERVICE_NAME = "domain_trust_enrichment_v2"
VERSION = "2.0.0"

HIGH_RISK_TLDS = {'.ru', '.xyz', '.tk', '.ml', '.ga', '.cc', '.su', '.info', '.biz', '.click', '.link', '.work', '.date', '.racing'}
MODERATE_RISK_TLDS = {'.io', '.co', '.net', '.org', '.me'}

TRUSTED_REGISTRIES = {'npm', 'pypi', 'github', 'official', 'nuget', 'maven', 'rubygems', 'cargo'}
COMMUNITY_REGISTRIES = {'community', 'aur', 'brew'}
RISKY_REGISTRIES = {'unknown', 'unverified', 'third-party', 'mirror'}


def get_tld_from_url(url: str) -> str:
    if not url:
        return ''
    url_lower = url.lower()
    for tld in HIGH_RISK_TLDS | MODERATE_RISK_TLDS | {'.com', '.org', '.io', '.net', '.edu', '.gov'}:
        if url_lower.endswith(tld):
            return tld
    return ''


def score_registry_source(registry_source: str) -> Tuple[float, str]:
    if not registry_source:
        return 40.0, 'none'
    rs = registry_source.lower().strip()
    if rs in TRUSTED_REGISTRIES:
        return 100.0, 'trusted'
    elif rs in COMMUNITY_REGISTRIES:
        return 70.0, 'community'
    elif rs in RISKY_REGISTRIES:
        return 25.0, 'risky'
    else:
        return 50.0, 'neutral'


def score_age_days(age_days: int) -> Tuple[float, str]:
    if age_days is None:
        return 0.0, 'unknown'
    if age_days < 7:
        return 15.0, 'very_new'
    elif age_days < 30:
        return 35.0, 'new'
    elif age_days < 90:
        return 55.0, 'recent'
    elif age_days < 180:
        return 70.0, 'established'
    elif age_days < 365:
        return 85.0, 'mature'
    else:
        return 100.0, 'well_established'


def score_download_count(download_count: int) -> Tuple[float, str]:
    if download_count is None:
        return 0.0, 'unknown'
    if download_count < 100:
        return 20.0, 'minimal'
    elif download_count < 1000:
        return 40.0, 'low'
    elif download_count < 10000:
        return 60.0, 'moderate'
    elif download_count < 100000:
        return 80.0, 'popular'
    elif download_count < 1000000:
        return 95.0, 'very_popular'
    else:
        return 100.0, 'extremely_popular'


def score_dependency_count(dependency_count: int) -> Tuple[float, str]:
    if dependency_count is None:
        return 0.0, 'unknown'
    if dependency_count < 0:
        return 0.0, 'invalid'
    elif dependency_count == 0:
        return 50.0, 'no_deps'
    elif dependency_count <= 3:
        return 70.0, 'minimal'
    elif dependency_count <= 10:
        return 85.0, 'low'
    elif dependency_count <= 30:
        return 80.0, 'moderate'
    elif dependency_count <= 100:
        return 60.0, 'high'
    else:
        return 40.0, 'very_high'


def score_publisher_verified(publisher_verified: bool) -> Tuple[float, str]:
    if publisher_verified is None:
        return 0.0, 'unknown'
    return (100.0, 'verified') if publisher_verified else (30.0, 'unverified')


def score_stars(stars: int) -> Tuple[float, str]:
    if stars is None:
        return 0.0, 'unknown'
    if stars < 0:
        return 0.0, 'invalid'
    elif stars == 0:
        return 35.0, 'none'
    elif stars < 10:
        return 50.0, 'minimal'
    elif stars < 100:
        return 70.0, 'low'
    elif stars < 1000:
        return 85.0, 'moderate'
    else:
        return 100.0, 'high'


def score_url_tld(url: str) -> Tuple[float, str, List[str]]:
    if not url:
        return 0.0, 'none', []
    tld = get_tld_from_url(url)
    penalties = []
    if not tld:
        return 30.0, 'unknown_tld', []
    if tld in HIGH_RISK_TLDS:
        penalties.append(('high_risk_tld', 20.0))
        return 25.0, 'high_risk', penalties
    elif tld in MODERATE_RISK_TLDS:
        return 70.0, 'moderate_risk', []
    else:
        return 90.0, 'standard', []


def compute_score(metadata: dict) -> Tuple[float, dict]:
    fields_used: List[str] = []
    partial_scores: Dict[str, float] = {}
    field_contexts: Dict[str, str] = {}
    interaction_penalties: List[Dict[str, Any]] = []
    penalty_log: List[str] = []

    registry_source = metadata.get('registry_source', '')
    age_days = metadata.get('age_days')
    download_count = metadata.get('download_count')
    dependency_count = metadata.get('dependency_count')
    publisher_verified = metadata.get('publisher_verified')
    stars = metadata.get('stars')
    url = metadata.get('url', '')

    if registry_source:
        fields_used.append('registry_source')
        score, context = score_registry_source(registry_source)
        partial_scores['registry_source'] = score
        field_contexts['registry_source'] = context

    if age_days is not None:
        fields_used.append('age_days')
        score, context = score_age_days(age_days)
        partial_scores['age_days'] = score
        field_contexts['age_days'] = context

    if download_count is not None:
        fields_used.append('download_count')
        score, context = score_download_count(download_count)
        partial_scores['download_count'] = score
        field_contexts['download_count'] = context

    if dependency_count is not None:
        fields_used.append('dependency_count')
        score, context = score_dependency_count(dependency_count)
        partial_scores['dependency_count'] = score
        field_contexts['dependency_count'] = context

    if publisher_verified is not None:
        fields_used.append('publisher_verified')
        score, context = score_publisher_verified(publisher_verified)
        partial_scores['publisher_verified'] = score
        field_contexts['publisher_verified'] = context

    if stars is not None:
        fields_used.append('stars')
        score, context = score_stars(stars)
        partial_scores['stars'] = score
        field_contexts['stars'] = context

    if url:
        fields_used.append('url')
        score, context, tld_penalties = score_url_tld(url)
        partial_scores['url'] = score
        field_contexts['url'] = context
        for pname, pval in tld_penalties:
            interaction_penalties.append({'name': pname, 'value': pval, 'reason': f'TLD {url} is high-risk'})
            penalty_log.append(f'{pname}: -{pval}')

    age_days_value = age_days if age_days is not None else -1
    download_count_value = download_count if download_count is not None else -1
    registry_source_value = registry_source.lower() if registry_source else ''

    if age_days_value >= 0 and age_days_value < 30 and download_count_value >= 0 and download_count_value < 1000:
        interaction_penalties.append({
            'name': 'new_low_downloads',
            'value': 15.0,
            'reason': f'Package is {age_days_value} days old but only {download_count_value} downloads'
        })
        penalty_log.append(f'new_low_downloads: -15.0')

    if publisher_verified is False and (not registry_source_value or registry_source_value in RISKY_REGISTRIES):
        interaction_penalties.append({
            'name': 'unverified_unknown_registry',
            'value': 20.0,
            'reason': 'Publisher is unverified AND registry source is unknown/unverified'
        })
        penalty_log.append(f'unverified_unknown_registry: -20.0')

    if age_days_value >= 0 and age_days_value < 30 and stars is not None and stars > 1000:
        interaction_penalties.append({
            'name': 'high_stars_new_package',
            'value': 25.0,
            'reason': f'Package has {stars} stars but is only {age_days_value} days old'
        })
        penalty_log.append(f'high_stars_new_package: -25.0')

    if download_count_value >= 50000 and age_days_value >= 0 and age_days_value < 30:
        interaction_penalties.append({
            'name': 'astroturfing_risk',
            'value': 30.0,
            'reason': f'{download_count_value} downloads in {age_days_value} days suggests astroturfing'
        })
        penalty_log.append(f'astroturfing_risk: -30.0')

    if publisher_verified is False and download_count_value >= 0 and download_count_value > 50000:
        interaction_penalties.append({
            'name': 'popular_unverified',
            'value': 15.0,
            'reason': f'Package has {download_count_value} downloads but publisher is unverified'
        })
        penalty_log.append(f'popular_unverified: -15.0')

    if age_days_value >= 0 and age_days_value < 7 and dependency_count is not None and dependency_count > 20:
        interaction_penalties.append({
            'name': 'new_with_many_deps',
            'value': 18.0,
            'reason': f'Package is {age_days_value} days old but has {dependency_count} dependencies'
        })
        penalty_log.append(f'new_with_many_deps: -18.0')

    if url and not registry_source_value:
        interaction_penalties.append({
            'name': 'url_no_registry',
            'value': 10.0,
            'reason': 'URL present but no registry source provided'
        })
        penalty_log.append(f'url_no_registry: -10.0')

    if not partial_scores:
        return 0.0, {
            'fields_used': [],
            'partial_scores': {},
            'field_contexts': {},
            'interaction_penalties': [],
            'final_score_raw': 0.0,
            'error': 'no fields available'
        }

    total_penalty = sum(p['value'] for p in interaction_penalties)
    final_score_raw = sum(partial_scores.values()) - total_penalty

    final_score = max(0.0, min(100.0, final_score_raw))

    evidence = {
        'fields_used': fields_used,
        'partial_scores': {k: round(v, 4) for k, v in partial_scores.items()},
        'field_contexts': field_contexts,
        'interaction_penalties': interaction_penalties,
        'penalty_log': penalty_log,
        'total_penalty': round(total_penalty, 4),
        'final_score_raw': round(final_score_raw, 4)
    }

    return round(final_score, 2), evidence


def generate_fingerprint(metadata: dict) -> str:
    fingerprint_parts = [
        str(metadata.get('registry_source', '')),
        str(metadata.get('age_days', '')),
        str(metadata.get('download_count', '')),
        str(metadata.get('dependency_count', '')),
        str(metadata.get('publisher_verified', '')),
        str(metadata.get('stars', '')),
        str(metadata.get('url', '')),
    ]
    return hashlib.md5('|'.join(fingerprint_parts).encode()).hexdigest()[:12]


def run_harness():
    test_cases = [
        {
            'name': 'New package, unknown registry, no downloads',
            'metadata': {
                'registry_source': 'unknown',
                'age_days': 5,
                'download_count': 10,
                'dependency_count': 2,
                'publisher_verified': False,
                'stars': 0,
                'url': 'http://example.xyz/package'
            }
        },
        {
            'name': 'Mature npm package, verified, popular',
            'metadata': {
                'registry_source': 'npm',
                'age_days': 730,
                'download_count': 5000000,
                'dependency_count': 8,
                'publisher_verified': True,
                'stars': 25000,
                'url': 'https://lodash.com'
            }
        },
        {
            'name': 'New but extremely popular (astroturfing risk)',
            'metadata': {
                'registry_source': 'community',
                'age_days': 3,
                'download_count': 200000,
                'dependency_count': 0,
                'publisher_verified': False,
                'stars': 5,
                'url': ''
            }
        },
        {
            'name': 'Typosquat risk - high-risk TLD with no verification',
            'metadata': {
                'registry_source': 'third-party',
                'age_days': 15,
                'download_count': 500,
                'dependency_count': 1,
                'publisher_verified': False,
                'stars': 0,
                'url': 'https://lodhsh.tk/package'
            }
        },
        {
            'name': 'Minimal metadata only',
            'metadata': {}
        },
        {
            'name': 'Only registry source known',
            'metadata': {
                'registry_source': 'npm'
            }
        },
        {
            'name': 'Popular but very new package with high stars',
            'metadata': {
                'registry_source': 'pypi',
                'age_days': 10,
                'download_count': 150000,
                'dependency_count': 5,
                'publisher_verified': True,
                'stars': 5000,
                'url': ''
            }
        },
        {
            'name': 'Old package, low trust, moderate downloads',
            'metadata': {
                'registry_source': 'unverified',
                'age_days': 400,
                'download_count': 5000,
                'dependency_count': 15,
                'publisher_verified': False,
                'stars': 50,
                'url': 'http://mypackage.xyz'
            }
        },
        {
            'name': 'High-risk TLD with high stars but new',
            'metadata': {
                'registry_source': 'mirror',
                'age_days': 8,
                'download_count': 10000,
                'dependency_count': 3,
                'publisher_verified': False,
                'stars': 2000,
                'url': 'https://coolpackage.ml'
            }
        },
        {
            'name': 'Zero-day package with many dependencies',
            'metadata': {
                'registry_source': 'unknown',
                'age_days': 2,
                'download_count': 50,
                'dependency_count': 45,
                'publisher_verified': False,
                'stars': 0,
                'url': ''
            }
        },
        {
            'name': 'Moderate maturity with verified publisher',
            'metadata': {
                'registry_source': 'github',
                'age_days': 180,
                'download_count': 50000,
                'dependency_count': 6,
                'publisher_verified': True,
                'stars': 800,
                'url': 'https://github.com/myorg/myrepo'
            }
        },
        {
            'name': 'High-risk TLD, unverified, low downloads, high deps',
            'metadata': {
                'registry_source': 'unknown',
                'age_days': 20,
                'download_count': 200,
                'dependency_count': 80,
                'publisher_verified': False,
                'stars': 3,
                'url': 'https://suspicious.cc/payload'
            }
        },
        {
            'name': 'Established package with moderate trust',
            'metadata': {
                'registry_source': 'community',
                'age_days': 300,
                'download_count': 15000,
                'dependency_count': 4,
                'publisher_verified': False,
                'stars': 100,
                'url': ''
            }
        },
        {
            'name': 'Popular unverified package',
            'metadata': {
                'registry_source': 'third-party',
                'age_days': 60,
                'download_count': 100000,
                'dependency_count': 2,
                'publisher_verified': False,
                'stars': 200,
                'url': ''
            }
        },
    ]

    results = []
    for tc in test_cases:
        metadata = tc['metadata']
        score, evidence = compute_score(metadata)
        fingerprint = generate_fingerprint(metadata)
        results.append({
            'name': tc['name'],
            'score': score,
            'fingerprint': fingerprint,
            'fields_count': len(evidence['fields_used']),
            'penalty_count': len(evidence['interaction_penalties'])
        })
        print(f"Test: {tc['name']}")
        print(f"  Score: {score}")
        print(f"  Fingerprint: {fingerprint}")
        print(f"  Fields used: {evidence['fields_used']}")
        if evidence['penalty_log']:
            print(f"  Penalties: {evidence['penalty_log']}")
        print()

    unique_scores = set(r['score'] for r in results)
    unique_fingerprints = set(r['fingerprint'] for r in results)
    print(f"Total test cases: {len(results)}")
    print(f"Unique scores: {len(unique_scores)}")
    print(f"Unique fingerprints: {len(unique_fingerprints)}")
    print(f"Scores: {sorted(unique_scores)}")

    if len(unique_scores) >= 20:
        print("\n[PASS] Generated at least 20 distinct scores")
    else:
        print(f"\n[WARN] Only {len(unique_scores)} distinct scores (need >= 20)")

    if len(unique_fingerprints) >= 20:
        print("[PASS] Generated at least 20 distinct fingerprints")
    else:
        print(f"[INFO] Only {len(unique_fingerprints)} distinct fingerprints")


def run():
    print(f"{SERVICE_NAME} v{VERSION} - Domain Trust Enrichment Engine v2")
    print("Running harness validation...")
    run_harness()


if __name__ == '__main__':
    run()