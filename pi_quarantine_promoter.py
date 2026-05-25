#!/usr/bin/env python3
"""
pi_quarantine_promoter.py
Mechanical mover daemon: moves approved quarantine items to pi_test_corpus table.
No review logic - all judgement done upstream by pi_quarantine_reviewer.py
"""

import os
import sys
import time
import json
import fcntl
import signal
import threading
from datetime import datetime
from pathlib import Path

import requests

# Constants
SERVICE_NAME = 'pi_quarantine_promoter'
REVIEW_APPROVED = '/home/workspace/zo_sentinel/pi_review/approved'
REVIEW_PROMOTED = '/home/workspace/zo_sentinel/pi_review/promoted'
REVIEW_LOG = '/home/workspace/zo_sentinel/pi_review/log.jsonl'
MALFORMED_DIR = '/home/workspace/zo_sentinel/pi_review/malformed'
LOCK_FILE = '/tmp/pi_quarantine_promoter.lock'
CYCLE_SECS = 600
HEARTBEAT_SECS = 60
MAX_PER_CYCLE = 500

shutdown_flag = False
start_time = datetime.utcnow()


def log(msg):
    """Emit timestamped log to stdout."""
    ts = datetime.utcnow().isoformat()
    print(f"[{ts}] {SERVICE_NAME}: {msg}", flush=True)


