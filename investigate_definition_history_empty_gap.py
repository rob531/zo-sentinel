import psycopg2
from psycopg2 import sql

def investigate_definition_history_empty_gap():
    # Connect to the database
    conn = psycopg2.connect(
        dbname="your_db_name",
        user="your_db_user",
        password="your_db_password",
        host="your_db_host"
    )

    # Create a cursor object
    cur = conn.cursor()

    # Check if mcp_definition_history table is empty
    cur.execute("SELECT COUNT(*) FROM mcp_definition_history;")
    count = cur.fetchone()[0]

    if count == 0:
        # Check if the daemon responsible for populating mcp_definition_history is running
        cur.execute("SELECT * FROM mcp_server_registry WHERE daemon_name = 'definition_history_daemon';")
        daemon_info = cur.fetchone()

        if daemon_info is None:
            print("Integration Gap: The daemon responsible for populating mcp_definition_history is not registered in mcp_server_registry.")
        else:
            # Check if the daemon is healthy
            cur.execute("SELECT is_healthy FROM mcp_server_registry WHERE daemon_name = 'definition_history_daemon';")
            is_healthy = cur.fetchone()[0]

            if not is_healthy:
                print("Integration Gap: The daemon responsible for populating mcp_definition_history is registered but not healthy.")
            else:
                print("Integration Gap: The daemon responsible for populating mcp_definition_history is registered and healthy, but the table is still empty.")
    else:
        print("No integration gap found. The mcp_definition_history table is not empty.")

    # Close the cursor and connection
    cur.close()
    conn.close()

investigate_definition_history_empty_gap()