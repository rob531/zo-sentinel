import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

class SignalAnalyserSmokeDiag:
    def __init__(self):
        self.project_root = Path("/home/workspace/zo_sentinel")
        self.signal_analyser_path = self.project_root / "signal_analyser.py"
        self.import_chain_diag_path = self.project_root / "smoke_import_chain_diag_v2.py"
        
    def run_import_chain_diag(self):
        """Execute smoke_import_chain_diag_v2.py to trace the import chain"""
        sys.path.insert(0, str(self.project_root))
        
        try:
            from smoke_import_chain_diag_v2 import trace_import_chain
            result = trace_import_chain("signal_analyser")
            return result
        except ImportError as e:
            return {"error": f"Failed to load import chain diag: {e}"}
        except Exception as e:
            return {"error": f"Import chain diag failed: {e}"}
    
    def direct_import_attempt(self):
        """Directly attempt to import signal_analyser and capture full traceback"""
        sys.path.insert(0, str(self.project_root))
        
        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "method": "direct_import_attempt",
            "signal_analyser_path": str(self.signal_analyser_path)
        }
        
        try:
            import signal_analyser
            result["status"] = "success"
            result["module_loaded"] = True
        except ImportError as e:
            result["status"] = "import_failure"
            result["failing_module"] = self._extract_failing_module(str(e))
            result["import_reason"] = str(e)
            result["full_traceback"] = traceback.format_exc()
            result["failure_type"] = "ImportError"
        except ModuleNotFoundError as e:
            result["status"] = "module_not_found"
            result["failing_module"] = self._extract_failing_module(str(e))
            result["import_reason"] = str(e)
            result["full_traceback"] = traceback.format_exc()
            result["failure_type"] = "ModuleNotFoundError"
        except Exception as e:
            result["status"] = "unknown_failure"
            result["failing_module"] = self._extract_failing_module(str(e))
            result["import_reason"] = str(e)
            result["full_traceback"] = traceback.format_exc()
            result["failure_type"] = type(e).__name__
            
        return result
    
    def _extract_failing_module(self, error_message):
        """Extract the actual failing module name from error message"""
        error_str = str(error_message)
        
        if "No module named" in error_str:
            parts = error_str.split("No module named")
            if len(parts) > 1:
                module_name = parts[1].strip().strip("'\"")
                return module_name
        
        if "cannot import" in error_str.lower():
            if "from" in error_str.lower():
                parts = error_str.lower().split("from")
                if len(parts) > 1:
                    import_part = parts[1].strip().split()[0].strip().strip("'\"")
                    return import_part
        
        return "unknown"
    
    def analyse_missing_dependencies(self, failing_module):
        """Check if failing module has unmet dependencies"""
        sys.path.insert(0, str(self.project_root))
        
        result = {
            "failing_module": failing_module,
            "dependency_analysis": {}
        }
        
        try:
            import pkgutil
            if failing_module != "unknown":
                module_spec = importlib.util.find_spec(failing_module)
                if module_spec is None:
                    result["dependency_analysis"]["status"] = "module_not_installed"
                    result["dependency_analysis"]["suggestion"] = f"pip install {failing_module}"
                else:
                    result["dependency_analysis"]["status"] = "module_exists"
                    result["dependency_analysis"]["location"] = str(module_spec.origin) if module_spec.origin else "unknown"
        except Exception as e:
            result["dependency_analysis"]["status"] = "analysis_failed"
            result["dependency_analysis"]["error"] = str(e)
            
        return result
    
    def check_file_integrity(self):
        """Check if signal_analyser.py exists and has valid syntax"""
        result = {
            "method": "file_integrity_check",
            "signal_analyser_path": str(self.signal_analyser_path)
        }
        
        if not self.signal_analyser_path.exists():
            result["status"] = "file_missing"
            return result
            
        result["file_exists"] = True
        
        try:
            with open(self.signal_analyser_path, 'r') as f:
                content = f.read()
            result["file_size"] = len(content)
            result["status"] = "file_readable"
        except Exception as e:
            result["status"] = "file_read_error"
            result["error"] = str(e)
            
        try:
            compile(content, str(self.signal_analyser_path), 'exec')
            result["syntax_valid"] = True
        except SyntaxError as e:
            result["syntax_valid"] = False
            result["syntax_error"] = {
                "line": e.lineno,
                "offset": e.offset,
                "text": e.text,
                "message": str(e)
            }
            
        return result
    
    def run(self):
        """Run complete diagnostic suite"""
        findings = []
        
        findings.append({
            "timestamp": datetime.utcnow().isoformat(),
            "diagnostic_type": "signal_analyser_smoke_diag"
        })
        
        file_check = self.check_file_integrity()
        findings.append(file_check)
        
        direct_import = self.direct_import_attempt()
        findings.append(direct_import)
        
        if direct_import.get("failing_module") and direct_import.get("failing_module") != "unknown":
            dep_analysis = self.analyse_missing_dependencies(direct_import["failing_module"])
            findings.append(dep_analysis)
        
        findings.append(self.run_import_chain_diag())
        
        final_finding = {
            "timestamp": datetime.utcnow().isoformat(),
            "diagnostic_type": "signal_analyser_smoke_diag_summary",
            "failing_module": direct_import.get("failing_module", "unknown"),
            "import_reason": direct_import.get("import_reason", direct_import.get("error", "unknown")),
            "failure_type": direct_import.get("failure_type", "unknown"),
            "file_exists": file_check.get("file_exists", False),
            "syntax_valid": file_check.get("syntax_valid", None),
            "full_traceback": direct_import.get("full_traceback", None)
        }
        findings.append(final_finding)
        
        return findings


def run():
    """Main execution for __main__"""
    import logging
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger("signal_analyser_smoke_diag")
    
    logger.info("Starting signal_analyser smoke failure diagnostics")
    
    diag = SignalAnalyserSmokeDiag()
    findings = diag.run()
    
    for finding in findings:
        print(json.dumps(finding, default=str))
        
    logger.info("Diagnostics complete")


if __name__ == "__main__":
    run()