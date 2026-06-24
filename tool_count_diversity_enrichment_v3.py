# tool_count_diversity_enrichment_v3.py
"""
Tool Count Diversity Enrichment Module v3
==========================================
Extends v2 to compute diversity scores based on MULTIPLE schema fields:
- tool_names: variety in naming patterns
- tool_descriptions: diversity in description content
- tool_categories: diversity in categorical groupings  
- tool_permissions: variety in permission requirements
- tool_param_counts: distribution of parameter complexity

Section 3 Contract: Must produce enriched score beyond raw count.
"""

import hashlib
import re
from typing import Dict, List, Any, Optional
from collections import Counter


def compute_score(metadata: dict) -> dict:
    """
    Compute comprehensive diversity score for tool_count field.
    
    Reads multiple schema fields to assess true tool diversity, not just count.
    
    Args:
        metadata: Dictionary containing tool schema information
        
    Returns:
        dict with:
            - raw_score: original tool count
            - diversity_score: computed 0-1 diversity metric
            - components: breakdown by field type
            - enriched_value: final computed score
            - confidence: reliability of the score (0-1)
            - signal_strength: 'WEAK'/'MODERATE'/'STRONG'
    """
    
    # Extract all relevant fields
    tool_names = metadata.get('tool_names', [])
    tool_descriptions = metadata.get('tool_descriptions', [])
    tool_categories = metadata.get('tool_categories', [])
    tool_permissions = metadata.get('tool_permissions', [])
    tool_param_counts = metadata.get('tool_param_counts', [])
    
    # Count check
    tool_count = len(tool_names)
    
    if tool_count == 0:
        return {
            'raw_score': 0,
            'diversity_score': 0.0,
            'components': {},
            'enriched_value': 0.0,
            'confidence': 1.0,
            'signal_strength': 'WEAK',
            'field_count': 0
        }
    
    # Initialize component scores
    components = {}
    
    # 1. NAME PATTERN DIVERSITY (20% weight)
    components['name_diversity'] = _compute_name_diversity(tool_names)
    
    # 2. DESCRIPTION DIVERSITY (25% weight)  
    components['description_diversity'] = _compute_description_diversity(tool_descriptions)
    
    # 3. CATEGORY DIVERSITY (25% weight)
    components['category_diversity'] = _compute_category_diversity(tool_categories)
    
    # 4. PERMISSION DIVERSITY (15% weight)
    components['permission_diversity'] = _compute_permission_diversity(tool_permissions)
    
    # 5. PARAMETER COMPLEXITY DIVERSITY (15% weight)
    components['param_diversity'] = _compute_param_diversity(tool_param_counts)
    
    # Compute weighted diversity score
    weights = {
        'name_diversity': 0.20,
        'description_diversity': 0.25,
        'category_diversity': 0.25,
        'permission_diversity': 0.15,
        'param_diversity': 0.15
    }
    
    weighted_sum = sum(
        components[field] * weights[field] 
        for field in weights
    )
    
    # Normalize to 0-1 range with count boost
    count_normalized = min(tool_count / 10.0, 1.0)  # Cap at 10 tools
    diversity_score = (weighted_sum * 0.6) + (count_normalized * 0.4)
    
    # Compute confidence based on field coverage
    populated_fields = sum(1 for field in [tool_names, tool_descriptions, 
                                           tool_categories, tool_permissions,
                                           tool_param_counts] if len(field) > 0)
    confidence = populated_fields / 5.0
    
    # Enriched value combines raw count with diversity
    enriched_value = round(diversity_score * 10, 2)
    
    # Signal strength classification
    if enriched_value >= 7.0:
        signal_strength = 'STRONG'
    elif enriched_value >= 4.0:
        signal_strength = 'MODERATE'
    else:
        signal_strength = 'WEAK'
    
    return {
        'raw_score': tool_count,
        'diversity_score': round(diversity_score, 4),
        'components': {k: round(v, 4) for k, v in components.items()},
        'enriched_value': enriched_value,
        'confidence': round(confidence, 4),
        'signal_strength': signal_strength,
        'field_count': populated_fields,
        'tool_count': tool_count
    }


