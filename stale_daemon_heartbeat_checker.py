# stale_daemon_heartbeat_checker.py

```python
# deps: requests
import logging
import requests
import sys

API_ENDPOINT = "http://127.0.0.1:8772/query"


def check_stale_daemons(stale_threshold_seconds: int = 7200) -> list[dict]:
    """
    Check for stale daemons in the service_health table.

    Args:
        stale_threshold_seconds: Number of seconds after which a daemon is
                                 considered stale. Defaults to 7200 (2 hours).

    Returns:
        List of dicts with daemon_id, last_heartbeat, and stale_seconds.
    """
    sql = """
    SELECT daemon_id, last_heartbeat, 
           EXTRACT(EPOCH FROM (NOW() - last_heartbeat))::INTEGER as stale_seconds
    FROM service_health
    WHERE last_heartbeat IS NOT NULL
      AND EXTRACT(EPOCH FROM (NOW() - last_heartbeat)) > %s
    ORDER BY stale_seconds DESC
    """

    try:
        response = requests.post(
            API_ENDPOINT,
            json={"sql": sql, "params": [stale_threshold_seconds]},
            timeout=30
        )
        response.raise_for_status()
        results = response.json()

        stale_daemons = []
        for row in results:
            daemon_info = {
                "daemon_id": row["daemon_id"],
                "last_heartbeat": row["last_heartbeat"],
                "stale_seconds": row["stale_seconds"]
            }
            stale_daemons.append(daemon_info)

            logging.warning(
                f"[STALE DAEMON] daemon_id={daemon_info['daemon_id']}, "
                f"last_heartbeat={daemon_info['last_heartbeat']}, "
                f"stale_by={daemon_info['stale_seconds']}s"
            )

        return stale_daemons

    except requests.exceptions.RequestException as e:
        logging.error(f"Database request failed: {e}")
        raise
    except (KeyError, ValueError, TypeError) as e:
        logging.error(f"Failed to parse response: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s"
    )
    try:
        stale = check_stale_daemons()
        print(f"Found {len(stale)} stale daemon(s)")
        for d in stale:
            print(f"  - {d['daemon_id']}: stale by {d['stale_seconds']}s")
        assert isinstance(stale, list)
        sys.exit(0)
    except Exception as e:
        logging.error(f"Check failed: {e}")
        sys.exit(1)
```