import psycopg2
from datetime import datetime
import numpy as np

def verify_known_bad_pattern_enrichment_v2_effectiveness():
    # Database connection parameters
    db_params = {
        'host': 'zo-sentinel-db',
        'database': 'signal_analysis',
        'user': 'analyst',
        'password': 'securepassword'
    }

    try:
        # Connect to the database
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()

        # Query to get all known_bad_pattern scores
        query = """
        SELECT score
        FROM mcp_signal_scores
        WHERE signal_type = 'known_bad_pattern'
        """
        cursor.execute(query)
        scores = [row[0] for row in cursor.fetchall()]

        if not scores:
            print("No scores found for known_bad_pattern. Test failed.")
            return False

        # Calculate distinct scores and range
        distinct_scores = len(set(scores))
        score_range = max(scores) - min(scores)

        # Check the conditions
        if distinct_scores < 20 or score_range < 20.0:
            print(f"Effectiveness verification failed. Distinct scores: {distinct_scores}, Range: {score_range}")
            return False
        else:
            print(f"Effectiveness verified. Distinct scores: {distinct_scores}, Range: {score_range}")
            return True

    except Exception as e:
        print(f"Error during verification: {e}")
        return False
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    verify_known_bad_pattern_enrichment_v2_effectiveness()