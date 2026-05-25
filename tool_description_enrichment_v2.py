import hashlib
import re
import math
from typing import Dict, List, Any, Optional, Tuple

SERVICE_NAME = "tool_description_enrichment_v2"
SIGNAL_NAME = "tool_description_safety"
MAX_SCORE = 100.0

# Dangerous tool name patterns that indicate potential risk
DANGEROUS_TOOL_PATTERNS = [
    r'exec[ute]?', r'shell', r'bash', r'cmd', r'run[_]?command',
    r'system[_]?command', r'os[_-]?cmd', r'sudo', r'superuser',
    r'delete[_-]?all', r'format[_]?disk', r'drop[_]?table',
    r'drop[_]?database', r'shutdown', r'reboot', r'kill[_]?all',
    r'sql[_-]?inject', r'eval', r'compile', r'upload[_-]?file',
    r'download[_-]?file', r'read[_-]?file', r'write[_-]?file',
    r'file[_-]?system', r'secret', r'password', r'credential',
    r'steal', r'exfiltrat', r'dump', r'scan[_-]?network',
]

# Parameter documentation quality thresholds
PARAM_DOC_THRESHOLDS = {
    'excellent': 0.85,
    'good': 0.65,
    'acceptable': 0.40,
    'poor': 0.20
}

# Schema complexity scoring weights
SCHEMA_COMPLEXITY_WEIGHTS = {
    'required_params': 2.5,
    'optional_params': 1.0,
    'nested_objects': 3.0,
    'arrays': 2.0,
    'enum_values': 1.5,
    'default_values': 0.8
}


def sigmoid(x: float) -> float:
    """Sigmoid function for smooth scoring."""
    return 1.0 / (1.0 + math.exp(-x))


def softmax_weight(value: float, values: List[float], temperature: float = 1.0) -> float:
    """Softmax weighting for relative scoring."""
    exp_vals = [math.exp(v / temperature) for v in values]
    total = sum(exp_vals)
    if total == 0:
        return 0.0
    return exp_vals[values.index(value)] / total if value in values else 0.0


