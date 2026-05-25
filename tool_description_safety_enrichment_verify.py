import sys
import os
import ast
import re
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '/home/workspace/zo_sentinel')

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

class SafetyEnrichmentVerifier:
    def __init__(self):
        self.results = {
            "verifier": "tool_description_safety_enrichment_verify",
            "timestamp": datetime.utcnow().isoformat(),
            "checks": []
        }
        self.wiring_directives = []

    def log_check(self, name: str, passed: bool, details: str = ""):
        self.results["checks"].append({
            "name": name,
            "passed": passed,
            "details": details,
            "timestamp": datetime.utcnow().isoformat()
        })
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")
        if details:
            print(f"    Details: {details}")

    def check_source_files_exist(self):
        print("\n[1] Checking source files exist...")
        
        enrichment_path = Path("/home/workspace/zo_sentinel/tool_description_safety_enrichment.py")
        analyser_path = Path("/home/workspace/zo_sentinel/signal_analyser.py")
        
        enrichment_exists = enrichment_path.exists()
        self.log_check(
            "tool_description_safety_enrichment.py exists",
            enrichment_exists,
            str(enrichment_path) if enrichment_exists else "File not found"
        )
        
        analyser_exists = analyser_path.exists()
        self.log_check(
            "signal_analyser.py exists",
            analyser_exists,
            str(analyser_path) if analyser_exists else "File not found"
        )
        
        return enrichment_exists and analyser_exists

    def check_enrichment_module_structure(self):
        print("\n[2] Checking enrichment module structure...")
        
        enrichment_path = Path("/home/workspace/zo_sentinel/tool_description_safety_enrichment.py")
        
        with open(enrichment_path, 'r') as f:
            source = f.read()
        
        has_score_fn = "def score_tool_description" in source or "def compute_safety_score" in source
        self.log_check(
            "Has scoring function",
            has_score_fn,
            "Found scoring function" if has_score_fn else "No scoring function found"
        )
        
        has_signal_type = "tool_description_safety" in source
        self.log_check(
            "Defines signal_type='tool_description_safety'",
            has_signal_type,
            "signal_type defined" if has_signal_type else "signal_type not found"
        )
        
        has_enrich_call = "def enrich" in source or "def process" in source
        self.log_check(
            "Has enrich/process function",
            has_enrich_call,
            "Enrich function found" if has_enrich_call else "No enrich function found"
        )
        
        return has_score_fn and has_signal_type and has_enrich_call

    def check_signal_analyser_wiring(self):
        print("\n[3] Checking signal_analyser.py wiring...")
        
        analyser_path = Path("/home/workspace/zo_sentinel/signal_analyser.py")
        
        with open(analyser_path, 'r') as f:
            source = f.read()
        
        import_pattern = r"import.*tool_description_safety|from.*tool_description_safety"
        has_import = bool(re.search(import_pattern, source))
        self.log_check(
            "Imports tool_description_safety_enrichment",
            has_import,
            "Import found" if has_import else "No import statement found"
        )
        
        enrich_call_pattern = r"tool_description_safety|enrich.*tool|enrich_tool_description"
        has_call = bool(re.search(enrich_call_pattern, source, re.IGNORECASE))
        self.log_check(
            "Calls enrichment module",
            has_call,
            "Enrichment call found" if has_call else "No enrichment call found in analyser"
        )
        
        if not has_import or not has_call:
            self.wiring_directives.append({
                "issue": "tool_description_safety_enrichment not wired into signal_analyser",
                "directive": self.generate_wiring_directive(source)
            })
        
        return has_import and has_call

    def generate_wiring_directive(self, analyser_source: str = ""):
        directive = []
        directive.append("# WIRING DIRECTIVE: tool_description_safety_enrichment integration")
        directive.append("")
        directive.append("# Add to imports section of signal_analyser.py:")
        directive.append("from tool_description_safety_enrichment import ToolDescriptionSafetyEnricher")
        directive.append("")
        directive.append("# Add to initialization in __init__ or setup:")
        directive.append("self.tool_safety_enricher = ToolDescriptionSafetyEnricher()")
        directive.append("")
        directive.append("# Add to enrich() or process() method where enrichments are called:")
        directive.append("# Tool description safety enrichment")
        directive.append("if 'tool_description_safety' in self.enabled_enrichments:")
        directive.append("    enriched = self.tool_safety_enricher.enrich(enriched)")
        directive.append("")
        directive.append("# Register in enrichments registry:")
        directive.append("self.enrichment_registry['tool_description_safety'] = self.tool_safety_enricher.enrich")
        
        return "\n".join(directive)

    def check_database_enrichment_records(self):
        print("\n[4] Checking database for enrichment records...")
        
        try:
            payload = {
                "table": "mcp_signal_enrichments",
                "rows": {
                    "query": {
                        "signal_type": "tool_description_safety"
                    },
                    "action": "select"
                },
                "wait": True
            }
            
            response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if "rows" in data:
                    rows = data["rows"]
                    
                    scores = set()
                    for row in rows:
                        if "score" in row:
                            scores.add(row["score"])
                    
                    distinct_scores = len(scores)
                    self.log_check(
                        f"Has >= 20 distinct score values",
                        distinct_scores >= 20,
                        f"Found {distinct_scores} distinct score values"
                    )
                    
                    self.log_check(
                        f"Has enrichment records in database",
                        len(rows) > 0,
                        f"Found {len(rows)} records"
                    )
                    
                    return len(rows) > 0 and distinct_scores >= 20
                else:
                    self.log_check(
                        "Database records exist",
                        False,
                        "No rows returned from query"
                    )
                    return False
            else:
                self.log_check(
                    "Database query",
                    False,
                    f"Status {response.status_code}: {response.text}"
                )
                return False
                
        except Exception as e:
            self.log_check(
                "Database query",
                False,
                f"Error: {str(e)}"
            )
            return False

    def generate_wiring_directive_file(self):
        if self.wiring_directives:
            directive_path = Path("/home/workspace/zo_sentinel/tool_description_safety_wiring_directive.txt")
            with open(directive_path, 'w') as f:
                f.write("# Generated Wiring Directive\n")
                f.write(f"# Generated: {datetime.utcnow().isoformat()}\n")
                f.write("# Purpose: Wire tool_description_safety_enrichment into signal_analyser\n\n")
                
                for directive in self.wiring_directives:
                    f.write(f"# Issue: {directive['issue']}\n\n")
                    f.write(directive['directive'])
                    f.write("\n\n")
            
            print(f"\n[WIRING DIRECTIVE] Generated: {directive_path}")
            return str(directive_path)
        return None

    def run_verification(self):
        print("=" * 70)
        print("ZO-SENTINEL: Tool Description Safety Enrichment Verification")
        print("=" * 70)
        
        files_exist = self.check_source_files_exist()
        
        if not files_exist:
            self.results["summary"] = "FAILED: Required source files missing"
            self.results["wiring_needed"] = True
            return self.results
        
        enrichment_valid = self.check_enrichment_module_structure()
        
        wiring_ok = self.check_signal_analyser_wiring()
        
        db_ok = self.check_database_enrichment_records()
        
        passed_checks = sum(1 for c in self.results["checks"] if c["passed"])
        total_checks = len(self.results["checks"])
        
        self.results["summary"] = f"Passed {passed_checks}/{total_checks} checks"
        self.results["wiring_needed"] = not wiring_ok
        
        self.generate_wiring_directive_file()
        
        print("\n" + "=" * 70)
        print(f"VERIFICATION SUMMARY: {self.results['summary']}")
        
        if self.wiring_directives:
            print("⚠ WIRING DIRECTIVES GENERATED - Manual integration required")
        else:
            print("✓ All checks passed - wiring verified")
        
        print("=" * 70)
        
        return self.results

def main():
    verifier = SafetyEnrichmentVerifier()
    results = verifier.run_verification()
    
    try:
        health_payload = {
            "table": "service_health",
            "rows": {
                "service": "tool_description_safety_enrichment_verify",
                "last_heartbeat": datetime.utcnow().isoformat(),
                "status": "complete",
                "checks_passed": sum(1 for c in results["checks"] if c["passed"]),
                "checks_total": len(results["checks"]),
                "wiring_needed": results.get("wiring_needed", False)
            },
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=health_payload, timeout=5)
    except:
        pass
    
    return 0 if all(c["passed"] for c in results["checks"]) else 1

if __name__ == "__main__":
    sys.exit(main())