def ws_write(table, row):
    """Write row(s) to table via write_service HTTP API."""
    resp = requests.post(
        'http://127.0.0.1:8772/write',
        json={'table': table, 'rows': [row], 'wait': True},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def ws_query(sql):
    """Execute SELECT via write_service HTTP API."""
    resp = requests.post(
        'http://127.0.0.1:8772/query',
        json={'sql': sql},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql):
    """Execute DDL/DML via write_service HTTP API."""
    resp = requests.post(
        'http://127.0.0.1:8772/execute',
        json={'sql': sql},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def ensure_table():
    """Create pi_test_corpus table if it doesn't exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS pi_test_corpus (
        payload_id VARCHAR PRIMARY KEY,
        family VARCHAR,
        source VARCHAR,
        modalities VARCHAR,
        prompt_text TEXT,
        expected_detection BOOLEAN,
        severity VARCHAR,
        review_decision VARCHAR,
        review_confidence DOUBLE,
        review_reasoning TEXT,
        ingested_at TIMESTAMPTZ,
        promoted_at TIMESTAMPTZ DEFAULT now()
    )
    """
    try:
        ws_execute(sql)
        log("Ensured pi_test_corpus table exists")
    except Exception as e:
        log(f"Error creating table: {e}")
        raise


def check_duplicate(payload_id):
    """Return True if payload_id already exists in pi_test_corpus."""
    try:
        result = ws_query(f"SELECT 1 FROM pi_test_corpus WHERE payload_id = '{payload_id}' LIMIT 1")
        return result.get('count', 0) > 0
    except Exception as e:
        log(f"Duplicate check error: {e}")
        return False


def move_to_malformed(filepath):
    """Move a malformed file to the malformed/ subdirectory."""
    try:
        os.makedirs(MALFORMED_DIR, exist_ok=True)
        dest = os.path.join(MALFORMED_DIR, filepath.name)
        # Avoid overwrite collisions
        if os.path.exists(dest):
            base = filepath.stem
            ext = filepath.suffix
            counter = 1
            while os.path.exists(dest):
                dest = os.path.join(MALFORMED_DIR, f"{base}_{counter}{ext}")
                counter += 1
        os.rename(str(filepath), dest)
        log(f"Moved malformed file to: {dest}")
    except Exception as e:
        log(f"Error moving to malformed: {e}")


def promotion_cycle():
    """Process approved files and promote to pi_test_corpus."""
    promoted = 0
    skipped_duplicate = 0
    errors = 0
    cycle_start = time.time()

    try:
        approved_files = sorted(Path(REVIEW_APPROVED).glob('*.json'))[:MAX_PER_CYCLE]
    except Exception as e:
        log(f"Error listing approved files: {e}")
        return promoted, skipped_duplicate, errors

    if not approved_files:
        log("No approved files to process")
        return promoted, skipped_duplicate, errors

    log(f"Processing {len(approved_files)} approved files")

    for filepath in approved_files:
        if shutdown_flag:
            log("Shutdown requested, aborting cycle")
            break

        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log(f"Malformed JSON in {filepath.name}: {e}")
            move_to_malformed(filepath)
            errors += 1
            continue

        # Verify 'review' key exists with APPROVE decision
        if 'review' not in data:
            log(f"Missing 'review' key in {filepath.name}, treating as malformed")
            move_to_malformed(filepath)
            errors += 1
            continue

        review = data.get('review', {})
        if review.get('decision') != 'APPROVE':
            log(f"Invalid review decision '{review.get('decision')}' in {filepath.name}, treating as malformed")
            move_to_malformed(filepath)
            errors += 1
            continue

        # Extract content_hash as payload_id
        content_hash = data.get('content_hash')
        if not content_hash:
            log(f"Missing content_hash in {filepath.name}, treating as malformed")
            move_to_malformed(filepath)
            errors += 1
            continue

        # Check for duplicate before attempting write
        if check_duplicate(content_hash):
            log(f"Duplicate detected, skipping: {content_hash}")
            try:
                dest_name = f"{data.get('source_dataset', 'unknown')}__{content_hash}.json"
                dest = os.path.join(REVIEW_PROMOTED, dest_name)
                os.rename(str(filepath), dest)
            except Exception as e:
                log(f"Error moving duplicate file: {e}")
            log_to_file({
                'timestamp': datetime.utcnow().isoformat(),
                'hash': content_hash,
                'action': 'skip_duplicate',
                'source': filepath.name
            })
            skipped_duplicate += 1
            continue

        # Build row for pi_test_corpus
        categories = data.get('categories', [])
        family = categories[0] if categories else 'unknown'
        modalities_list = data.get('modalities', [])
        modalities_str = ','.join(modalities_list) if isinstance(modalities_list, list) else str(modalities_list)

        row = {
            'payload_id': content_hash,
            'family': family,
            'source': data.get('source_dataset', 'unknown'),
            'modalities': modalities_str,
            'prompt_text': data.get('prompt_text', ''),
            'expected_detection': data.get('expected_detection', False),
            'severity': data.get('severity', 'unknown'),
            'review_decision': 'APPROVE',
            'review_confidence': review.get('confidence', 0.0),
            'review_reasoning': review.get('reasoning', ''),
            'ingested_at': data.get('ingested_at', datetime.utcnow().isoformat())
        }

        # Write to pi_test_corpus
        try:
            ws_write('pi_test_corpus', row)
        except Exception as e:
            log(f"Write failed for {content_hash}: {e}")
            # Leave file in approved/ for retry next cycle
            log_to_file({
                'timestamp': datetime.utcnow().isoformat(),
                'hash': content_hash,
                'action': 'error',
                'error': str(e),
                'source': filepath.name
            })
            errors += 1
            continue

        # Move file to promoted/
        try:
            dest_name = f"{data.get('source_dataset', 'unknown')}__{content_hash}.json"
            dest = os.path.join(REVIEW_PROMOTED, dest_name)
            os.rename(str(filepath), dest)
            log(f"Promoted: {content_hash}")
        except Exception as e:
            log(f"Error moving promoted file: {e}")

        log_to_file({
            'timestamp': datetime.utcnow().isoformat(),
            'hash': content_hash,
            'action': 'promoted',
            'source': filepath.name
        })
        promoted += 1

    cycle_duration = time.time() - cycle_start

    # Stats to mesh_memory
    try:
        event = {
            'source': SERVICE_NAME,
            'event_type': 'pi_promote_cycle',
            'timestamp': datetime.utcnow().isoformat(),
            'promoted': promoted,
            'skipped_duplicate': skipped_duplicate,
            'errors': errors,
            'cycle_duration_sec': round(cycle_duration, 2)
        }
        requests.post('http://127.0.0.1:8782/ingest', json=event, timeout=10)
    except Exception as e:
        log(f"Stats to mesh_memory error: {e}")

    log(f"Cycle complete: promoted={promoted}, skipped_dup={skipped_duplicate}, errors={errors}, duration={cycle_duration:.2f}s")
    return promoted, skipped_duplicate, errors


def log_to_file(entry):
    """Append entry to REVIEW_LOG as JSONL."""
    try:
        os.makedirs(os.path.dirname(REVIEW_LOG), exist_ok=True)
        with open(REVIEW_LOG, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception as e:
        log(f"Error writing to review log: {e}")


def heartbeat_loop():
    """Send periodic heartbeat to service_health."""
    while not shutdown_flag:
        try:
            uptime = (datetime.utcnow() - start_time).total_seconds()
            ws_write('service_health', {
                'service': SERVICE_NAME,
                'last_heartbeat': datetime.utcnow().isoformat()
            })
            log(f"Heartbeat sent, uptime={uptime:.0f}s")
        except Exception as e:
            log(f"Heartbeat error: {e}")
        time.sleep(HEARTBEAT_SECS)


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_flag
    sig_name = signal.Signals(signum).name
    log(f"Received {sig_name}, initiating graceful shutdown...")
    shutdown_flag = True


def run():
    """Main daemon run loop."""
    global shutdown_flag, start_time
    start_time = datetime.utcnow()

    log(f"Starting {SERVICE_NAME}")

    # Ensure promoted/ directory exists
    os.makedirs(REVIEW_PROMOTED, exist_ok=True)

    # Ensure table exists
    ensure_table()

    # Start heartbeat thread
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    log(f"Entering main loop, cycle_interval={CYCLE_SECS}s, max_per_cycle={MAX_PER_CYCLE}")

    while not shutdown_flag:
        cycle_start = time.time()
        promotion_cycle()
        elapsed = time.time() - cycle_start
        sleep_time = max(1, CYCLE_SECS - elapsed)
        log(f"Sleeping {sleep_time:.0f}s until next cycle")
        # Use small increments to respect shutdown_flag quickly
        while sleep_time > 0 and not shutdown_flag:
            time.sleep(min(sleep_time, 5))
            sleep_time -= 5

    log(f"Shutdown complete, exiting")


if __name__ == '__main__':
    # Install signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Acquire exclusive lock
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError) as e:
        print(f"Failed to acquire lock: {e}", flush=True)
        sys.exit(1)

    try:
        run()
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
            # Clean up lock file on clean exit
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass