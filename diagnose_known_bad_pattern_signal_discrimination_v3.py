import pandas as pd
import matplotlib.pyplot as plt
import sqlite3
from pathlib import Path

def analyze_known_bad_pattern_signal():
    # Connect to the database
    db_path = Path('mcp_data.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Query the known_bad_pattern signal scores
    query = """
    SELECT score, COUNT(*) as count
    FROM mcp_signal_scores
    WHERE signal_type = 'known_bad_pattern'
    GROUP BY score
    ORDER BY score
    """
    cursor.execute(query)
    results = cursor.fetchall()

    # Convert results to DataFrame
    df = pd.DataFrame(results, columns=['score', 'count'])

    # Print basic statistics
    print("Known Bad Pattern Signal Score Distribution:")
    print(df.describe())

    # Plot the distribution
    plt.figure(figsize=(10, 6))
    plt.bar(df['score'], df['count'], color='skyblue')
    plt.title('Distribution of Known Bad Pattern Signal Scores')
    plt.xlabel('Score Value')
    plt.ylabel('Count')
    plt.xticks(df['score'])
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    plt.show()

    # Check if scores are binary
    is_binary = len(df) == 2
    print(f"\nScores are binary: {is_binary}")

    # Close the database connection
    conn.close()

    # Load previous diagnostic results if available
    prev_diag_path = Path('investigate_known_bad_pattern_signal_discrimination_v2.py')
    if prev_diag_path.exists():
        print("\nPrevious diagnostic findings:")
        with open(prev_diag_path, 'r') as f:
            print(f.read())
    else:
        print("\nNo previous diagnostic files found.")

if __name__ == "__main__":
    analyze_known_bad_pattern_signal()