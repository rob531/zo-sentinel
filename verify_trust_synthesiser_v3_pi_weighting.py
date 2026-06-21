import unittest
from unittest.mock import patch, MagicMock
from trust_synthesiser_v3_pi_dimension import calculate_weighted_score
from write_service import get_db_connection

def verify_weighting() -> bool:
    # Mock the database connection and query
    with patch('write_service.get_db_connection') as mock_db:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Mock the query result for injection_resilience
        mock_cursor.fetchall.return_value = [
            (1, 0.85),  # (id, score)
            (2, 0.75),
            (3, 0.90)
        ]

        mock_db.return_value = mock_conn

        # Get the actual scores from the module
        actual_scores = calculate_weighted_score('injection_resilience')

        # Expected scores after applying weight and threshold
        expected_scores = {
            1: 1.6 * 0.85,
            2: 0.0,  # Below threshold
            3: 1.6 * 0.90
        }

        # Verify the scores
        for id, score in actual_scores.items():
            if id not in expected_scores or not abs(score - expected_scores[id]) < 1e-9:
                return False

        return True

if __name__ == "__main__":
    result = verify_weighting()
    assert result, "Weighting verification failed"
    print("PASS")