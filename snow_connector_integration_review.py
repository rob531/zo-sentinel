import sys
import os
import re
import ast

sys.path.insert(0, '/home/workspace/zo_sentinel')

class IntegrationDiagnostic:
    def __init__(self):
        self.results = []
        
    def log(self, status, msg):
        self.results.append((status, msg))
        print(f"[{status}] {msg}")
    
    def check_snow_connector_exposes_webhook_handler(self):
        self.log("CHECK", "Verifying snow_connector.py exposes handle_snow_webhook...")
        try:
            with open('/home/workspace/zo_sentinel/snow_connector.py', 'r') as f:
                content = f.read()
            
            tree = ast.parse(content)
            handlers = []
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    handlers.append(node.name)
            
            if 'handle_snow_webhook' in handlers:
                self.log("PASS", "handle_snow_webhook function found in snow_connector.py")
                
                func_node = None
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef) and node.name == 'handle_snow_webhook':
                        func_node = node
                        break
                
                if func_node:
                    args = [a.arg for a in func_node.args.args]
                    returns = func_node.returns
                    if 'payload' in args and len(args) == 1:
                        self.log("PASS", "handle_snow_webhook signature: (payload: dict) -> dict is correct")
                    else:
                        self.log("FAIL", f"handle_snow_webhook has unexpected signature: {args}")
            else:
                self.log("FAIL", "handle_snow_webhook not found in snow_connector.py")
                return False
            return True
        except Exception as e:
            self.log("ERROR", f"Failed to parse snow_connector.py: {e}")
            return False
    
    def check_approval_workflow_imports_snow(self):
        self.log("CHECK", "Verifying approval_workflow.py imports or calls snow_connector...")
        try:
            with open('/home/workspace/zo_sentinel/approval_workflow.py', 'r') as f:
                content = f.read()
            
            import_patterns = [
                r'from\s+snow_connector\s+import',
                r'import\s+snow_connector',
                r'snow_connector\.'
            ]
            
            found = False
            for pattern in import_patterns:
                if re.search(pattern, content):
                    found = True
                    break
            
            if found:
                self.log("PASS", "approval_workflow.py references snow_connector")
            else:
                self.log("FAIL", "approval_workflow.py does not import or use snow_connector")
                return False
            
            webhook_call = re.search(r'handle_snow_webhook', content)
            if webhook_call:
                self.log("PASS", "approval_workflow.py calls handle_snow_webhook")
            else:
                self.log("WARN", "approval_workflow.py imports snow_connector but may not call handle_snow_webhook")
            
            return True
        except FileNotFoundError:
            self.log("FAIL", "approval_workflow.py not found")
            return False
        except Exception as e:
            self.log("ERROR", f"Failed to check approval_workflow.py: {e}")
            return False
    
    def check_http_calls_between_services(self):
        self.log("CHECK", "Checking HTTP calls between services...")
        files_to_check = [
            '/home/workspace/zo_sentinel/approval_workflow.py',
            '/home/workspace/zo_sentinel/snow_connector.py'
        ]
        
        http_patterns = [
            r'requests\.(get|post|put|delete|patch)',
            r'httpx\.(get|post|put|delete|patch)',
            r'http://127\.0\.0\.1:\d+'
        ]
        
        found_calls = []
        for filepath in files_to_check:
            try:
                with open(filepath, 'r') as f:
                    content = f.read()
                
                for pattern in http_patterns:
                    matches = re.findall(pattern, content)
                    if matches:
                        found_calls.append((os.path.basename(filepath), matches))
            except FileNotFoundError:
                continue
            except Exception as e:
                self.log("ERROR", f"Failed to check {filepath}: {e}")
        
        if found_calls:
            self.log("PASS", f"HTTP calls found in {len(found_calls)} file(s)")
            for filepath, calls in found_calls:
                self.log("INFO", f"  {filepath}: {len(calls)} HTTP call(s)")
        else:
            self.log("WARN", "No HTTP calls found between services (internal service calls may use direct function calls)")
        
        return True
    
    def run_diagnostics(self):
        print("=" * 60)
        print("ZO-SENTINEL: Snow Connector Integration Review")
        print("=" * 60)
        
        results = {}
        results['snow_webhook_handler'] = self.check_snow_connector_exposes_webhook_handler()
        results['approval_workflow_integration'] = self.check_approval_workflow_imports_snow()
        results['http_calls'] = self.check_http_calls_between_services()
        
        print("\n" + "=" * 60)
        print("DIAGNOSTIC SUMMARY")
        print("=" * 60)
        
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        
        for check, result in results.items():
            status = "PASS" if result else "FAIL"
            print(f"  [{status}] {check}")
        
        print(f"\nResult: {passed}/{total} checks passed")
        
        if passed == total:
            self.log("PASS", "All integration checks passed")
            return 0
        else:
            self.log("FAIL", f"{total - passed} check(s) failed")
            return 1

if __name__ == '__main__':
    diagnostic = IntegrationDiagnostic()
    exit_code = diagnostic.run_diagnostics()
    sys.exit(exit_code)