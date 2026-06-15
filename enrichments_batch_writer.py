#!/usr/bin/env python3
"""
enrichments_batch_writer.py

Daemon that batches enrichment results from existing enrichment modules and writes
them to the mcp_signal_enrichments table via write_service.

deps: requests
"""

import importlib
import json
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

READ_SERVICE_URL = "http://localhost:8080"
WRITE_SERVICE_URL = "http://localhost:8081"
SERVICE_NAME = "enrichments_batch_writer"

BATCH_SIZE = 50
POLL_INTERVAL = 300
HEARTBEAT_INTERVAL = 60
HTTP_TIMEOUT = 10
WRITE_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2

ENRICHMENT_MODULES = [
    "supply_chain_threat_enrichment",
    "community_signal_enrichment",
]


def get_mcps_batch(read_url: str, batch_size: int) -> list[dict]:
    """Fetch a batch of MCPs from mcp_server_registry ordered by first_seen."""
    params = {"limit": batch_size, "order_by": "first_seen"}
    resp = requests.get(
        f"{read_url}/mcp_server_registry",
        params=params,
        timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_existing_enrichments(read_url: str, mcp_ids: list[str], since: datetime) -> set[tuple[str, str]]:
    """Check which MCPs already have recent enrichments for idempotency."""
    if not mcp_ids:
        return set()
    params = {
        "mcp_server_ids": mcp_ids,
        "since": since.isoformat()
    }
    resp = requests.get(
        f"{read_url}/mcp_signal_enrichments",
        params=params,
        timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    existing = set()
    for row in resp.json().get("data", []):
        existing.add((row["mcp_server_id"], row["signal_type"]))
    return existing


def compute_score(module_name: str, metadata: dict) -> dict | None:
    """Dynamically import and call compute_score from an enrichment module."""
    try:
        module = importlib.import_module(module_name)
        result = module.compute_score(metadata)
        return result
    except Exception as e:
        logger.warning("compute_score failed for %s: %s", module_name, e)
        return None


def write_enrichment(write_url: str, enrichment: dict) -> bool:
    """POST a single enrichment row to write_service with retry logic."""
    retries = 0
    delay = 1
    while retries <= MAX_RETRIES:
        try:
            resp = requests.post(
                f"{write_url}/mcp_signal_enrichments",
                json=enrichment,
                timeout=WRITE_TIMEOUT
            )
            if resp.status_code >= 500:
                if retries < MAX_RETRIES:
                    logger.warning(
                        "write_service 5xx (%d), retry %d in %ds",
                        resp.status_code, retries, delay
                    )
                    time.sleep(delay)
                    delay *= RETRY_BACKOFF_BASE
                    retries += 1
                    continue
                else:
                    logger.error("write_service failed after %d retries: %d", MAX_RETRIES, resp.status_code)
                    return False
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            if retries < MAX_RETRIES:
                logger.warning("write_service error, retry %d in %ds: %s", retries, delay, e)
                time.sleep(delay)
                delay *= RETRY_BACKOFF_BASE
                retries += 1
            else:
                logger.error("write_service failed after %d retries: %s", MAX_RETRIES, e)
                return False
    return False


def send_heartbeat(service_url: str, service_name: str) -> None:
    """Send a heartbeat row to service_health."""
    payload = {
        "service_name": service_name,
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    try:
        resp = requests.post(
            f"{service_url}/service_health",
            json=payload,
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        logger.debug("Heartbeat sent to service_health")
    except requests.RequestException as e:
        logger.warning("Failed to send heartbeat: %s", e)


def run():
    """Main loop: poll every 300s, batch 50 MCPs per cycle, heartbeat every 60s."""
    last_heartbeat = 0
    last_poll = 0
    enrichments_written = 0

    logger.info("Starting %s daemon", SERVICE_NAME)

    while True:
        now = time.time()

        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            send_heartbeat(READ_SERVICE_URL, SERVICE_NAME)
            last_heartbeat = now

        if now - last_poll >= POLL_INTERVAL:
            logger.info("Starting enrichment batch cycle")
            cycle_start = datetime.now(timezone.utc)
            cutoff = cycle_start - timedelta(hours=24)

            try:
                mcps = get_mcps_batch(READ_SERVICE_URL, BATCH_SIZE)
                logger.info("Fetched %d MCPs from registry", len(mcps))
            except requests.RequestException as e:
                logger.error("Failed to fetch MCPs: %s", e)
                last_poll = now
                continue

            if mcps:
                mcp_ids = [m["id"] for m in mcps]
                try:
                    existing = get_existing_enrichments(READ_SERVICE_URL, mcp_ids, cutoff)
                except requests.RequestException as e:
                    logger.error("Failed to check existing enrichments: %s", e)
                    existing = set()

                for mcp in mcps:
                    mcp_id = mcp["id"]
                    metadata = mcp.get("metadata", {})

                    for module_name in ENRICHMENT_MODULES:
                        signal_type = module_name
                        key = (mcp_id, signal_type)

                        if key in existing:
                            logger.debug("Skipping %s/%s (recent enrichment exists)", mcp_id, signal_type)
                            continue

                        result = compute_score(module_name, metadata)
                        if result is None:
                            continue

                        enrichment = {
                            "mcp_server_id": mcp_id,
                            "signal_type": signal_type,
                            "score": result.get("score", 0),
                            "evidence_blob": result.get("evidence", {}),
                            "computed_at": datetime.now(timezone.utc).isoformat()
                        }

                        if write_enrichment(WRITE_SERVICE_URL, enrichment):
                            enrichments_written += 1
                            logger.info(
                                "Wrote enrichment for %s/%s (score=%s)",
                                mcp_id, signal_type, enrichment["score"]
                            )
                        else:
                            logger.error(
                                "Failed to write enrichment for %s/%s",
                                mcp_id, signal_type
                            )

            logger.info("Batch cycle complete. Total enrichments written: %d", enrichments_written)
            last_poll = now

        time.sleep(1)


if __name__ == "__main__":
    run()