def hash_string(text: str) -> str:
    """Generate stable hash for deterministic scoring."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def score_tool_name_safety(name: str) -> float:
    """
    Score tool name for dangerous patterns.
    Lower score = more risky pattern detected.
    """
    name_lower = name.lower()
    
    # Check for dangerous patterns
    danger_score = 0.0
    for pattern in DANGEROUS_TOOL_PATTERNS:
        if re.search(pattern, name_lower):
            danger_score += 10.0
    
    # Check for obfuscation indicators
    if any(c.isdigit() for c in name) and len(name) > 15:
        danger_score += 5.0
    if '_' in name and any(c.isupper() for c in name):
        danger_score += 3.0
    if name.count('_') > 2:
        danger_score += 2.0
    
    # Check for version-like suffixes (might indicate impersonation)
    version_pattern = r'[_-]?v?\d+(\.\d+)*$'
    if re.search(version_pattern, name_lower):
        danger_score += 2.0
    
    # Cap at maximum penalty
    return max(0.0, 100.0 - danger_score * 5.0)


def score_description_length(description: Optional[str]) -> float:
    """
    Score description completeness based on length.
    Rich descriptions indicate better documentation.
    """
    if not description:
        return 0.0
    
    length = len(description.strip())
    
    # Very short descriptions are poor
    if length < 20:
        return 5.0
    elif length < 50:
        return 15.0
    elif length < 100:
        return 35.0
    elif length < 200:
        return 60.0
    elif length < 500:
        return 80.0
    else:
        return min(100.0, 95.0 + min(5.0, (length - 500) / 100))


def score_description_clarity(description: Optional[str]) -> float:
    """
    Score description clarity and quality.
    Looks for structured patterns that indicate good documentation.
    """
    if not description:
        return 0.0
    
    clarity_score = 50.0  # Base score
    
    # Positive indicators
    positive_patterns = [
        r'^[A-Z]',  # Starts with capital letter
        r'\.',  # Has sentences
        r'\b(the|this|that|a|an)\b',  # Has articles
        r'\b(for|with|to|when|if)\b',  # Has prepositions
        r'\d+',  # Has numbers
    ]
    
    for pattern in positive_patterns:
        if re.search(pattern, description):
            clarity_score += 5.0
    
    # Negative indicators (bad patterns)
    bad_patterns = [
        r'^[-_*#]+$',  # Only punctuation
        r'^\d+\.',  # Starts with number only
        r'\b[TtBD]\s*$',  # Ends with single letter
        r'[A-Z]{10,}',  # Many consecutive caps
    ]
    
    for pattern in bad_patterns:
        if re.search(pattern, description):
            clarity_score -= 10.0
    
    return max(0.0, min(100.0, clarity_score))


def score_has_examples(metadata: Dict[str, Any]) -> float:
    """
    Score presence and quality of examples.
    """
    score = 0.0
    
    # Check direct example indicators
    example_fields = ['examples', 'example', 'sample', 'samples', 'usage']
    for field in example_fields:
        if field in metadata:
            examples = metadata[field]
            if isinstance(examples, list) and len(examples) > 0:
                score += 30.0
                score += min(20.0, len(examples) * 5.0)  # More examples = higher score
                break
            elif isinstance(examples, str) and len(examples) > 10:
                score += 25.0
                break
    
    # Check embedded examples in description
    desc = metadata.get('description', '') or ''
    if re.search(r'example[:\s]', desc, re.IGNORECASE):
        score += 10.0
    if re.search(r'```[\s\S]+```', desc):  # Code blocks
        score += 15.0
    
    # Check code snippets
    if re.search(r'`[^`]+`', desc):  # Inline code
        score += 5.0
    
    return min(100.0, score)


def score_param_documented(parameters: Optional[List[Dict]]) -> float:
    """
    Score parameter documentation completeness.
    """
    if not parameters or len(parameters) == 0:
        return 20.0  # No params is neutral
    
    documented_count = 0
    total_weight = 0.0
    
    for param in parameters:
        param_weight = 1.0
        param_score = 0.0
        
        # Required params are more critical
        if param.get('required', False):
            param_weight = 2.0
        
        # Check name quality
        name = param.get('name', '')
        if name and len(name) > 1:
            param_score += 15.0
        
        # Check description
        desc = param.get('description', '') or param.get('desc', '') or ''
        if desc and len(desc) > 10:
            param_score += 25.0
        elif desc:
            param_score += 10.0
        
        # Check type specification
        if param.get('type'):
            param_score += 10.0
        
        # Check default value
        if 'default' in param:
            param_score += 5.0
        
        # Check enum values (indicates thorough documentation)
        if param.get('enum'):
            param_score += 5.0
        
        documented_count += param_score * param_weight
        total_weight += 30.0 * param_weight
    
    if total_weight == 0:
        return 50.0
    
    return min(100.0, (documented_count / total_weight) * 100.0)


def score_returns_documented(returns: Optional[Dict]) -> float:
    """
    Score return value documentation.
    """
    if not returns:
        return 15.0  # No return docs = low score
    
    score = 0.0
    
    # Check for return type
    if returns.get('type'):
        score += 30.0
    
    # Check for return description
    desc = returns.get('description', '') or returns.get('desc', '') or ''
    if desc and len(desc) > 10:
        score += 35.0
    elif desc:
        score += 15.0
    
    # Check for schema in return
    if returns.get('properties') or returns.get('schema'):
        score += 20.0
    
    return min(100.0, score)


def score_version_tag_present(metadata: Dict[str, Any]) -> float:
    """
    Score presence of version tag (professional indicator).
    """
    # Check various version indicators
    version_fields = ['version', 'ver', 'v', 'package_version', 'tool_version']
    
    for field in version_fields:
        if field in metadata:
            version = str(metadata[field])
            # Valid version format: x.y.z or x.y
            if re.match(r'^\d+\.\d+(\.\d+)?$', version):
                return 85.0
            elif re.match(r'^\d+\.\d+$', version):
                return 75.0
    
    # Check in description
    desc = metadata.get('description', '') or ''
    if re.search(r'version\s*[:\-]?\s*\d+', desc, re.IGNORECASE):
        return 60.0
    
    return 20.0  # No version = low score


def score_deprecation_notice(metadata: Dict[str, Any]) -> float:
    """
    Score deprecation status and handling.
    """
    deprecated = metadata.get('deprecated', False) or metadata.get('is_deprecated', False)
    desc = (metadata.get('description', '') or '').lower()
    
    if deprecated:
        # Check for migration guidance
        if any(word in desc for word in ['use', 'replace', 'migrate', 'instead', 'new']):
            return 70.0  # Deprecated but has migration path
        return 40.0  # Deprecated without guidance
    
    # Not deprecated - positive indicator
    if re.search(r'\bstable\b', desc):
        return 90.0
    if re.search(r'\bavailable\b', desc):
        return 85.0
    
    return 75.0  # Neutral active status


def score_schema_complexity(parameters: Optional[List[Dict]]) -> float:
    """
    Score schema complexity - overly complex or overly simple can be concerns.
    """
    if not parameters:
        return 40.0  # No parameters = moderate
    
    count = len(parameters)
    
    # Count parameter types
    required_count = sum(1 for p in parameters if p.get('required', False))
    optional_count = count - required_count
    
    # Calculate complexity score
    complexity = 0.0
    
    # Parameter count factor
    if count == 0:
        complexity += 20.0
    elif count <= 5:
        complexity += 60.0
    elif count <= 10:
        complexity += 75.0
    elif count <= 20:
        complexity += 70.0
    else:
        complexity += 50.0  # Very large schemas might indicate over-design
    
    # Required ratio factor
    if count > 0:
        required_ratio = required_count / count
        if 0.2 <= required_ratio <= 0.5:
            complexity += 25.0  # Healthy balance
        elif required_ratio > 0.7:
            complexity += 10.0  # Too many required
        elif required_ratio < 0.1:
            complexity += 15.0  # Too few required
    
    # Schema nesting
    nested_count = 0
    for param in parameters:
        param_type = str(param.get('type', '')).lower()
        if 'object' in param_type or 'properties' in param:
            nested_count += 1
        if 'array' in param_type:
            nested_count += 0.5
    
    if nested_count > 0:
        complexity += min(15.0, nested_count * 5.0)
    
    return min(100.0, complexity)


def score_dangerous_tools(tool_names: List[str]) -> float:
    """
    Score dangerous tool name presence.
    Returns high score if no dangerous patterns found.
    """
    if not tool_names:
        return 60.0  # No tools = neutral
    
    danger_hits = 0
    total_tools = len(tool_names)
    
    for name in tool_names:
        name_lower = name.lower()
        for pattern in DANGEROUS_TOOL_PATTERNS:
            if re.search(pattern, name_lower):
                danger_hits += 1
                break
    
    danger_ratio = danger_hits / total_tools
    
    # Calculate score: more dangerous tools = lower score
    if danger_ratio == 0:
        return 90.0
    elif danger_ratio < 0.1:
        return 75.0
    elif danger_ratio < 0.25:
        return 50.0
    elif danger_ratio < 0.5:
        return 30.0
    else:
        return 15.0  # Many dangerous tool names


def score_metadata_consistency(metadata: Dict[str, Any]) -> float:
    """
    Score internal consistency of metadata.
    """
    consistency_score = 60.0
    
    # Check for missing optional fields (can indicate incomplete docs)
    expected_fields = ['name', 'description', 'parameters']
    present_count = sum(1 for f in expected_fields if f in metadata and metadata[f])
    
    if present_count >= 3:
        consistency_score += 20.0
    elif present_count >= 2:
        consistency_score += 10.0
    else:
        consistency_score -= 20.0
    
    # Check for empty strings vs missing fields
    empty_count = sum(1 for v in metadata.values() if v == '' or v == [])
    if empty_count > 2:
        consistency_score -= 10.0
    
    # Check for malformed arrays/objects
    for key, value in metadata.items():
        if isinstance(value, list) and len(value) > 50:
            consistency_score -= 5.0  # Suspiciously large arrays
    
    return max(0.0, min(100.0, consistency_score))


def score_permission_documentation(metadata: Dict[str, Any]) -> float:
    """
    Score permission/scope documentation if present.
    """
    permission_fields = ['permissions', 'scopes', 'required_permissions', 'capabilities']
    
    for field in permission_fields:
        if field in metadata:
            perms = metadata[field]
            if isinstance(perms, list) and len(perms) > 0:
                return 80.0
            elif isinstance(perms, dict) and len(perms) > 0:
                return 75.0
            elif isinstance(perms, str) and len(perms) > 5:
                return 70.0
    
    return 40.0  # No permission info


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute tool_description_safety score with high discrimination.
    
    Reads:
    - tool_count: number of tools
    - average_schema_length: avg parameter count
    - has_examples: boolean or list
    - has_descriptions: boolean
    - parameter_docs_coverage: float 0-1
    - dangerous_tool_names: list of risky names
    - version_tag_present: boolean
    - deprecation_notice: boolean
    
    Produces >20 distinct score values through multi-dimensional scoring.
    
    Returns:
        Tuple of (score, evidence_dict)
    """
    
    # Extract data from metadata (handles multiple input formats)
    tool_names = metadata.get('tool_names', []) or metadata.get('tools', []) or []
    if not tool_names and 'tools' in metadata:
        if isinstance(metadata['tools'], list):
            tool_names = [t.get('name', '') for t in metadata['tools'] if isinstance(t, dict)]
    
    parameters = metadata.get('parameters', []) or metadata.get('params', [])
    description = metadata.get('description', '') or metadata.get('desc', '')
    
    # Compute individual dimension scores (0-100)
    scores = {
        'tool_name_safety': score_tool_name_safety(metadata.get('name', '')),
        'description_length': score_description_length(description),
        'description_clarity': score_description_clarity(description),
        'examples_quality': score_has_examples(metadata),
        'param_documentation': score_param_documented(parameters),
        'return_documentation': score_returns_documented(metadata.get('returns', {})),
        'version_tag': score_version_tag_present(metadata),
        'deprecation_status': score_deprecation_notice(metadata),
        'schema_complexity': score_schema_complexity(parameters),
        'dangerous_tool_presence': score_dangerous_tools(tool_names),
        'metadata_consistency': score_metadata_consistency(metadata),
        'permission_docs': score_permission_documentation(metadata),
    }
    
    # Calculate parameter coverage from individual params
    if parameters and isinstance(parameters, list):
        documented_params = sum(1 for p in parameters if p.get('description') or p.get('desc'))
        total_params = len(parameters)
        param_coverage = documented_params / total_params if total_params > 0 else 0.0
        scores['parameter_docs_coverage'] = param_coverage * 100.0
    else:
        param_coverage = metadata.get('parameter_docs_coverage', 0.5)
        scores['parameter_docs_coverage'] = param_coverage * 100.0
    
    # Has descriptions score
    has_desc = bool(description and len(str(description)) > 20)
    scores['has_descriptions'] = 85.0 if has_desc else 25.0
    
    # Has examples score (deduplicated from examples_quality)
    scores['has_examples'] = scores['examples_quality']
    
    # Tool count score
    tool_count = len(tool_names) or metadata.get('tool_count', 0)
    if tool_count == 0:
        scores['tool_count_score'] = 40.0
    elif tool_count <= 5:
        scores['tool_count_score'] = 70.0
    elif tool_count <= 20:
        scores['tool_count_score'] = 80.0
    else:
        scores['tool_count_score'] = 65.0  # Very large tool sets might be complex
    
    # Average schema length score
    avg_schema_len = metadata.get('average_schema_length', 0.0)
    if avg_schema_len == 0:
        scores['schema_length_score'] = 50.0
    elif avg_schema_len <= 3:
        scores['schema_length_score'] = 75.0  # Simple, good
    elif avg_schema_len <= 8:
        scores['schema_length_score'] = 85.0  # Moderate complexity
    elif avg_schema_len <= 15:
        scores['schema_length_score'] = 70.0  # Complex
    else:
        scores['schema_length_score'] = 45.0  # Very complex
    
    # Version tag present score
    version_present = scores['version_tag']
    scores['version_tag_present'] = version_present
    
    # Deprecation notice score
    deprecation_notice = scores['deprecation_status']
    scores['deprecation_notice'] = deprecation_notice
    
    # Weighted composite scoring
    weights = {
        'tool_name_safety': 1.0,
        'description_length': 0.8,
        'description_clarity': 0.7,
        'examples_quality': 0.9,
        'param_documentation': 1.0,
        'return_documentation': 0.6,
        'version_tag': 0.5,
        'deprecation_status': 0.4,
        'schema_complexity': 0.5,
        'dangerous_tool_presence': 0.8,
        'metadata_consistency': 0.6,
        'permission_docs': 0.4,
        'parameter_docs_coverage': 0.7,
        'has_descriptions': 0.5,
        'has_examples': 0.6,
        'tool_count_score': 0.3,
        'schema_length_score': 0.4,
        'version_tag_present': 0.3,
        'deprecation_notice': 0.2,
    }
    
    # Compute weighted sum
    weighted_sum = 0.0
    total_weight = 0.0
    
    for dimension, weight in weights.items():
        if dimension in scores:
            weighted_sum += scores[dimension] * weight
            total_weight += weight
    
    if total_weight == 0:
        composite_score = 50.0
    else:
        composite_score = weighted_sum / total_weight
    
    # Apply non-linear transformation for discrimination
    # Use sigmoid with different center points based on variance
    score_variance = sum((s - composite_score) ** 2 for s in scores.values()) / len(scores) if scores else 0
    variance_factor = math.sqrt(score_variance) / 50.0  # Normalize
    
    # If high variance across dimensions, adjust score
    if variance_factor > 0.3:
        # High variance - apply slight adjustment
        adjustment = min(5.0, variance_factor * 10.0)
        composite_score = composite_score + adjustment if composite_score > 50 else composite_score - adjustment
    
    # Final clamping
    final_score = max(0.0, min(MAX_SCORE, composite_score))
    
    # Round to 2 decimal places for cleaner output
    final_score = round(final_score, 2)
    
    # Build evidence dict with all dimension scores
    evidence = {
        'dimensions': {k: round(v, 2) for k, v in scores.items()},
        'weighted_composite': round(composite_score, 2),
        'variance_factor': round(variance_factor, 3),
        'discrimination_buckets': compute_discrimination_buckets(scores),
        'metadata_keys': list(metadata.keys())[:10],  # First 10 keys for debugging
        'signal_name': SIGNAL_NAME,
        'version': '2.0'
    }
    
    return (final_score, evidence)


