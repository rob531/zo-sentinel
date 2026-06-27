import psycopg2
from datetime import datetime, timedelta

def verify_mcp_submissions_population():
    try:
        # Connect to the write_service database
        conn = psycopg2.connect(
            dbname='your_database_name',
            user='your_username',
            password='your_password',
            host='your_host',
            port='your_port'
        )

        # Create a cursor object
        cur = conn.cursor()

        # Calculate the time 24 hours ago
        time_threshold = datetime.now() - timedelta(hours=24)

        # Query the mcp_submissions table for recent entries
        cur.execute("SELECT COUNT(*) FROM mcp_submissions WHERE created_at >= %s", (time_threshold,))

        # Fetch the result
        count = cur.fetchone()[0]

        # Close the cursor and connection
        cur.close()
        conn.close()

        # Check if there are any recent entries
        if count > 0:
            print("PASS: The mcp_submissions table contains recent entries.")
            return True
        else:
            print("FAIL: The mcp_submissions table has no recent entries.")
            return False

    except Exception as e:
        print(f"FAIL: An error occurred while verifying the mcp_submissions table: {e}")
        return False

if __name__ == "__main__":
    verify_mcp_submissions_population()