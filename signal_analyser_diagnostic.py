import logging
import time
import json
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('signal_analyser_diagnostic')


class SignalAnalyserDiagnostic:
    def __init__(self):
        self.write_service_url = 'http://127.0.0.1:8772/write'
        self.signal_analyser_name = 'signal_analyser'
        self.trust_synthesiser_name = 'trust_synthesiser'
        self.interval = 60

    def query_last_heartbeat(self, service_name: str) -> Optional[str]:
        """Query write_service for last heartbeat of a service."""
        try:
            query = {
                'table': 'service_health',
                'query': {
                    'service': service_name
                },
                'order_by': 'last_heartbeat:desc',
                'limit': 1
            }
            response = requests.post(
                'http://127.0.0.1:8772/query',
                json=query,
                timeout=5
            )
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0].get('last_heartbeat')
        except Exception as e:
            logger.warning(f"Failed to query heartbeat for {service_name}: {e}")
        return None

    def query_last_signal_score(self) -> Optional[str]:
        """Query mcp_signal_scores for most recent entry timestamp."""
        try:
            query = {
                'table': 'mcp_signal_scores',
                'order_by': 'created_at:desc',
                'limit': 1
            }
            response = requests.post(
                'http://127.0.0.1:8772/query',
                json=query,
                timeout=5
            )
            if response.status_code == 200:
                results = response.json().get('results', [])
                if results:
                    return results[0].get('created_at')
        except Exception as e:
            logger.warning(f"Failed to query signal scores: {e}")
        return None

    def emit_diagnostic(self, diagnostic_data: Dict[str, Any]):
        """Emit diagnostic blob to signal_diagnostics table."""
        try:
            payload = {
                'table': 'signal_diagnostics',
                'rows': {
                    'daemon_name': diagnostic_data['daemon_name'],
                    'last_heartbeat_ts': diagnostic_data['last_heartbeat_ts'],
                    'last_work_ts': diagnostic_data['last_work_ts'],
                    'diagnostic_blob': json.dumps(diagnostic_data),
                    'created_at': datetime.now(timezone.utc).isoformat()
                },
                'wait': True
            }
            response = requests.post(self.write_service_url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"Diagnostic emitted: {diagnostic_data['daemon_name']}")
            else:
                logger.error(f"Failed to emit diagnostic: {response.status_code}")
        except Exception as e:
            logger.error(f"Error emitting diagnostic: {e}")

    def diagnose(self):
        """Perform diagnostic check on signal_analyser."""
        logger.info("Starting signal_analyser diagnostic check")

        sa_heartbeat = self.query_last_heartbeat(self.signal_analyser_name)
        ts_heartbeat = self.query_last_heartbeat(self.trust_synthesiser_name)
        last_signal_ts = self.query_last_signal_score()

        diagnostic_data = {
            'daemon_name': 'signal_analyser_diagnostic',
            'last_heartbeat_ts': sa_heartbeat or 'NONE',
            'last_work_ts': last_signal_ts or 'NONE',
            'trust_synthesiser_heartbeat': ts_heartbeat or 'NONE',
            'signal_scores_last_entry': last_signal_ts or 'NONE',
            'stale_analysis': {
                'signal_analyser_stale': sa_heartbeat is None or self.is_stale(sa_heartbeat),
                'downstream_affected': ts_heartbeat is None or self.is_stale(ts_heartbeat)
            },
            'checked_at': datetime.now(timezone.utc).isoformat()
        }

        self.emit_diagnostic(diagnostic_data)
        logger.info(f"Diagnostic complete: {diagnostic_data}")
        return diagnostic_data

    def is_stale(self, timestamp_str: Optional[str]) -> bool:
        """Check if timestamp is stale (>1 hour ago)."""
        if not timestamp_str:
            return True
        try:
            if isinstance(timestamp_str, str):
                dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                dt = timestamp_str
            now = datetime.now(timezone.utc)
            age_seconds = (now - dt).total_seconds()
            return age_seconds > 3600
        except Exception:
            return True

    def run(self):
        """Main run loop."""
        logger.info("Signal Analyser Diagnostic daemon starting")
        while True:
            try:
                self.diagnose()
            except Exception as e:
                logger.error(f"Diagnostic error: {e}")
            time.sleep(self.interval)


if __name__ == '__main__':
    run()