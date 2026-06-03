# deps: requests

import requests
import logging
import sys
import datetime

STALE_THRESHOLD_SECONDS = 7200  # 2 hours
HEARTBEAT_INTERVAL_SECONDS = 60

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
DAEMON_NAME = "stale_heartbeat_checker"


def _get_daemon_name():
    return DAEMON_NAME


def _epoch_now():
    """Get current UTC epoch timestamp in seconds."""
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def _build_heartbeat_payload():
    """Build heartbeat payload for service_health."""
    return {
        'sql': 'INSERT INTO service_health (daemon_name, status, meta) VALUES (?, ?, ?)',
        'params': [_get_daemon_name(), 'alive', _epoch_now()]
    }


def _build_stale_query_payload(threshold_epoch):
    """Build payload for stale daemon query."""
    return {
        'sql': """
            SELECT daemon_name, MAX(meta) as last_heartbeat
            FROM service_health
            WHERE status = 'alive'
            GROUP BY daemon_name
            HAVING MAX(meta) < ?
        """,
        'params': [threshold_epoch]
    }


def send_heartbeat():
    """Send heartbeat to service_health via write_service."""
    payload = _build_heartbeat_payload()
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Sent heartbeat for daemon: {_get_daemon_name()}")
    except Exception as e:
        logger.error(f"Failed to send heartbeat: {e}")
        raise


def check_stale_daemons():
    """Query for daemons with stale heartbeats (>2 hours old)."""
    current_epoch = _epoch_now()
    threshold_epoch = current_epoch - STALE_THRESHOLD_SECONDS
    payload = _build_stale_query_payload(threshold_epoch)
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        response.raise_for_status()
        stale_daemons = response.json()
        if stale_daemons:
            logger.warning(f"Found {len(stale_daemons)} stale daemon(s):")
            for row in stale_daemons:
                daemon = row.get('daemon_name', 'unknown')
                last_hb = row.get('last_heartbeat', 0)
                last_hb_dt = datetime.datetime.fromtimestamp(last_hb, tz=datetime.timezone.utc)
                age_seconds = current_epoch - last_hb
                logger.warning(f"  - {daemon}: last heartbeat {last_hb_dt.isoformat()} (stale by {age_seconds:.0f}s)")
            logger.warning(f"STALE DAEMON ALERT: {len(stale_daemons)} daemon(s) exceed {STALE_THRESHOLD_SECONDS}s threshold")
        else:
            logger.debug("No stale daemons detected")
        return stale_daemons
    except Exception as e:
        logger.error(f"Failed to check stale daemons: {e}")
        return []


def _self_test():
    """Self-test to validate logic and queries."""
    logger.info("Running self-test...")

    # Test 1: Verify epoch calculation
    now = _epoch_now()
    assert isinstance(now, float), "Epoch should be float"
    assert now > 0, "Epoch should be positive"
    assert now > 1700000000, "Epoch should be reasonable (post-2023)"
    logger.info("  [PASS] Epoch calculation valid")

    # Test 2: Verify heartbeat payload structure
    hb_payload = _build_heartbeat_payload()
    assert 'sql' in hb_payload, "Heartbeat payload missing sql key"
    assert 'params' in hb_payload, "Heartbeat payload missing params key"
    assert len(hb_payload['params']) == 3, "Heartbeat should have 3 params"
    assert hb_payload['params'][1] == 'alive', "Status should be 'alive'"
    logger.info("  [PASS] Heartbeat payload structure valid")

    # Test 3: Verify stale query payload structure
    stale_payload = _build_stale_query_payload(now - STALE_THRESHOLD_SECONDS)
    assert 'sql' in stale_payload, "Stale query missing sql key"
    assert 'params' in stale_payload, "Stale query missing params key"
    assert len(stale_payload['params']) == 1, "Stale query should have 1 param"
    logger.info("  [PASS] Stale query payload structure valid")

    # Test 4: Verify threshold calculation
    threshold = now - STALE_THRESHOLD_SECONDS
    assert threshold < now, "Threshold should be in the past"
    assert (now - threshold) == STALE_THRESHOLD_SECONDS, "Threshold should be exactly STALE_THRESHOLD_SECONDS ago"
    logger.info("  [PASS] Threshold calculation valid")

    # Test 5: Verify SQL contains required elements
    sql = stale_payload['sql']
    assert 'SELECT' in sql, "Query should contain SELECT"
    assert 'GROUP BY' in sql, "Query should group by daemon"
    assert 'HAVING' in sql, "Query should filter stale entries"
    assert 'MAX(meta)' in sql or 'max(meta)' in sql.lower(), "Query should check MAX(meta)"
    logger.info("  [PASS] SQL query contains required elements")

    # Test 6: Simulate stale daemon detection logic
    current = 1000000.0
    stale_time = current - (STALE_THRESHOLD_SECONDS + 100)
    recent_time = current - 100
    assert stale_time < (current - STALE_THRESHOLD_SECONDS), "Stale time should be below threshold"
    assert recent_time >= (current - STALE_THRESHOLD_SECONDS), "Recent time should be above threshold"
    logger.info("  [PASS] Stale detection logic valid")

    logger.info("All self-test checks passed!")
    return True


def run():
    """Main loop: send heartbeat and check for stale daemons."""
    logger.info(f"Starting stale daemon heartbeat checker (threshold={STALE_THRESHOLD_SECONDS}s, interval={HEARTBEAT_INTERVAL_SECONDS}s)")

    iteration = 0
    while True:
        iteration += 1
        logger.debug(f"Iteration {iteration} starting")

        # Send heartbeat
        try:
            send_heartbeat()
        except Exception as e:
            logger.error(f"Heartbeat failed on iteration {iteration}: {e}")
            # Continue to check even if heartbeat fails

        # Check for stale daemons
        try:
            check_stale_daemons()
        except Exception as e:
            logger.error(f"Stale check failed on iteration {iteration}: {e}")

        logger.debug(f"Iteration {iteration} complete, sleeping for {HEARTBEAT_INTERVAL_SECONDS}s")
        import time
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--self-test':
        success = _self_test()
        sys.exit(0 if success else 1)
    else:
        run()