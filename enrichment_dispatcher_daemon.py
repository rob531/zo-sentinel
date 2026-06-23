#!/usr/bin/env python3
# community_signal_enrichment wiring — do not apply twice

"""
enrichment_dispatcher_daemon.py

Daemon wrapper that continuously dispatches enrichment batches to write_service
for scoring and DB write to mcp_signal_enrichments.
"""

import time
import signal
import sys
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
HEALTH_SERVICE_URL = "http://127.0.0.1:8772/health"
HEARTBEAT_INTERVAL = 60  # seconds
DISPATCH_INTERVAL = 30  # seconds between dispatch cycles

# Flag for graceful shutdown
_running = True


def signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for clean shutdown."""
    global _running
    logger.info("Received shutdown signal, finishing current cycle...")
    _running = False


def post_heartbeat() -> bool:
    """Post heartbeat to service_health. Returns True on success."""
    try:
        response = requests.post(
            HEALTH_SERVICE_URL,
            json={
                "service": "enrichment_dispatcher",
                "status": "alive",
                "timestamp": datetime.utcnow().isoformat()
            },
            timeout=5
        )
        if response.status_code in (200, 201):
            logger.info("heartbeat")
            return True
        else:
            logger.warning(f"Heartbeat returned status {response.status_code}")
            return False
    except requests.RequestException as e:
        logger.warning(f"Heartbeat failed: {e}")
        return False

# Legacy wrapper for backward compatibility
def heartbeat_loop() -> bool:
    """Legacy wrapper that forwards to post_heartbeat."""
    return post_heartbeat()

    """Post heartbeat to service_health. Returns True on success."""
    try:
        response = requests.post(
            HEALTH_SERVICE_URL,
            json={
                "service": "enrichment_dispatcher",
                "status": "alive",
                "timestamp": datetime.utcnow().isoformat()
            },
            timeout=5
        )
        if response.status_code in (200, 201):
            logger.info("heartbeat")
            return True
        else:
            logger.warning(f"Heartbeat returned status {response.status_code}")
            return False
    except requests.RequestException as e:
        logger.warning(f"Heartbeat failed: {e}")
        return False


def get_mcp_server_registry() -> List[Dict[str, Any]]:
    """Fetch the MCP server registry."""
    try:
        # In production, this would fetch from the registry service
        # For now, return empty list if service unavailable
        response = requests.get("http://127.0.0.1:8772/registry/mcp_servers", timeout=5)
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass
    return []


def get_enricher_registry() -> Dict[str, Any]:
    """Get the registered enrichers."""
    return {
        "domain_trust": {
            "enabled": True,
            "module": "domain_trust_enrichment"
        },
        "supply_chain": {
            "enabled": True,
            "module": "supply_chain_enrichment"
        },
        "community_signal": {
            "enabled": True,
            "module": "community_signal_enrichment"
        },
        "temporal_stability": {
            "enabled": True,
            "module": "temporal_stability_enrichment_v6"
        }
    }


def compute_domain_trust_score(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compute domain trust score from metadata."""
    try:
        from domain_trust_enrichment import compute_score
        score = compute_score(metadata)
        return {
            "signal_type": "domain_trust",
            "confidence": score.get("confidence", 0.5),
            "evidence_blob": score
        }
    except ImportError:
        # Fallback if module not available
        domain = metadata.get("domain", "")
        score = 0.5 if domain else 0.0
        return {
            "signal_type": "domain_trust",
            "confidence": score,
            "evidence_blob": {"domain": domain, "method": "fallback"}
        }
    except Exception as e:
        logger.warning(f"domain_trust scoring failed: {e}")
        return None


