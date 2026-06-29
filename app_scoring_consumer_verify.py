import json
from typing import Dict, Any
from write_service import query_scores

def verify_app_scoring_consumer() -> None:
    """
    Verification module for app_scoring_consumer.py.
    Validates the output from mcp_llm_axis_scores query.
    """
    try:
        # Query the scores from the write_service
        scores_data = query_scores()

        # Check if the response is valid JSON
        if not isinstance(scores_data, dict):
            raise ValueError("Invalid JSON response from query_scores")

        # Check for required keys and structure
        required_axes = [
            'axis_1', 'axis_2', 'axis_3', 'axis_4',
            'axis_5', 'axis_6', 'axis_7', 'overall_risk'
        ]

        if len(scores_data) != len(required_axes):
            raise ValueError(f"Expected {len(required_axes)} axes, got {len(scores_data)}")

        for axis in required_axes:
            if axis not in scores_data:
                raise ValueError(f"Missing axis: {axis}")

            axis_data = scores_data[axis]
            if not isinstance(axis_data, dict):
                raise ValueError(f"Axis {axis} should be a dictionary")

            if 'label' not in axis_data or 'p_top' not in axis_data:
                raise ValueError(f"Axis {axis} missing 'label' or 'p_top'")

        # Check risk_tier derivation
        overall_risk = scores_data['overall_risk']['p_top']
        if 'risk_tier' not in scores_data:
            raise ValueError("Missing 'risk_tier' in scores data")

        # Define risk tiers based on overall_risk thresholds
        if overall_risk < 0.2:
            expected_tier = "Low"
        elif 0.2 <= overall_risk < 0.5:
            expected_tier = "Medium"
        elif 0.5 <= overall_risk < 0.8:
            expected_tier = "High"
        else:
            expected_tier = "Critical"

        if scores_data['risk_tier'] != expected_tier:
            raise ValueError(
                f"risk_tier mismatch. Expected: {expected_tier}, Got: {scores_data['risk_tier']}"
            )

        # Check criteria_version
        if 'criteria_version' not in scores_data:
            raise ValueError("Missing 'criteria_version' in scores data")

        # Self-test
        self_test_scores = {
            'axis_1': {'label': 'test', 'p_top': 0.1},
            'axis_2': {'label': 'test', 'p_top': 0.1},
            'axis_3': {'label': 'test', 'p_top': 0.1},
            'axis_4': {'label': 'test', 'p_top': 0.1},
            'axis_5': {'label': 'test', 'p_top': 0.1},
            'axis_6': {'label': 'test', 'p_top': 0.1},
            'axis_7': {'label': 'test', 'p_top': 0.1},
            'overall_risk': {'label': 'test', 'p_top': 0.1},
            'risk_tier': 'Low',
            'criteria_version': '1.0'
        }

        assert len(self_test_scores) == 8, "Self-test failed: axis count != 8"
        assert self_test_scores['risk_tier'] == 'Low', "Self-test failed: risk_tier mismatch"

        print("PASS")

    except Exception as e:
        print(f"FAIL: {str(e)}")

if __name__ == "__main__":
    verify_app_scoring_consumer()