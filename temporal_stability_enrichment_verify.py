import requests
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("temporal_stability_verify")


class TemporalStabilityEnrichmentVerifier:
    def __init__(self, write_service_url: str = "http://127.0.0.1:8772"):
        self.write_service_url = write_service_url
        self.verification_results = {
            "timestamp": datetime.utcnow().isoformat(),
            "checks": [],
            "overall_status": "PASS",
            "wiring_directive": None
        }
    
    def _write_result(self, table: str, result: Dict[str, Any]) -> bool:
        try:
            payload = {
                "table": table,
                "rows": result,
                "wait": True
            }
            resp = requests.post(f"{self.write_service_url}/write", json=payload, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to write result: {e}")
            return False
    
    def _query_enrichments(self, signal_type: str, min_rows: int = 100) -> List[Dict]:
        try:
            resp = requests.get(
                "http://127.0.0.1:8772/query",
                params={
                    "q": f"SELECT DISTINCT score, COUNT(*) as cnt FROM mcp_signal_enrichments WHERE signal_type = '{signal_type}' GROUP BY score ORDER BY score"
                },
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json().get("data", [])
            return []
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return []
    
    def _check_source_file_exists(self, filepath: str) -> bool:
        import os
        base_path = "/home/workspace/zo_sentinel"
        full_path = os.path.join(base_path, filepath)
        return os.path.exists(full_path)
    
    def _parse_signal_analyser_for_integration(self) -> Dict[str, Any]:
        result = {
            "import_found": False,
            "compute_score_called": False,
            "persist_enrichment_found": False,
            "signal_type_registered": False
        }
        
        try:
            signal_analyser_path = "/home/workspace/zo_sentinel/signal_analyser.py"
            with open(signal_analyser_path, 'r') as f:
                content = f.read()
            
            result["import_found"] = "temporal_stability_enrichment_v2" in content
            result["compute_score_called"] = "temporal_stability_v2" in content and "compute_score" in content
            result["persist_enrichment_found"] = "mcp_signal_enrichments" in content
            result["signal_type_registered"] = "temporal_stability" in content
            
        except Exception as e:
            logger.error(f"Failed to parse signal_analyser.py: {e}")
        
        return result
    
    def check_source_file(self) -> Dict[str, Any]:
        check = {
            "test": "source_file_exists",
            "status": "FAIL",
            "details": {},
            "timestamp": datetime.utcnow().isoformat()
        }
        
        file_exists = self._check_source_file_exists("temporal_stability_enrichment_v2.py")
        check["details"]["file_exists"] = file_exists
        check["status"] = "PASS" if file_exists else "FAIL"
        
        if file_exists:
            try:
                with open("/home/workspace/zo_sentinel/temporal_stability_enrichment_v2.py", 'r') as f:
                    content = f.read()
                check["details"]["has_compute_score"] = "def compute_score" in content
                check["details"]["has_run_function"] = "def run" in content or "def cycle" in content
            except Exception as e:
                check["details"]["parse_error"] = str(e)
        
        self.verification_results["checks"].append(check)
        return check
    
    def check_signal_analyser_integration(self) -> Dict[str, Any]:
        check = {
            "test": "signal_analyser_integration",
            "status": "FAIL",
            "details": {},
            "timestamp": datetime.utcnow().isoformat()
        }
        
        integration = self._parse_signal_analyser_for_integration()
        check["details"]["integration_analysis"] = integration
        
        all_integrated = all([
            integration["import_found"],
            integration["compute_score_called"],
            integration["persist_enrichment_found"],
            integration["signal_type_registered"]
        ])
        
        check["status"] = "PASS" if all_integrated else "FAIL"
        
        if not all_integrated:
            self.verification_results["wiring_directive"] = {
                "action": "UPDATE signal_analyser.py",
                "reason": "temporal_stability_enrichment_v2 integration incomplete",
                "required_changes": [
                    "1. Add import: from temporal_stability_enrichment_v2 import TemporalStabilityEnricherV2",
                    "2. Instantiate enricher in signal_analyser init or as module-level singleton",
                    "3. Call enricher.compute_score(signal_context) during signal analysis pipeline",
                    "4. Persist result to mcp_signal_enrichments: INSERT INTO mcp_signal_enrichments (signal_id, signal_type, score, metadata, created_at) VALUES (?, 'temporal_stability', ?, ?, ?)",
                    "5. Register 'temporal_stability' in signal_type registry if applicable"
                ],
                "target_file": "signal_analyser.py",
                "dependency": "temporal_stability_enrichment_v2.py"
            }
        
        self.verification_results["checks"].append(check)
        return check
    
    def check_data_enrichments(self) -> Dict[str, Any]:
        check = {
            "test": "data_enrichments_exist",
            "status": "FAIL",
            "details": {},
            "timestamp": datetime.utcnow().isoformat()
        }
        
        enrichments = self._query_enrichments("temporal_stability")
        distinct_scores = [row.get("score") for row in enrichments if row.get("score") is not None]
        unique_count = len(set(distinct_scores))
        
        check["details"]["distinct_score_count"] = unique_count
        check["details"]["required_minimum"] = 20
        check["details"]["total_rows"] = len(enrichments)
        check["details"]["sample_scores"] = distinct_scores[:10] if distinct_scores else []
        
        check["status"] = "PASS" if unique_count >= 20 else "FAIL"
        
        if unique_count < 20:
            check["details"]["recommendation"] = f"Only {unique_count} distinct scores found. Expected >=20. Either: (1) Enrichment not running yet, or (2) Integration not wiring scores correctly."
        
        self.verification_results["checks"].append(check)
        return check
    
    def check_heartbeat(self) -> Dict[str, Any]:
        check = {
            "test": "heartbeat_reporting",
            "status": "PASS",
            "details": {},
            "timestamp": datetime.utcnow().isoformat()
        }
        
        try:
            result = {
                "service": "temporal_stability_verify",
                "last_heartbeat": datetime.utcnow().isoformat(),
                "checks_performed": len(self.verification_results["checks"]),
                "overall_status": self.verification_results["overall_status"]
            }
            self._write_result("service_health", result)
            check["details"]["heartbeat_sent"] = True
        except Exception as e:
            check["details"]["heartbeat_sent"] = False
            check["details"]["error"] = str(e)
        
        self.verification_results["checks"].append(check)
        return check
    
    def run_verification(self) -> Dict[str, Any]:
        logger.info("Starting temporal_stability_enrichment_v2 integration verification...")
        
        self.check_source_file()
        self.check_signal_analyser_integration()
        self.check_data_enrichments()
        self.check_heartbeat()
        
        failed_checks = [c for c in self.verification_results["checks"] if c["status"] == "FAIL"]
        if failed_checks:
            self.verification_results["overall_status"] = "FAIL"
        else:
            self.verification_results["overall_status"] = "PASS"
        
        self._write_result("verification_results", {
            "component": "temporal_stability_enrichment_v2",
            "status": self.verification_results["overall_status"],
            "failed_checks": len(failed_checks),
            "total_checks": len(self.verification_results["checks"]),
            "wiring_directive": self.verification_results.get("wiring_directive"),
            "timestamp": datetime.utcnow().isoformat()
        })
        
        logger.info(f"Verification complete: {self.verification_results['overall_status']}")
        logger.info(f"Failed checks: {len(failed_checks)}/{len(self.verification_results['checks'])}")
        
        if self.verification_results.get("wiring_directive"):
            logger.warning("WIRING DIRECTIVE REQUIRED:")
            logger.warning(json.dumps(self.verification_results["wiring_directive"], indent=2))
        
        return self.verification_results
    
    def get_wiring_directive(self) -> Optional[Dict[str, Any]]:
        return self.verification_results.get("wiring_directive")


def run():
    verifier = TemporalStabilityEnrichmentVerifier()
    results = verifier.run_verification()
    
    directive = verifier.get_wiring_directive()
    if directive:
        print("\n" + "="*80)
        print("WIRING DIRECTIVE REQUIRED")
        print("="*80)
        print(json.dumps(directive, indent=2))
        print("="*80 + "\n")
    
    return results


if __name__ == "__main__":
    results = run()
    sys.exit(0 if results["overall_status"] == "PASS" else 1)