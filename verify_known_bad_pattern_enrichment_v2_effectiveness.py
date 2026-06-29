import psycopg2
from datetime import datetime

def verify_known_bad_pattern_enrichment_v2_effectiveness():
    # Database connection parameters
    db_params = {
        'host': 'localhost',
        'database': 'zo_sentinel',
        'user': 'zo_admin',
        'password': 'secure_password'
    }

    try:
        # Connect to the database
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()

        # Query to get all distinct scores for known_bad_pattern signal_type
        query = """
        SELECT DISTINCT score
        FROM mcp_signal_scores
        WHERE signal_type = 'known_bad_pattern'
        ORDER BY score;
        """
        cursor.execute(query)
        scores = cursor.fetchall()

        # Calculate distinct score count and range
        distinct_score_count = len(scores)
        if distinct_score_count < 2:
            score_range = 0.0
        else:
            score_range = scores[-1][0] - scores[0][0]

        # Verify effectiveness
        if distinct_score_count < 20 or score_range < 20.0:
            print(f"FAIL: known_bad_pattern_enrichment_v2.py is ineffective.")
            print(f"Distinct scores: {distinct_score_count} (required >= 20)")
            print(f"Score range: {score_range} (required >= 20.0)")
            return False
        else:
            print("PASS: known_bad_pattern_enrichment_v2.py improves signal discrimination.")
            print(f"Distinct scores: {distinct_score_count}")
            print(f"Score range: {score_range}")
            return True

    except psycopg2.Error as e:
        print(f"Database error: {e}")
        return False

    finally:
        if conn is not None:
            conn.close()

if __name__ == "__main__":
    verify_known_bad_pattern_enrichment_v2_effectiveness()