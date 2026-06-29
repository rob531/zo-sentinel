import psycopg2
from datetime import datetime

def verify_known_bad_pattern_enrichment_v2_effectiveness():
    # Database connection parameters
    db_params = {
        'host': 'your_db_host',
        'database': 'your_db_name',
        'user': 'your_db_user',
        'password': 'your_db_password'
    }

    try:
        # Connect to the database
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()

        # Query to get all distinct scores for known_bad_pattern signal
        query = """
        SELECT DISTINCT score
        FROM mcp_signal_scores
        WHERE signal_type = 'known_bad_pattern'
        """
        cursor.execute(query)
        scores = cursor.fetchall()

        # Calculate distinct score count and range
        distinct_scores = len(scores)
        if distinct_scores < 1:
            print("No scores found for known_bad_pattern signal.")
            return False

        score_values = [score[0] for score in scores]
        score_range = max(score_values) - min(score_values)

        # Check if the signal meets the criteria
        if distinct_scores < 20 or score_range < 20.0:
            print(f"Signal does not meet the criteria. Distinct scores: {distinct_scores}, Range: {score_range}")
            return False
        else:
            print(f"Signal meets the criteria. Distinct scores: {distinct_scores}, Range: {score_range}")
            return True

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"Error while connecting to PostgreSQL: {error}")
        return False

    finally:
        if conn is not None:
            conn.close()

# Run the verification
if __name__ == "__main__":
    result = verify_known_bad_pattern_enrichment_v2_effectiveness()
    if result:
        print("Verification passed. known_bad_pattern_enrichment_v2.py improves signal discrimination.")
    else:
        print("Verification failed. known_bad_pattern_enrichment_v2.py does not improve signal discrimination.")