import math
from typing import List, Dict, Tuple
from app.db import get_session
from app.models import McpLlmAxisScore

def compute_drift(server_id: str, axis_records: List[Dict]) -> Tuple[float, Dict]:
    if len(axis_records) < 2:
        return (0.0, {'window_count': len(axis_records)})

    # Group records by window (scored_at)
    windows = {}
    for record in axis_records:
        scored_at = record['scored_at']
        if scored_at not in windows:
            windows[scored_at] = []
        windows[scored_at].append(record)

    window_list = list(windows.values())
    window_count = len(window_list)

    if window_count < 2:
        return (0.0, {'window_count': window_count})

    # Calculate JSD between consecutive windows
    jsd_scores = []
    axis_deltas = {}
    flagged_axes = set()

    for i in range(1, window_count):
        prev_window = window_list[i-1]
        curr_window = window_list[i]

        # Aggregate probs by axis_name
        prev_probs = {}
        curr_probs = {}
        for record in prev_window:
            axis_name = record['axis_name']
            probs = record['probs']
            if axis_name not in prev_probs:
                prev_probs[axis_name] = [0.0] * 7
            for j in range(7):
                prev_probs[axis_name][j] += probs[j]

        for record in curr_window:
            axis_name = record['axis_name']
            probs = record['probs']
            if axis_name not in curr_probs:
                curr_probs[axis_name] = [0.0] * 7
            for j in range(7):
                curr_probs[axis_name][j] += probs[j]

        # Normalize probs
        for axis_name in prev_probs:
            total = sum(prev_probs[axis_name])
            if total > 0:
                prev_probs[axis_name] = [p / total for p in prev_probs[axis_name]]

        for axis_name in curr_probs:
            total = sum(curr_probs[axis_name])
            if total > 0:
                curr_probs[axis_name] = [p / total for p in curr_probs[axis_name]]

        # Calculate JSD for each axis
        for axis_name in prev_probs:
            if axis_name not in curr_probs:
                continue

            p = prev_probs[axis_name]
            q = curr_probs[axis_name]

            # Calculate M (average of p and q)
            m = [(p[i] + q[i]) / 2 for i in range(7)]

            # Calculate KL divergences
            kl_p_m = sum(p[i] * (math.log2(p[i] / m[i]) if p[i] > 0 and m[i] > 0 else 0) for i in range(7))
            kl_q_m = sum(q[i] * (math.log2(q[i] / m[i]) if q[i] > 0 and m[i] > 0 else 0) for i in range(7))

            # Calculate JSD
            jsd = (kl_p_m + kl_q_m) / 2
            jsd_scores.append(jsd)

            # Calculate delta for each axis
            delta = sum(abs(p[i] - q[i]) for i in range(7)) / 7
            axis_deltas[axis_name] = delta

            if delta > 0.20:
                flagged_axes.add(axis_name)

    # Calculate overall drift score (average JSD)
    drift_score = sum(jsd_scores) / len(jsd_scores) if jsd_scores else 0.0

    evidence = {
        'jsd_between_windows': jsd_scores,
        'axis_deltas': axis_deltas,
        'flagged_axes': list(flagged_axes),
        'window_count': window_count
    }

    return (drift_score, evidence)

if __name__ == '__main__':
    # Test case 1: Stable server with identical windows
    stable_records = [
        {'axis_name': 'axis1', 'probs': [0.5, 0.2, 0.1, 0.1, 0.05, 0.03, 0.02], 'scored_at': '2023-01-01'},
        {'axis_name': 'axis2', 'probs': [0.5, 0.2, 0.1, 0.1, 0.05, 0.03, 0.02], 'scored_at': '2023-01-01'},
        {'axis_name': 'axis1', 'probs': [0.5, 0.2, 0.1, 0.1, 0.05, 0.03, 0.02], 'scored_at': '2023-01-02'},
        {'axis_name': 'axis2', 'probs': [0.5, 0.2, 0.1, 0.1, 0.05, 0.03, 0.02], 'scored_at': '2023-01-02'},
    ]
    drift_score, _ = compute_drift('test-srv-drift', stable_records)
    assert drift_score < 0.05, f"Stable server drift score too high: {drift_score}"

    # Test case 2: Drifted server with significant probs shift
    drifted_records = [
        {'axis_name': 'axis1', 'probs': [0.5, 0.2, 0.1, 0.1, 0.05, 0.03, 0.02], 'scored_at': '2023-01-01'},
        {'axis_name': 'axis2', 'probs': [0.5, 0.2, 0.1, 0.1, 0.05, 0.03, 0.02], 'scored_at': '2023-01-01'},
        {'axis_name': 'axis1', 'probs': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.3], 'scored_at': '2023-01-02'},
        {'axis_name': 'axis2', 'probs': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.3], 'scored_at': '2023-01-02'},
    ]
    drift_score, _ = compute_drift('test-srv-drift', drifted_records)
    assert drift_score > 0.15, f"Drifted server drift score too low: {drift_score}"

    # Test case 3: Single-window server
    single_window_records = [
        {'axis_name': 'axis1', 'probs': [0.5, 0.2, 0.1, 0.1, 0.05, 0.03, 0.02], 'scored_at': '2023-01-01'},
        {'axis_name': 'axis2', 'probs': [0.5, 0.2, 0.1, 0.1, 0.05, 0.03, 0.02], 'scored_at': '2023-01-01'},
    ]
    drift_score, _ = compute_drift('test-srv-drift', single_window_records)
    assert drift_score == 0.0, f"Single-window server drift score not zero: {drift_score}"

    print("PASS")