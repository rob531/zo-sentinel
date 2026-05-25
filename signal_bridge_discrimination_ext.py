import logging
import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
SERVICE_NAME = "signal_bridge_discrimination_ext"
SERVICE_PORT = 8774

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(SERVICE_NAME)


def ws_write(table: str, rows: Dict[str, Any], wait: bool = True) -> Optional[Dict]:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": wait},
            timeout=30
        )
        response.raise_for_status()
        return response.json() if wait else None
    except Exception as e:
        log.error(f"ws_write failed for table {table}: {e}")
        return None


def ws_query(table: str, query: str, params: Optional[Dict] = None) -> Optional[List[Dict]]:
    try:
        response = requests.post(
            f"http://127.0.0.1:8772/query",
            json={"table": table, "query": query, "params": params or {}},
            timeout=30
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except Exception as e:
        log.error(f"ws_query failed for table {table}: {e}")
        return None


def ws_execute(sql: str, params: Optional[Dict] = None) -> Optional[Dict]:
    try:
        response = requests.post(
            f"http://127.0.0.1:8772/execute",
            json={"sql": sql, "params": params or {}},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return None


def send_heartbeat() -> None:
    try:
        ws_write("service_health", {
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")


@dataclass
class WeakSignalConfig:
    signal_name: str
    min_discrimination_threshold: int = 10
    table_name: str = "mcp_tool_analysis"
    column_name: str = ""


class SignalBridgeDiscriminationExt:

    WEAK_SIGNALS = [
        WeakSignalConfig(
            signal_name="permission_scope",
            column_name="permission_scope"
        ),
        WeakSignalConfig(
            signal_name="temporal_stability",
            column_name="temporal_stability"
        ),
        WeakSignalConfig(
            signal_name="tool_description_safety",
            column_name="tool_description_safety"
        ),
    ]

    def __init__(self):
        self.service_name = SERVICE_NAME
        self.service_port = SERVICE_PORT
        self.last_diagnostic_time = None
        log.info(f"{self.service_name} initialized")

    def get_signal_discrimination(self, signal_config: WeakSignalConfig) -> Dict[str, Any]:
        try:
            result = ws_query(
                signal_config.table_name,
                f"""
                SELECT DISTINCT {signal_config.column_name} as value
                FROM {signal_config.table_name}
                WHERE {signal_config.column_name} IS NOT NULL
                """
            )
            if result is None:
                return {
                    "signal": signal_config.signal_name,
                    "distinct_count": 0,
                    "distinct_values": [],
                    "discrimination_quality": "UNKNOWN",
                    "meets_threshold": False
                }
            
            distinct_values = [row.get("value") for row in result if row.get("value") is not None]
            distinct_count = len(distinct_values)
            meets_threshold = distinct_count >= signal_config.min_discrimination_threshold
            
            if distinct_count == 0:
                quality = "NO_DATA"
            elif distinct_count < 3:
                quality = "POOR"
            elif distinct_count < signal_config.min_discrimination_threshold:
                quality = "WEAK"
            else:
                quality = "GOOD"
            
            return {
                "signal": signal_config.signal_name,
                "distinct_count": distinct_count,
                "distinct_values": distinct_values[:50],
                "discrimination_quality": quality,
                "meets_threshold": meets_threshold
            }
        except Exception as e:
            log.error(f"Failed to get discrimination for {signal_config.signal_name}: {e}")
            return {
                "signal": signal_config.signal_name,
                "distinct_count": 0,
                "distinct_values": [],
                "discrimination_quality": "ERROR",
                "meets_threshold": False,
                "error": str(e)
            }

    def diagnose_signal_quality(self) -> List[Dict[str, Any]]:
        results = []
        for signal_config in self.WEAK_SIGNALS:
            discrimination = self.get_signal_discrimination(signal_config)
            results.append(discrimination)
            
            if not discrimination["meets_threshold"]:
                log.warning(
                    f"WEAK SIGNAL DETECTED: {discrimination['signal']} "
                    f"has only {discrimination['distinct_count']} distinct values "
                    f"(threshold: {signal_config.min_discrimination_threshold}) - "
                    f"provides poor discrimination"
                )
            
            self._write_discrimination_result(discrimination)
        
        self.last_diagnostic_time = datetime.now(timezone.utc)
        return results

    def _write_discrimination_result(self, discrimination: Dict[str, Any]) -> None:
        try:
            ws_write("signal_discrimination_metrics", {
                "signal_name": discrimination["signal"],
                "distinct_count": discrimination["distinct_count"],
                "discrimination_quality": discrimination["discrimination_quality"],
                "meets_threshold": discrimination["meets_threshold"],
                "diagnostic_timestamp": datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            log.error(f"Failed to write discrimination result: {e}")

    def get_weak_signals_summary(self) -> Dict[str, Any]:
        results = self.diagnose_signal_quality()
        weak_signals = [r for r in results if not r["meets_threshold"]]
        
        return {
            "total_signals_checked": len(results),
            "weak_signals_count": len(weak_signals),
            "weak_signals": [
                {
                    "signal": w["signal"],
                    "distinct_count": w["distinct_count"]
                }
                for w in weak_signals
            ],
            "diagnostic_time": self.last_diagnostic_time.isoformat() if self.last_diagnostic_time else None,
            "overall_status": "DEGRADED" if weak_signals else "HEALTHY"
        }

    def run(self) -> None:
        log.info(f"{self.service_name} starting main loop")
        
        while True:
            try:
                send_heartbeat()
                diagnostic_results = self.diagnose_signal_quality()
                
                if any(not r["meets_threshold"] for r in diagnostic_results):
                    log.warning(
                        f"Discrimination monitoring: {len([r for r in diagnostic_results if not r['meets_threshold']])} "
                        f"weak signals detected out of {len(diagnostic_results)} monitored"
                    )
                else:
                    log.info(f"All {len(diagnostic_results)} signals meet discrimination threshold")
                
            except Exception as e:
                log.error(f"Main loop error: {e}")
            
            time.sleep(60)


def run():
    service = SignalBridgeDiscriminationExt()
    service.run()


if __name__ == "__main__":
    run()