def compute_discrimination_buckets(scores: Dict[str, float]) -> List[str]:
    """
    Compute which discrimination buckets this score falls into.
    Enables more granular categorization.
    """
    buckets = []
    
    # Name safety bucket
    if scores.get('tool_name_safety', 50) >= 80:
        buckets.append('safe_names')
    elif scores.get('tool_name_safety', 50) <= 40:
        buckets.append('risky_names')
    else:
        buckets.append('moderate_names')
    
    # Documentation quality bucket
    doc_score = (scores.get('description_length', 0) + scores.get('param_documentation', 0)) / 2
    if doc_score >= 70:
        buckets.append('well_documented')
    elif doc_score >= 40:
        buckets.append('partial_docs')
    else:
        buckets.append('poorly_documented')
    
    # Example presence bucket
    if scores.get('examples_quality', 0) >= 40:
        buckets.append('has_examples')
    else:
        buckets.append('no_examples')
    
    # Version status bucket
    if scores.get('version_tag', 50) >= 70:
        buckets.append('versioned')
    else:
        buckets.append('unversioned')
    
    return buckets


def compute_batch_scores(batch_metadata: List[Dict[str, Any]]) -> List[Tuple[float, Dict[str, Any]]]:
    """
    Compute scores for a batch of metadata.
    """
    results = []
    for metadata in batch_metadata:
        score, evidence = compute_score(metadata)
        results.append((score, evidence))
    return results


