import math
from typing import Dict, List, Tuple

def compute_score(metadata: Dict) -> Tuple[float, Dict]:
    server_id = metadata['server_id']
    axis_scores = metadata['axis_scores']

    n_axes = len(axis_scores)
    if n_axes == 0:
        return 0.0, {
            'n_axes': 0,
            'coverage_pct': 0.0,
            'mean_p_top': 0.0,
            'variance_p_top': 0.0,
            'per_axis': {}
        }

    p_tops = [score['p_top'] for score in axis_scores]
    mean_p_top = sum(p_tops) / n_axes
    variance_p_top = sum((x - mean_p_top) ** 2 for x in p_tops) / n_axes

    coverage_pct = mean_p_top * 100

    per_axis = {}
    for score in axis_scores:
        axis_name = score['axis_name']
        p_top = score['p_top']
        probs = score['probs']

        entropy = 0.0
        for prob in probs.values():
            if prob > 0:
                entropy -= prob * math.log2(prob)

        per_axis[axis_name] = {
            'p_top': p_top,
            'entropy': entropy
        }

    confidence = coverage_pct

    evidence = {
        'n_axes': n_axes,
        'coverage_pct': coverage_pct,
        'mean_p_top': mean_p_top,
        'variance_p_top': variance_p_top,
        'per_axis': per_axis
    }

    return confidence, evidence

if __name__ == "__main__":
    # Test case 1: High confidence
    test_metadata_1 = {
        'server_id': 'server1',
        'axis_scores': [
            {'axis_name': 'axis1', 'p_top': 0.9, 'probs': {'label1': 0.9, 'label2': 0.1}},
            {'axis_name': 'axis2', 'p_top': 0.85, 'probs': {'label1': 0.85, 'label2': 0.15}},
            {'axis_name': 'axis3', 'p_top': 0.95, 'probs': {'label1': 0.95, 'label2': 0.05}}
        ]
    }

    # Test case 2: Medium confidence
    test_metadata_2 = {
        'server_id': 'server2',
        'axis_scores': [
            {'axis_name': 'axis1', 'p_top': 0.6, 'probs': {'label1': 0.6, 'label2': 0.4}},
            {'axis_name': 'axis2', 'p_top': 0.7, 'probs': {'label1': 0.7, 'label2': 0.3}},
            {'axis_name': 'axis3', 'p_top': 0.5, 'probs': {'label1': 0.5, 'label2': 0.5}}
        ]
    }

    # Test case 3: Low confidence
    test_metadata_3 = {
        'server_id': 'server3',
        'axis_scores': [
            {'axis_name': 'axis1', 'p_top': 0.3, 'probs': {'label1': 0.3, 'label2': 0.7}},
            {'axis_name': 'axis2', 'p_top': 0.4, 'probs': {'label1': 0.4, 'label2': 0.6}},
            {'axis_name': 'axis3', 'p_top': 0.2, 'probs': {'label1': 0.2, 'label2': 0.8}}
        ]
    }

    test_cases = [test_metadata_1, test_metadata_2, test_metadata_3]

    for i, metadata in enumerate(test_cases, 1):
        score, evidence = compute_score(metadata)
        assert 0 <= score <= 100, f"Test case {i} failed: score out of range"
        assert 'n_axes' in evidence, f"Test case {i} failed: missing n_axes"
        assert 'coverage_pct' in evidence, f"Test case {i} failed: missing coverage_pct"
        assert 'mean_p_top' in evidence, f"Test case {i} failed: missing mean_p_top"
        assert 'variance_p_top' in evidence, f"Test case {i} failed: missing variance_p_top"
        assert 'per_axis' in evidence, f"Test case {i} failed: missing per_axis"

    print("PASS")