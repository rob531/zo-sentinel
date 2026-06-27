import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
import logging

# Database specific import - using psycopg2 for PostgreSQL as a common example.
# If you are using a different database (e.g., MySQL, SQL Server),
# you will need to replace 'psycopg2' with the appropriate driver.
try:
    import psycopg2
    from psycopg2 import Error
except ImportError:
    print("Error: psycopg2 not found. Please install it using 'pip install psycopg2-binary'")
    sys.exit(1)

# --- Configuration Constants ---
DEFAULT_SLA_HOURS = 1.0  # Default SLA threshold: 1 hour
DEFAULT_LOOKBACK_HOURS = 24.0 # Default lookback period: 24 hours
DB_HOST_ENV = 'MCP_DB_HOST'
DB_NAME_ENV = 'MCP_DB_NAME'
DB_USER_ENV = 'MCP_DB_USER'
DB_PASSWORD_ENV = 'MCP_DB_PASSWORD'
DB_PORT_ENV = 'MCP_DB_PORT'

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_db_connection(host, dbname, user, password, port):
    """
    Establishes a database connection using psycopg2.
    Exits the script if connection fails.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=host,
            database=dbname,
            user=user,
            password=password,
            port=port
        )
        logger.info("Successfully connected to the database.")
        return conn
    except Error as e:
        logger.error(f"Error connecting to the database: {e}")
        sys.exit(1) # Exit with an error code if DB connection fails

def verify_verdict_freshness(conn, sla_threshold: timedelta, lookback_period: timedelta):
    """
    Verifies MCP verdict freshness against the defined SLA.
    It queries mcp_submissions and mcp_risk_register tables,
    calculates the time difference between submission and verdict,
    and reports any violations.

    Assumed table schemas:
    - mcp_submissions:
        - submission_id (PRIMARY KEY, e.g., UUID or INT)
        - submission_timestamp (TIMESTAMP WITH TIME ZONE, when the submission was made)
        - ... other columns ...
    - mcp_risk_register:
        - risk_register_id (PRIMARY KEY)
        - submission_id (FOREIGN KEY to mcp_submissions.submission_id)
        - verdict_timestamp (TIMESTAMP WITH TIME ZONE, when the verdict was issued)
        - ... other columns ...

    The script assumes that for a given submission_id, the 'verdict_timestamp'
    in mcp_risk_register represents the time the verdict was first issued.
    If multiple verdicts can exist for a single submission, it takes the
    earliest verdict_timestamp to check the initial SLA.
    """
    violations = []
    total_checked = 0

    # Calculate the start time for the lookback period in UTC
    now_utc = datetime.now(timezone.utc)
    lookback_start_time = now_utc - lookback_period

    logger.info(f"Checking MCP verdicts generated since: {lookback_start_time.isoformat()}")
    logger.info(f"SLA threshold for verdict generation: {sla_threshold}")

    # SQL query to fetch relevant data.
    # We use a CTE (Common Table Expression) to find the earliest verdict timestamp
    # for each submission within the lookback period.
    query = """
    WITH EarliestVerdicts AS (
        SELECT
            submission_id,
            MIN(verdict_timestamp) AS first_verdict_timestamp
        FROM
            mcp_risk_register
        WHERE
            verdict_timestamp >= %s -- Filter verdicts within the lookback period
            AND verdict_timestamp IS NOT NULL
        GROUP BY
            submission_id
    )
    SELECT
        ms.submission_id,
        ms.submission_timestamp,
        ev.first_verdict_timestamp
    FROM
        mcp_submissions ms
    JOIN
        EarliestVerdicts ev ON ms.submission_id = ev.submission_id
    WHERE
        ms.submission_timestamp IS NOT NULL
    ORDER BY
        ms.submission_id;
    """

    try:
        with conn.cursor() as cur:
            cur.execute(query, (lookback_start_time,))
            records = cur.fetchall()

            if not records:
                logger.info("No MCP verdicts found within the specified lookback period to check.")
                return 0, 0 # No violations, 0 checked

            for submission_id, submission_ts, verdict_ts in records:
                total_checked += 1

                # Ensure timestamps are timezone-aware for correct comparison.
                # psycopg2 typically returns timezone-aware datetimes if the DB column is TIMESTAMPTZ.
                # If they are naive (e.g., from TIMESTAMP WITHOUT TIME ZONE), assume UTC.
                if submission_ts.tzinfo is None:
                    submission_ts = submission_ts.replace(tzinfo=timezone.utc)
                if verdict_ts.tzinfo is None:
                    verdict_ts = verdict_ts.replace(tzinfo=timezone.utc)

                time_diff = verdict_ts - submission_ts

                if time_diff > sla_threshold:
                    violations.append({
                        "submission_id": submission_id,
                        "submission_timestamp": submission_ts.isoformat(),
                        "verdict_timestamp": verdict_ts.isoformat(),
                        "time_taken": str(time_diff),
                        "sla_threshold": str(sla_threshold)
                    })
                    logger.warning(
                        f"SLA VIOLATION for submission_id={submission_id}: "
                        f"Submission at {submission_ts.isoformat()}, "
                        f"Verdict at {verdict_ts.isoformat()}. "
                        f"Time taken: {time_diff} (exceeds SLA of {sla_threshold})"
                    )
                else:
                    logger.debug(
                        f"Submission_id={submission_id}: OK. "
                        f"Time taken: {time_diff} (within SLA of {sla_threshold})"
                    )

    except Error as e:
        logger.error(f"Error executing database query: {e}")
        # Return current state of violations and checked count, but indicate a failure
        return len(violations), total_checked, True # Added a flag for query error

    return len(violations), total_checked, False # No query error

def main():
    parser = argparse.ArgumentParser(
        description="Verify MCP verdict freshness against Service Level Agreements (SLAs).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--db-host",
        default=os.getenv(DB_HOST_ENV, "localhost"),
        help=f"Database host. Defaults to environment variable {DB_HOST_ENV} or 'localhost'."
    )
    parser.add_argument(
        "--db-name",
        default=os.getenv(DB_NAME_ENV, "mcp_db"),
        help=f"Database name. Defaults to environment variable {DB_NAME_ENV} or 'mcp_db'."
    )
    parser.add_argument(
        "--db-user",
        default=os.getenv(DB_USER_ENV, "mcp_user"),
        help=f"Database user. Defaults to environment variable {DB_USER_ENV} or 'mcp_user'."
    )
    parser.add_argument(
        "--db-password",
        default=os.getenv(DB_PASSWORD_ENV, "password"),
        help=f"Database password. Defaults to environment variable {DB_PASSWORD_ENV} or 'password'."
    )
    parser.add_argument(
        "--db-port",
        type=int,
        default=int(os.getenv(DB_PORT_ENV, 5432)),
        help=f"Database port. Defaults to environment variable {DB_PORT_ENV} or 5432."
    )
    parser.add_argument(
        "--sla-hours",
        type=float,
        default=DEFAULT_SLA_HOURS,
        help="Service Level Agreement threshold in hours for verdict generation."
    )
    parser.add_argument(
        "--lookback-hours",
        type=float,
        default=DEFAULT_LOOKBACK_HOURS,
        help="Number of hours to look back for verdicts to check. Only verdicts with a "
             "verdict_timestamp within this period will be considered."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (DEBUG level)."
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    sla_threshold = timedelta(hours=args.sla_hours)
    lookback_period = timedelta(hours=args.lookback_hours)

    conn = None
    try:
        conn = get_db_connection(
            args.db_host, args.db_name, args.db_user, args.db_password, args.db_port
        )
        # get_db_connection exits if connection fails, so conn will not be None here if successful.

        num_violations, total_checked, query_error = verify_verdict_freshness(conn, sla_threshold, lookback_period)

        logger.info(f"\n--- MCP Verdict Freshness Check Summary ---")
        logger.info(f"Total verdicts checked: {total_checked}")
        logger.info(f"SLA threshold: {sla_threshold}")

        if query_error:
            logger.error("FAIL: An error occurred during database query execution. Check logs for details.")
            sys.exit(1)
        elif num_violations > 0:
            logger.error(f"FAIL: Found {num_violations} SLA violation(s) out of {total_checked} verdicts checked.")
            sys.exit(1)
        elif total_checked == 0:
            logger.info("No verdicts found to check within the specified lookback period. Exiting successfully.")
            sys.exit(0)
        else:
            logger.info(f"SUCCESS: All {total_checked} verdicts checked are within the SLA.")
            sys.exit(0)

    finally:
        if conn:
            conn.close()
            logger.info("Database connection closed.")

if __name__ == "__main__":
    main()