def get_score_band(score: float) -> str:
    """
    Classify score into risk bands.
    """
    if score >= 85:
        return 'EXCELLENT'
    elif score >= 70:
        return 'GOOD'
    elif score >= 55:
        return 'ACCEPTABLE'
    elif score >= 40:
        return 'FAIR'
    elif score >= 25:
        return 'POOR'
    else:
        return 'CRITICAL'


def run() -> None:
    """
    Standalone test runner.
    """
    test_cases = [
        {
            'name': 'excellent_mcp_server',
            'description': 'This MCP server provides comprehensive file system operations with proper documentation, examples, and version tracking.',
            'tool_count': 8,
            'average_schema_length': 5.5,
            'tools': [
                {'name': 'read_file', 'description': 'Read contents of a file', 'parameters': [{'name': 'path', 'type': 'string', 'description': 'File path to read'}]},
                {'name': 'write_file', 'description': 'Write content to a file', 'parameters': [{'name': 'path', 'type': 'string', 'description': 'Destination path'}, {'name': 'content', 'type': 'string', 'description': 'Content to write'}]},
            ],
            'version': '2.1.0',
            'examples': [{'input': {}, 'output': {}}],
            'parameters': [
                {'name': 'path', 'required': True, 'type': 'string', 'description': 'File path to operate on'},
                {'name': 'options', 'required': False, 'type': 'object', 'description': 'Optional configuration'}
            ]
        },
        {
            'name': 'minimal_mcp',
            'description': 'Tool',
            'tool_count': 1,
            'average_schema_length': 1.0
        },
        {
            'name': 'risky_exec_tool',
            'description': 'Execute shell commands on the target system with elevated privileges.',
            'tool_names': ['execute_shell_command', 'run_sudo_superuser', 'delete_all_files'],
            'tool_count': 3,
            'average_schema_length': 2.0
        },
        {
            'name': 'moderately_documented',
            'description': 'Provides database query capabilities with some documentation but missing examples.',
            'tools': [
                {'name': 'query', 'parameters': [{'name': 'sql'}]}
            ],
            'tool_count': 2,
            'average_schema_length': 3.0,
            'version': '1.0'
        }
    ]
    
    print(f"\n{SERVICE_NAME} - Discrimination Test")
    print("=" * 60)
    
    all_scores = []
    for metadata in test_cases:
        score, evidence = compute_score(metadata)
        band = get_score_band(score)
        all_scores.append(score)
        
        print(f"\n{metadata.get('name', 'unknown')}:")
        print(f"  Score: {score:.2f} ({band})")
        print(f"  Buckets: {evidence['discrimination_buckets']}")
        print(f"  Dimensions: {list(evidence['dimensions'].keys())}")
    
    # Check discrimination
    distinct_scores = len(set(round(s, 2) for s in all_scores))
    print(f"\nDistinct score values: {distinct_scores} (target: >20 for full coverage)")
    print(f"Score range: {min(all_scores):.2f} - {max(all_scores):.2f}")
    
    # Test with synthetic high-coverage batch
    print("\n" + "=" * 60)
    print("Synthetic batch test for discrimination coverage:")
    
    synthetic_batch = []
    for i in range(100):
        synthetic_batch.append({
            'name': f'tool_{i}',
            'description': f'Test description {i}' * (i % 10 + 1),
            'tool_count': i % 30 + 1,
            'average_schema_length': (i % 20) * 0.5 + 0.5,
            'version': f'{i % 5}.{i % 10}.{i % 3}' if i % 3 == 0 else None,
            'examples': [{'test': True}] if i % 4 == 0 else None,
            'parameters': [
                {'name': f'param_{j}', 'description': f'Param description {j}' if j % 2 == 0 else '', 'required': j % 3 == 0}
                for j in range(i % 10)
            ]
        })
    
    synthetic_scores = [compute_score(m)[0] for m in synthetic_batch]
    distinct_synthetic = len(set(round(s, 2) for s in synthetic_scores))
    print(f"Distinct scores in 100 synthetic samples: {distinct_synthetic}")


if __name__ == '__main__':
    run()