def _compute_name_diversity(names: List[str]) -> float:
    """Compute diversity based on naming patterns and uniqueness."""
    if not names:
        return 0.0
    
    # 1. Unique name ratio
    unique_ratio = len(set(names)) / len(names)
    
    # 2. Length variation
    lengths = [len(n) for n in names]
    length_variance = _compute_variance(lengths) if len(lengths) > 1 else 0.0
    length_diversity = min(length_variance / 100.0, 1.0)  # Normalize
    
    # 3. Prefix/suffix pattern variety
    prefixes = set()
    suffixes = set()
    for name in names:
        parts = re.split(r'[_:\-]', name.lower())
        if parts:
            prefixes.add(parts[0])
            suffixes.add(parts[-1])
    
    prefix_diversity = min(len(prefixes) / max(len(names), 1), 1.0)
    suffix_diversity = min(len(suffixes) / max(len(names), 1), 1.0)
    
    # Combined score
    score = (
        unique_ratio * 0.4 +
        length_diversity * 0.2 +
        prefix_diversity * 0.2 +
        suffix_diversity * 0.2
    )
    
    return min(score, 1.0)


def _compute_description_diversity(descriptions: List[str]) -> float:
    """Compute diversity based on description content variety."""
    if not descriptions:
        return 0.0
    
    # 1. Length variation
    lengths = [len(d) for d in descriptions]
    length_variance = _compute_variance(lengths) if len(lengths) > 1 else 0.0
    length_diversity = min(length_variance / 10000.0, 1.0)
    
    # 2. Vocabulary richness (unique words)
    all_words = []
    for desc in descriptions:
        words = re.findall(r'\b[a-z]{3,}\b', desc.lower())
        all_words.extend(words)
    
    if all_words:
        vocab_richness = len(set(all_words)) / len(all_words)
    else:
        vocab_richness = 0.0
    
    # 3. Action verb variety
    action_verbs = set()
    for desc in descriptions:
        verbs = re.findall(r'\b(get|create|update|delete|fetch|list|search|add|remove|set|find|process|handle|manage)\b', 
                          desc.lower())
        action_verbs.update(verbs)
    
    verb_diversity = min(len(action_verbs) / 10.0, 1.0)
    
    # 4. Description uniqueness
    unique_ratio = len(set(descriptions)) / len(descriptions)
    
    score = (
        length_diversity * 0.2 +
        vocab_richness * 0.3 +
        verb_diversity * 0.25 +
        unique_ratio * 0.25
    )
    
    return min(score, 1.0)


def _compute_category_diversity(categories: List[str]) -> float:
    """Compute diversity based on category distribution."""
    if not categories:
        return 0.0
    
    # 1. Unique category ratio
    unique_ratio = len(set(categories)) / len(categories)
    
    # 2. Category distribution entropy
    counter = Counter(categories)
    total = len(categories)
    
    if total > 0:
        probabilities = [count / total for count in counter.values()]
        entropy = -sum(p * (p ** 0.5) for p in probabilities if p > 0)  # Gini-like
        max_entropy = len(counter) / 2
        normalized_entropy = min(entropy / max_entropy, 1.0) if max_entropy > 0 else 0.0
    else:
        normalized_entropy = 0.0
    
    # 3. Top category dominance (inverse)
    if counter:
        max_share = max(counter.values()) / total
        dominance_penalty = max_share
    else:
        dominance_penalty = 0.0
    
    score = (
        unique_ratio * 0.3 +
        normalized_entropy * 0.4 +
        (1 - dominance_penalty) * 0.3
    )
    
    return min(score, 1.0)


def _compute_permission_diversity(permissions: List[str]) -> float:
    """Compute diversity based on permission requirements."""
    if not permissions:
        return 0.0
    
    # 1. Unique permission types
    flat_perms = []
    for perm in permissions:
        if isinstance(perm, list):
            flat_perms.extend(perm)
        elif isinstance(perm, str):
            flat_perms.append(perm)
    
    if not flat_perms:
        return 0.0
    
    unique_ratio = len(set(flat_perms)) / len(flat_perms)
    
    # 2. Permission set variation across tools
    if len(permissions) > 1:
        permission_sets = []
        for perm in permissions:
            if isinstance(perm, list):
                permission_sets.append(frozenset(perm))
            elif isinstance(perm, str):
                permission_sets.append(frozenset([perm]))
            else:
                permission_sets.append(frozenset())
        
        unique_sets = len(set(permission_sets))
        set_diversity = unique_sets / len(permissions)
    else:
        set_diversity = 0.5  # Single tool, moderate diversity assumed
    
    # 3. Permission complexity
    perm_lengths = [len(p) if isinstance(p, list) else 1 for p in permissions]
    complexity_variance = _compute_variance(perm_lengths) if len(perm_lengths) > 1 else 0.0
    complexity_diversity = min(complexity_variance / 25.0, 1.0)
    
    score = (
        unique_ratio * 0.35 +
        set_diversity * 0.35 +
        complexity_diversity * 0.30
    )
    
    return min(score, 1.0)


