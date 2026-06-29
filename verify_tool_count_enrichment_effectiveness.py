import psycopg2
import numpy as np

def verify_tool_count_enrichment_effectiveness():
    # Connect to the database
    conn = psycopg2.connect(
        dbname="your_db_name",
        user="your_db_user",
        password="your_db_password",
        host="your_db_host"
    )
    cur = conn.cursor()

    # Query mcp_signal_scores for tool_count dimension
    cur.execute("""
        SELECT tool_count_score
        FROM mcp_signal_scores
        WHERE dimension = 'tool_count'
    """)
    scores = [row[0] for row in cur.fetchall()]

    # Calculate distinct score count and range distribution
    distinct_scores = len(set(scores))
    score_range = max(scores) - min(scores)
    score_distribution = np.histogram(scores, bins=10)

    # Print results
    print(f"Distinct score count: {distinct_scores}")
    print(f"Score range: {score_range}")
    print("Score distribution:")
    for i, count in enumerate(score_distribution[0]):
        print(f"{score_distribution[1][i]:.1f}-{score_distribution[1][i+1]:.1f}: {count} scores")

    # Check if enrichment module is producing discriminating scores
    if distinct_scores < 10 or score_range < 10:
        print("WARNING: Low score variety detected. Enrichment module may not be working correctly.")
    else:
        print("Enrichment module appears to be producing discriminating scores.")

    # Close the database connection
    cur.close()
    conn.close()

if __name__ == "__main__":
    verify_tool_count_enrichment_effectiveness()