def compute_supply_chain_score(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compute supply chain score from metadata."""
    try:
        from supply_chain_enrichment import compute_score
        score = compute_score(metadata)
        return {
            "signal_type": "supply_chain",
            "confidence": score.get("confidence", 0.5),
            "evidence_blob": score
        }
    except ImportError:
        # Fallback if module not available
        dependencies = metadata.get("dependencies", [])
        score = min(0.9, len(dependencies) * 0.1)
        return {
            "signal_type": "supply_chain",
            "confidence": score,
            "evidence_blob": {"dependencies": dependencies, "method": "fallback"}
        }
    except Exception as e:
        logger.warning(f"supply_chain scoring failed: {e}")
        return None


def compute_community_signal_score(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compute community signal score from metadata."""
    try:
        from community_signal_enrichment import compute_score
        score = compute_score(metadata)
        return {
            "signal_type": "community_signal",
            "confidence": score.get("confidence", 0.5),
            "evidence_blob": score
        }
    except ImportError:
        # Fallback if module not available
        downloads = metadata.get("downloads", 0)
        score = min(0.9, downloads / 1000000)
        return {
            "signal_type": "community_signal",
            "confidence": score,
            "evidence_blob": {"downloads": downloads, "method": "fallback"}
        }
    except Exception as e:
        logger.warning(f"community_signal scoring failed: {e}")
        return None


def dispatch_enrichment(mcp_metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Dispatch enrichment for a single MCP's metadata through all enrichers."""
    results = []
    enrichers = get_enricher_registry()

    for enricher_name, enricher_config in enrichers.items():
        if not enricher_config.get("enabled", True):
            continue

        try:
            if enricher_name == "domain_trust":
                result = compute_domain_trust_score(mcp_metadata)
            elif enricher_name == "supply_chain":
                result = compute_supply_chain_score(mcp_metadata)
            elif enricher_name == "community_signal":
                result = compute_community_signal_score(mcp_metadata)
            else:
                continue

            if result:
                # Add MCP identifier to result
                result["mcp_id"] = mcp_metadata.get("id", "unknown")
                result["mcp_name"] = mcp_metadata.get("name", "unknown")
                result["timestamp"] = datetime.utcnow().isoformat()
                results.append(result)
        except Exception as e:
            logger.warning(f"Enricher {enricher_name} failed: {e}")

    return results


def write_results(results: List[Dict[str, Any]]) -> bool:
    """Write enrichment results via write_service."""
    if not results:
        return True

    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                "table": "mcp_signal_enrichments",
                "records": results
            },
            timeout=10
        )
        if response.status_code in (200, 201):
            logger.info(f"Wrote {len(results)} enrichment records")
            return True
        else:
            logger.warning(f"Write failed with status {response.status_code}")
            return False
    except requests.RequestException as e:
        logger.error(f"Write service error: {e}")
        return False


def dispatch_cycle() -> int:
    """Execute one dispatch cycle. Returns number of records written."""
    logger.info("Starting enrichment dispatch cycle")
    
    mcp_registry = get_mcp_server_registry()
    if not mcp_registry:
        logger.info("No MCP servers in registry, skipping cycle")
        return 0

    all_results = []
    for mcp in mcp_registry:
        results = dispatch_enrichment(mcp)
        all_results.extend(results)

    if all_results:
        write_results(all_results)

    logger.info(f"Dispatch cycle complete: {len(all_results)} results")
    return len(all_results)


def run():
    """Main daemon loop."""
    global _running

    # Register signal handlers for clean shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("enrichment_dispatcher_daemon starting")
    
    # Initial heartbeat
    post_heartbeat()

    last_heartbeat = time.time()
    last_dispatch = time.time()

    while _running:
        try:
            current_time = time.time()

            # Check if it's time for heartbeat
            if current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
                post_heartbeat()
                last_heartbeat = current_time

            # Check if it's time for dispatch cycle
            if current_time - last_dispatch >= DISPATCH_INTERVAL:
                dispatch_cycle()
                last_dispatch = current_time

            # Small sleep to prevent busy loop
            time.sleep(1)

        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            # Ensure heartbeat still fires even on failure
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                post_heartbeat()
                last_heartbeat = time.time()
            time.sleep(1)

    logger.info("enrichment_dispatcher_daemon shutting down gracefully")
    sys.exit(0)


if __name__ == '__main__':
    run()