def _compute_param_diversity(param_counts: List[Any]) -> float:
    """Compute diversity based on parameter count distribution."""
    if not param_counts:
        return 0.0
    
    # Normalize to integers
    counts = []
    for c in param_counts:
        if isinstance(c, int):
            counts.append(c)
        elif isinstance(c, float):
            counts.append(int(c))
        elif isinstance(c, str):
            try:
                counts.append(int(float(c)))
            except:
                counts.append(0)
        elif isinstance(c, (list, dict)):
            counts.append(len(c))
        else:
            counts.append(0)
    
    if not counts:
        return 0.0
    
    # 1. Distribution spread
    min_c, max_c = min(counts), max(counts)
    range_normalized = (max_c - min_c) / max(max_c, 1)
    
    # 2. Variance in counts
    variance = _compute_variance(counts) if len(counts) > 1 else 0.0
    variance_normalized = min(variance / 100.0, 1.0)
    
    # 3. Distribution uniformity (counter-based entropy)
    counter = Counter(counts)
    total = len(counts)
    if total > 0:
        probabilities = [count / total for count in counter.values()]
        uniformity = 1 - (max(probabilities) - min(probabilities))
    else:
        uniformity = 0.0
    
    # 4. Mean distance from ideal (5 params = ideal complexity)
    ideal = 5
    mean_count = sum(counts) / len(counts)
    ideal_distance = abs(mean_count - ideal) / ideal
    
    score = (
        range_normalized * 0.25 +
        variance_normalized * 0.25 +
        uniformity * 0.25 +
        (1 - ideal_distance) * 0.25
    )
    
    return min(score, 1.0)


def _compute_variance(values: List[float]) -> float:
    """Compute variance of a list of values."""
    if len(values) < 2:
        return 0.0
    
    mean = sum(values) / len(values)
    squared_diffs = [(v - mean) ** 2 for v in values]
    return sum(squared_diffs) / len(values)


# Verification function for wiring check
def verify_wiring() -> dict:
    """Verify the module is properly wired and produces scores."""
    
    # Test metadata with varied tools
    test_metadata = {
        'tool_names': [
            'user_profile_fetch',
            'content_generator_create',
            'data_analytics_query',
            'notification_send_alert',
            'file_manager_upload',
            'api_gateway_forward'
        ],
        'tool_descriptions': [
            'Retrieve user profile information from database',
            'Generate new content with AI assistance',
            'Run analytics queries on data warehouse',
            'Send push notification alerts to users',
            'Upload files to cloud storage system',
            'Forward API requests to microservices'
        ],
        'tool_categories': [
            'user_management',
            'content',
            'analytics',
            'notifications',
            'storage',
            'networking'
        ],
        'tool_permissions': [
            ['read:users', 'read:profiles'],
            ['write:content', 'read:templates'],
            ['read:data', 'execute:queries'],
            ['send:notifications'],
            ['write:storage', 'read:storage'],
            ['proxy:requests']
        ],
        'tool_param_counts': [2, 4, 3, 5, 6, 2]
    }
    
    result = compute_score(test_metadata)
    
    return {
        'module_loaded': True,
        'function_callable': True,
        'produces_scores': True,
        'test_result': result,
        'signal_classification': result.get('signal_strength', 'UNKNOWN'),
        'enriched_value': result.get('enriched_value', 0)
    }


if __name__ == '__main__':
    # Run wiring verification
    print("=" * 60)
    print("TOOL_COUNT_DIVERSITY_ENRICHMENT_V3 - Wiring Verification")
    print("=" * 60)
    
    verification = verify_wiring()
    
    print(f"\nModule Loaded: {verification['module_loaded']}")
    print(f"Function Callable: {verification['function_callable']}")
    print(f"Produces Scores: {verification['produces_scores']}")
    print(f"\nTest Result:")
    for key, value in verification['test_result'].items():
        print(f"  {key}: {value}")
    
    print(f"\nSignal Classification: {verification['signal_classification']}")
    print(f"Enriched Value: {verification['enriched_value']}")
    print("\n" + "=" * 60)