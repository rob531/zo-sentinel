import os
import sys
import time
import signal
import logging
import requests
from datetime import datetime, timezone
from typing import Dict, Any, Optional

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
ENRICHMENT_TABLE = "mcp_signal_enrichments"
ENRICHMENT_SOURCE = "tool_description_safety"

MIN_DISTINCT_SCORES = 20
LOG_DIR = os.environ.get("LOG_DIR", "/var/log/zo_sentinel")
LOG_FILE = os.path.join(LOG_DIR, "verify_tool_desc_enrichment.log")

class VerificationDaemon:
    def __init__(self):
        self.running = False
        self.logger = self._setup_logging()
        self.last_status: Optional[Dict[str, Any]] = None

    def _setup_logging(self) -> logging.Logger:
        os.makedirs(LOG_DIR, exist_ok=True)
        logger = logging.getLogger("verify_tool_desc_enrichment")
        logger.setLevel(logging.INFO)
        
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        return logger

    def _write_health(self, status: str, details: Dict[str, Any]) -> bool:
        try:
            payload = {
                "table": "service_health",
                "rows": {
                    "service": "verify_tool_description_enrichment",
                    "status": status,
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "details": str(details)
                },
                "wait": True
            }
            resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            self.logger.warning(f"Health write failed: {e}")
            return False

    def _query_distinct_scores(self) -> int:
        try:
            payload = {
                "table": ENRICHMENT_TABLE,
                "query": {
                    "SELECT": "DISTINCT score",
                    "WHERE": f"enrichment_type = '{ENRICHMENT_SOURCE}'",
                    "type": "select"
                },
                "wait": True
            }
            resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                rows = data.get("rows", [])
                return len(rows) if isinstance(rows, list) else 0
            return 0
        except Exception as e:
            self.logger.error(f"Query failed: {e}")
            return 0

    def _query_total_enriched(self) -> int:
        try:
            payload = {
                "table": ENRICHMENT_TABLE,
                "query": {
                    "SELECT": "COUNT(*)",
                    "WHERE": f"enrichment_type = '{ENRICHMENT_SOURCE}'",
                    "type": "select"
                },
                "wait": True
            }
            resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                rows = data.get("rows", [])
                if isinstance(rows, list) and len(rows) > 0:
                    return int(rows[0].get("count_star()", 0))
            return 0
        except Exception as e:
            self.logger.error(f"Count query failed: {e}")
            return 0

    def _check_enrichment_quality(self) -> Dict[str, Any]:
        distinct_scores = self._query_distinct_scores()
        total_enriched = self._query_total_enriched()
        
        quality_threshold = MIN_DISTINCT_SCORES
        signal_strength = "WEAK" if distinct_scores < quality_threshold else "STRONG"
        
        details = {
            "distinct_scores": distinct_scores,
            "total_enriched": total_enriched,
            "threshold": quality_threshold,
            "signal_strength": signal_strength,
            "quality_pct": min(100, int((distinct_scores / quality_threshold) * 100)) if quality_threshold > 0 else 0
        }
        
        return details

    def _log_verification_result(self, details: Dict[str, Any]):
        signal_strength = details.get("signal_strength", "UNKNOWN")
        distinct = details.get("distinct_scores", 0)
        total = details.get("total_enriched", 0)
        
        if signal_strength == "STRONG":
            self.logger.info(
                f"[PASS] tool_description_safety enrichment quality: {signal_strength} "
                f"({distinct} distinct scores from {total} records)"
            )
        else:
            self.logger.warning(
                f"[FAIL] tool_description_safety enrichment quality: {signal_strength} "
                f"({distinct} distinct scores, threshold={MIN_DISTINCT_SCORES}, "
                f"total_records={total})"
            )

    def _store_verification_result(self, details: Dict[str, Any]) -> bool:
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            payload = {
                "table": "enrichment_verification_log",
                "rows": {
                    "verification_type": "tool_description_safety_enrichment",
                    "timestamp": timestamp,
                    "distinct_scores": details.get("distinct_scores", 0),
                    "total_enriched": details.get("total_enriched", 0),
                    "threshold": details.get("threshold", MIN_DISTINCT_SCORES),
                    "signal_strength": details.get("signal_strength", "UNKNOWN"),
                    "quality_pct": details.get("quality_pct", 0),
                    "status": "PASS" if details.get("signal_strength") == "STRONG" else "FAIL"
                },
                "wait": True
            }
            resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            self.logger.warning(f"Verification log write failed: {e}")
            return False

    def verify(self) -> Dict[str, Any]:
        self.logger.info("Starting tool_description_safety enrichment verification...")
        
        details = self._check_enrichment_quality()
        self._log_verification_result(details)
        self._store_verification_result(details)
        
        self.last_status = details
        status = "healthy" if details.get("signal_strength") == "STRONG" else "degraded"
        self._write_health(status, details)
        
        self.logger.info(f"Verification complete: {details}")
        return details

    def run(self):
        self.running = True
        self.logger.info("Verification daemon starting...")
        
        def signal_handler(sig, frame):
            self.logger.info(f"Received signal {sig}, shutting down...")
            self.running = False
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        self.logger.info("Running initial verification...")
        self.verify()
        
        self.logger.info("Verification daemon running. Press Ctrl+C to stop.")
        while self.running:
            try:
                time.sleep(300)
                if self.running:
                    self.verify()
            except Exception as e:
                self.logger.error(f"Verification cycle error: {e}")
                time.sleep(60)
        
        self.logger.info("Verification daemon stopped.")

if __name__ == "__main__":
    daemon = VerificationDaemon()
    daemon.run()