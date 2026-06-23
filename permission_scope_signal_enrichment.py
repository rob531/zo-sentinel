"""
Permission Scope Signal Enrichment Module

Pure enrichment module that computes trust/reliability scores for packages
based on multiple metadata signals including permission scope data.
"""

from typing import Dict, Tuple, Any, List
from enum import IntEnum


class TrustLevel(IntEnum):
    """Trust level classification based on composite score."""
    CRITICAL_UNTRUSTED = 1
    VERY_LOW_TRUST = 2
    LOW_TRUST = 3
    SUSPICIOUS = 4
    NEUTRAL_LOW = 5
    NEUTRAL = 6
    NEUTRAL_HIGH = 7
    TRUSTED = 8
    HIGHLY_TRUSTED = 9
    EXCELLENT = 10
    PREMIUM = 11


class PermissionScope:
    """Permission scope classification constants."""
    NONE = "none"
    MINIMAL = "minimal"
    STANDARD = "standard"
    ELEVATED = "elevated"
    CRITICAL = "critical"
    DANGEROUS = "dangerous"


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute a composite trust/reliability score based on package metadata.
    
    Args:
        metadata: Dictionary containing package metadata fields including:
            - registry_source: str (e.g., 'npm', 'pypi', 'cargo')
            - age_days: int (package age in days)
            - download_count: int (total downloads)
            - dependency_count: int (number of dependencies)
            - publisher_verified: bool
            - stars: int (repository stars)
            - permissions: list[str] (requested permissions)
            - permission_scopes: list[str] (permission scope levels)
            - has_license: bool
            - has_readme: bool
            - has_repository: bool
            - commit_activity_days: int (days since last commit)
            - open_issues: int (number of open issues)
            - closed_issues: int (number of closed issues)
            - versions_count: int (number of released versions)
            - has_security_policy: bool
            - has_code_of_conduct: bool
    
    Returns:
        Tuple of (score, details) where:
            - score: float between 0.0 and 1.0 representing composite trust
            - details: dict with breakdown of scoring components
    
    Raises:
        TypeError: If metadata is not a dict
        ValueError: If metadata is empty or missing critical fields
    """
    if not isinstance(metadata, dict):
        raise TypeError("metadata must be a dictionary")
    
    if not metadata:
        raise ValueError("metadata cannot be empty")
    
    # Initialize scoring components
    score_components: Dict[str, float] = {}
    signal_strengths: Dict[str, float] = {}
    warnings: List[str] = []
    risk_flags: List[str] = []
    
    # === Registry Source Scoring ===
    registry_score = _score_registry_source(metadata.get('registry_source', ''))
    score_components['registry_source'] = registry_score
    signal_strengths['registry_source'] = 0.15
    
    # === Age Scoring (mature packages are generally more trustworthy) ===
    age_days = metadata.get('age_days', 0)
    age_score = _score_package_age(age_days)
    score_components['package_age'] = age_score
    signal_strengths['package_age'] = 0.10
    
    # === Download Count Scoring ===
    downloads = metadata.get('download_count', 0)
    download_score = _score_downloads(downloads, age_days)
    score_components['download_count'] = download_score
    signal_strengths['download_count'] = 0.12
    
    # === Dependency Count Scoring ===
    deps = metadata.get('dependency_count', 0)
    dep_score = _score_dependencies(deps)
    score_components['dependency_count'] = dep_score
    signal_strengths['dependency_count'] = 0.08
    
    # === Publisher Verification ===
    verified = metadata.get('publisher_verified', False)
    verified_score = _score_publisher_verification(verified)
    score_components['publisher_verified'] = verified_score
    signal_strengths['publisher_verified'] = 0.12
    
    # === Stars Scoring ===
    stars = metadata.get('stars', 0)
    stars_score = _score_stars(stars)
    score_components['stars'] = stars_score
    signal_strengths['stars'] = 0.08
    
    # === Permission Scope Analysis ===
    permissions = metadata.get('permissions', [])
    permission_scopes = metadata.get('permission_scopes', [])
    perm_score, perm_risks, perm_warnings = _score_permission_scope(
        permissions, permission_scopes
    )
    score_components['permission_scope'] = perm_score
    signal_strengths['permission_scope'] = 0.15
    risk_flags.extend(perm_risks)
    warnings.extend(perm_warnings)
    
    # === Additional Quality Signals ===
    quality_score, quality_flags = _score_quality_indicators(metadata)
    score_components['quality_indicators'] = quality_score
    signal_strengths['quality_indicators'] = 0.10
    risk_flags.extend(quality_flags)
    
    # === Community Health Score ===
    community_score = _score_community_health(metadata)
    score_components['community_health'] = community_score
    signal_strengths['community_health'] = 0.05
    
    # === Activity Score ===
    activity_score = _score_activity(metadata)
    score_components['activity'] = activity_score
    signal_strengths['activity'] = 0.05
    
    # === Compute Weighted Composite Score ===
    total_weight = sum(signal_strengths.values())
    composite_score = 0.0
    
    for component_name, component_score in score_components.items():
        weight = signal_strengths[component_name]
        normalized_weight = weight / total_weight
        composite_score += component_score * normalized_weight
    
    # Apply risk multipliers
    if risk_flags:
        composite_score *= (1.0 - (len(risk_flags) * 0.05))
    
    # Clamp to valid range
    composite_score = max(0.0, min(1.0, composite_score))
    
    # Build details dictionary
    details = {
        'composite_score': round(composite_score, 4),
        'trust_level': _get_trust_level(composite_score),
        'score_components': {k: round(v, 4) for k, v in score_components.items()},
        'signal_weights': {k: round(v, 4) for k, v in signal_strengths.items()},
        'risk_flags': risk_flags,
        'warnings': warnings,
        'raw_metadata_summary': {
            'registry': metadata.get('registry_source', 'unknown'),
            'age_days': age_days,
            'downloads': downloads,
            'dependencies': deps,
            'verified': verified,
            'stars': stars,
            'permission_count': len(permissions),
            'permission_scope_max': _get_max_permission_scope(permission_scopes),
        },
        'recommendation': _get_recommendation(composite_score, risk_flags, perm_risks),
    }
    
    return composite_score, details


def _score_registry_source(source: str) -> float:
    """Score based on registry/source reputation."""
    source_scores = {
        'npm': 0.70,
        'pypi': 0.70,
        'cargo': 0.75,
        'nuget': 0.68,
        'maven': 0.72,
        'rubygems': 0.65,
        'packagist': 0.65,
        'pub': 0.72,
        'crates': 0.75,
        'hex': 0.70,
        'hackage': 0.60,
        'conda': 0.65,
        # Scorched earth registries get low scores
        'unknown': 0.20,
        '': 0.20,
    }
    base_score = source_scores.get(source.lower(), 0.50)
    
    # Adjust for known quality registries
    if source.lower() in ['crates', 'cargo', 'maven', 'pub']:
        return base_score + 0.15
    elif source.lower() in ['npm', 'pypi', 'nuget', 'hex']:
        return base_score + 0.05
    
    return base_score


def _score_package_age(age_days: int) -> float:
    """Score based on package age - older established packages score higher."""
    if age_days < 0:
        return 0.0
    elif age_days < 7:
        return 0.15  # Very new - unknown
    elif age_days < 30:
        return 0.30  # Less than a month
    elif age_days < 90:
        return 0.45  # A few months
    elif age_days < 180:
        return 0.55  # ~6 months
    elif age_days < 365:
        return 0.65  # Less than a year
    elif age_days < 730:  # ~2 years
        return 0.75
    elif age_days < 1825:  # ~5 years
        return 0.85
    else:
        return 0.95  # Very mature


def _score_downloads(downloads: int, age_days: int) -> float:
    """Score based on download count, normalized by age."""
    if downloads <= 0:
        return 0.10
    
    # Downloads per day (if package has age)
    if age_days > 0:
        downloads_per_day = downloads / age_days
    else:
        downloads_per_day = downloads
    
    if downloads_per_day < 1:
        return 0.25
    elif downloads_per_day < 10:
        return 0.40
    elif downloads_per_day < 100:
        return 0.60
    elif downloads_per_day < 1000:
        return 0.75
    elif downloads_per_day < 10000:
        return 0.85
    else:
        return 0.95


def _score_dependencies(deps: int) -> float:
    """Score based on dependency count - moderate is best, too many is risky."""
    if deps < 0:
        return 0.0
    elif deps == 0:
        return 0.50  # No deps - neutral
    elif deps <= 5:
        return 0.75  # Light - good
    elif deps <= 15:
        return 0.65  # Moderate - acceptable
    elif deps <= 50:
        return 0.45  # Heavy - concerning
    else:
        return 0.25  # Very heavy - high supply chain risk


def _score_publisher_verification(verified: bool) -> float:
    """Score based on publisher verification status."""
    return 1.0 if verified else 0.30


def _score_stars(stars: int) -> float:
    """Score based on repository stars."""
    if stars < 0:
        return 0.0
    elif stars == 0:
        return 0.30  # No community engagement
    elif stars < 10:
        return 0.40
    elif stars < 100:
        return 0.55
    elif stars < 1000:
        return 0.70
    elif stars < 10000:
        return 0.85
    else:
        return 0.95


def _score_permission_scope(
    permissions: List[str],
    permission_scopes: List[str]
) -> Tuple[float, List[str], List[str]]:
    """Score based on permission requirements - fewer/bounded is better."""
    risks = []
    warnings = []
    
    if not permissions and not permission_scopes:
        return 0.95, [], []  # No permissions needed - very safe
    
    # Dangerous permissions that trigger high risk
    dangerous_perms = {
        'admin', 'sudo', 'root', 'sysadmin', 'write', 'delete',
        'shell', 'exec', 'execute', 'eval', 'compile',
        'filesystem_full', 'fs_full', 'network_full', 'net_full',
        'process', 'os', 'subprocess', 'spawn',
        'read_all', 'write_all', 'secret', 'credential',
        'automated', 'ci_cd', 'deploy', 'production',
    }
    
    # Elevated permissions that need scrutiny
    elevated_perms = {
        'filesystem', 'fs', 'network', 'net', 'http', 'https',
        'read_local', 'write_local', 'env', 'environment',
        'process_list', 'system_info', 'user_info',
        'git', 'version_control', 'hook',
    }
    
    # Check permissions
    found_dangerous = []
    found_elevated = []
    
    all_perms = [p.lower() for p in permissions + permission_scopes]
    
    for perm in all_perms:
        if perm in dangerous_perms or 'dangerous' in perm or 'critical' in perm:
            found_dangerous.append(perm)
        elif perm in elevated_perms:
            found_elevated.append(perm)
    
    # Calculate score
    if found_dangerous:
        risks.extend([f"Dangerous permission: {p}" for p in found_dangerous])
        return 0.10, risks, warnings
    
    if found_elevated:
        warnings.extend([f"Elevated permission: {p}" for p in found_elevated])
        if len(found_elevated) <= 2:
            return 0.50, [], warnings
        else:
            return 0.35, [], warnings
    
    # Standard or minimal permissions
    scope_max = _get_max_permission_scope(permission_scopes)
    if scope_max == PermissionScope.MINIMAL:
        return 0.85, [], []
    elif scope_max == PermissionScope.STANDARD:
        return 0.70, [], []
    elif scope_max == PermissionScope.ELEVATED:
        return 0.45, [], []
    elif scope_max == PermissionScope.CRITICAL:
        return 0.20, [], []
    
    # Default - some permissions but not clearly categorized
    return 0.55, [], []


def _get_max_permission_scope(scopes: List[str]) -> str:
    """Determine the maximum scope level from a list of scopes."""
    if not scopes:
        return PermissionScope.NONE
    
    scope_hierarchy = [
        PermissionScope.NONE,
        PermissionScope.MINIMAL,
        PermissionScope.STANDARD,
        PermissionScope.ELEVATED,
        PermissionScope.CRITICAL,
        PermissionScope.DANGEROUS,
    ]
    
    max_index = 0
    for scope in scopes:
        scope_lower = scope.lower()
        for i, level in enumerate(scope_hierarchy):
            if level in scope_lower or scope_lower in level:
                max_index = max(max_index, i)
    
    return scope_hierarchy[max_index]


def _score_quality_indicators(metadata: Dict[str, Any]) -> Tuple[float, List[str]]:
    """Score based on quality indicators (license, readme, repo, etc.)."""
    flags = []
    score = 0.0
    weight = 0.0
    
    indicators = {
        'has_license': (0.25, 'License present'),
        'has_readme': (0.20, 'README present'),
        'has_repository': (0.25, 'Repository linked'),
        'has_security_policy': (0.15, 'Security policy'),
        'has_code_of_conduct': (0.10, 'Code of conduct'),
        'has_contributing_guide': (0.05, 'Contributing guide'),
    }
    
    for field, (value, label) in indicators.items():
        if metadata.get(field, False):
            score += value
        else:
            flags.append(f"Missing: {label}")
            # Missing quality indicators reduce score but don't penalize heavily
            score += value * 0.3
    
    return min(1.0, score), flags


def _score_community_health(metadata: Dict[str, Any]) -> float:
    """Score based on community health metrics."""
    open_issues = metadata.get('open_issues', 0)
    closed_issues = metadata.get('closed_issues', 0)
    total_issues = open_issues + closed_issues
    
    if total_issues == 0:
        return 0.50  # No issues tracked - neutral
    
    # High ratio of closed to open is good
    resolution_rate = closed_issues / total_issues
    
    if resolution_rate >= 0.9:
        return 0.85
    elif resolution_rate >= 0.7:
        return 0.70
    elif resolution_rate >= 0.5:
        return 0.55
    elif resolution_rate >= 0.3:
        return 0.40
    else:
        return 0.25


def _score_activity(metadata: Dict[str, Any]) -> float:
    """Score based on commit/activity frequency."""
    last_commit_days = metadata.get('commit_activity_days', -1)
    versions_count = metadata.get('versions_count', 0)
    
    # Score based on recency of last commit
    if last_commit_days < 0:
        activity_score = 0.40  # Unknown
    elif last_commit_days <= 7:
        activity_score = 0.95
    elif last_commit_days <= 30:
        activity_score = 0.80
    elif last_commit_days <= 90:
        activity_score = 0.65
    elif last_commit_days <= 180:
        activity_score = 0.50
    elif last_commit_days <= 365:
        activity_score = 0.35
    else:
        activity_score = 0.20  # Abandoned
    
    # Bonus for having multiple versions (active releases)
    if versions_count >= 10:
        activity_score = min(1.0, activity_score + 0.10)
    elif versions_count >= 5:
        activity_score = min(1.0, activity_score + 0.05)
    elif versions_count == 0:
        activity_score *= 0.8  # No releases is concerning
    
    return activity_score


def _get_trust_level(score: float) -> str:
    """Map score to trust level string."""
    if score >= 0.95:
        return TrustLevel.PREMIUM.name
    elif score >= 0.85:
        return TrustLevel.EXCELLENT.name
    elif score >= 0.75:
        return TrustLevel.HIGHLY_TRUSTED.name
    elif score >= 0.65:
        return TrustLevel.TRUSTED.name
    elif score >= 0.55:
        return TrustLevel.NEUTRAL_HIGH.name
    elif score >= 0.45:
        return TrustLevel.NEUTRAL.name
    elif score >= 0.35:
        return TrustLevel.NEUTRAL_LOW.name
    elif score >= 0.25:
        return TrustLevel.SUSPICIOUS.name
    elif score >= 0.15:
        return TrustLevel.LOW_TRUST.name
    elif score >= 0.05:
        return TrustLevel.VERY_LOW_TRUST.name
    else:
        return TrustLevel.CRITICAL_UNTRUSTED.name


def _get_recommendation(score: float, risk_flags: List[str], perm_risks: List[str]) -> str:
    """Generate a recommendation based on the score and risks."""
    if perm_risks or len(risk_flags) >= 3:
        return "BLOCK: High-risk permissions or multiple quality issues detected"
    elif score >= 0.85:
        return "APPROVE: Highly trusted package - safe to use"
    elif score >= 0.70:
        return "APPROVE_WITH_VERIFICATION: Trusted package, standard verification recommended"
    elif score >= 0.55:
        return "REVIEW: Neutral score - review permissions and dependencies"
    elif score >= 0.35:
        return "REVIEW_WITH_CAUTION: Some concerns detected - careful review required"
    else:
        return "REJECT: Low trust score or significant risk factors"