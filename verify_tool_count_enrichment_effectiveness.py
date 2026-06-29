import psycopg2
import numpy as np
from collections import defaultdict

def verify_tool_count_enrichment_effectiveness():
    # Connect to the database
    conn = psycopg2.connect(
        host="localhost",
        database="zo_sentinel",
        user="zo_admin",
        password="secure_password"
    )
    cursor = conn.cursor()

    # Query mcp_signal_scores for tool_count dimension
    cursor.execute("""
        SELECT tool_count, score
        FROM mcp_signal_scores
        WHERE dimension = 'tool_count'
    """)
    rows = cursor.fetchall()

    if not rows:
        print("No data found for tool_count dimension.")
        return

    # Extract scores and tool counts
    scores = [row[1] for row in rows]
    tool_counts = [row[0] for row in rows]

    # Calculate distinct score count and range
    distinct_scores = len(set(scores))
    score_range = max(scores) - min(scores)

    # Calculate score distribution
    score_distribution = defaultdict(int)
    for score in scores:
        score_distribution[score] += 1

    # Calculate basic statistics
    mean_score = np.mean(scores)
    median_score = np.median(scores)
    std_dev = np.std(scores)

    # Print results
    print("Tool Count Enrichment Effectiveness Verification:")
    print(f"Total records: {len(rows)}")
    print(f"Distinct scores: {distinct_scores}")
    print(f"Score range: {score_range:.2f}")
    print(f"Mean score: {mean_score:.2f}")
    print(f"Median score: {median_score:.2f}")
    print(f"Standard deviation: {std_dev:.2f}")
    print("\nScore Distribution:")
    for score, count in sorted(score_distribution.items()):
        print(f"Score {score}: {count} occurrences")

    # Check if enrichment is producing sufficient variety
    if distinct_scores < 10 or score_range < 20.0:
        print("\nWARNING: Low score variety detected. Enrichment may not be working effectively.")
    else:
        print("\nSUCCESS: Enrichment appears to be producing sufficient score variety.")

    # Close the connection
    cursor.close()
    conn.close()

if __name__ == "__main__":
    verify_tool_count_enrichment_effectiveness()