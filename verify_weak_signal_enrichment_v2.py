import psycopg2
from collections import defaultdict

def verify_weak_signal_enrichment():
    # Database connection parameters
    db_params = {
        'host': 'localhost',
        'database': 'zo_sentinel',
        'user': 'postgres',
        'password': 'your_password_here'
    }

    # Connect to the database
    conn = psycopg2.connect(**db_params)
    cursor = conn.cursor()

    # Query to get variety count per signal_type
    query = """
    SELECT signal_type, COUNT(DISTINCT score) as variety_count
    FROM mcp_signal_enrichments
    GROUP BY signal_type
    """

    cursor.execute(query)
    results = cursor.fetchall()

    # Check for signals with 2 or fewer distinct values
    weak_signals = []
    for signal_type, variety_count in results:
        if variety_count <= 2:
            weak_signals.append((signal_type, variety_count))

    # Close the database connection
    cursor.close()
    conn.close()

    # Report findings
    if weak_signals:
        print("Warning: The following signals have 2 or fewer distinct values:")
        for signal_type, variety_count in weak_signals:
            print(f"- {signal_type}: {variety_count} distinct values")
    else:
        print("All signals have sufficient score variety (more than 2 distinct values).")

    # Check specific enrichment modules
    module_results = {
        'known_bad_pattern_enrichment.py': check_module_distinctness('known_bad_pattern'),
        'tool_count_enrichment.py': check_module_distinctness('tool_count'),
        'temporal_stability_signal_enrichment.py': check_module_distinctness('temporal_stability')
    }

    for module, distinctness in module_results.items():
        print(f"\n{module} distinctness check:")
        if distinctness <= 2:
            print(f"Warning: Only {distinctness} distinct values found")
        else:
            print(f"OK: {distinctness} distinct values found")

def check_module_distinctness(signal_type):
    # Database connection parameters
    db_params = {
        'host': 'localhost',
        'database': 'zo_sentinel',
        'user': 'postgres',
        'password': 'your_password_here'
    }

    # Connect to the database
    conn = psycopg2.connect(**db_params)
    cursor = conn.cursor()

    # Query to get distinct score count for specific signal type
    query = """
    SELECT COUNT(DISTINCT score) as distinct_count
    FROM mcp_signal_enrichments
    WHERE signal_type = %s
    """

    cursor.execute(query, (signal_type,))
    result = cursor.fetchone()

    # Close the database connection
    cursor.close()
    conn.close()

    return result[0]

if __name__ == "__main__":
    verify_weak_signal_enrichment()