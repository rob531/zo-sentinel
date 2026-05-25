import ast
import re
from pathlib import Path
from typing import Set, Tuple, List, Dict

PROJECT_ROOT = Path("/home/workspace/zo_sentinel")


class WiringValidator:
    def __init__(self):
        self.snow_connector_path = PROJECT_ROOT / "snow_connector_integration_v2.py"
        self.approval_workflow_path = PROJECT_ROOT / "approval_workflow.py"
        self.db_schema_path = PROJECT_ROOT / "DB_SCHEMA.md"
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.findings: List[str] = []

    def validate(self) -> bool:
        self._log("Starting snow_connector_approval_wiring validation")
        
        if not self.snow_connector_path.exists():
            self.errors.append(f"Missing: {self.snow_connector_path}")
            return False
            
        if not self.approval_workflow_path.exists():
            self.errors.append(f"Missing: {self.approval_workflow_path}")
            return False
            
        if not self.db_schema_path.exists():
            self.errors.append(f"Missing: {self.db_schema_path}")
            return False

        snow_code = self.snow_connector_path.read_text()
        workflow_code = self.approval_workflow_path.read_text()
        schema_content = self.db_schema_path.read_text()

        self._validate_db_schema(schema_content)
        self._validate_snow_connector_wiring(snow_code, workflow_code)
        self._validate_approval_workflow_imports(workflow_code, snow_code)
        self._validate_write_service_calls(snow_code, workflow_code)

        return len(self.errors) == 0

    def _validate_db_schema(self, schema: str):
        required_table = "mcp_submissions"
        required_columns = {"mcp_name", "requested_by"}
        
        if required_table not in schema:
            self.errors.append(f"DB_SCHEMA.md missing table: {required_table}")
        else:
            self._log(f"Found table: {required_table}")
        
        for col in required_columns:
            if col not in schema:
                self.errors.append(f"DB_SCHEMA.md missing column: {col}")

    def _validate_snow_connector_wiring(self, snow_code: str, workflow_code: str):
        snow_classes = self._extract_classes(snow_code)
        workflow_classes = self._extract_classes(workflow_code)
        
        self._log(f"SNOW connector classes: {snow_classes}")
        self._log(f"Approval workflow classes: {workflow_classes}")

        snow_handlers = self._find_servicenow_handlers(snow_code)
        workflow_handlers = self._find_webhook_handlers(workflow_code)
        
        self._log(f"SNOW ServiceNow handlers: {snow_handlers}")
        self._log(f"Workflow webhook handlers: {workflow_handlers}")

        self._check_imports_between_modules(snow_code, workflow_code)

    def _validate_approval_workflow_imports(self, workflow_code: str, snow_code: str):
        snow_module_name = "snow_connector_integration_v2"
        snow_class_pattern = re.compile(r'class\s+(\w+).*?from\s+[\'"]' + snow_module_name)
        
        imported_classes = snow_class_pattern.findall(workflow_code)
        
        import_pattern = re.compile(rf'import.*{snow_module_name}|from.*{snow_module_name}')
        has_import = import_pattern.search(workflow_code) is not None
        
        if not has_import:
            self.warnings.append(
                f"approval_workflow.py does not import {snow_module_name}"
            )
        else:
            self._log(f"Found import of {snow_module_name}")
        
        if imported_classes:
            self._log(f"Imported classes from {snow_module_name}: {imported_classes}")

    def _validate_write_service_calls(self, snow_code: str, workflow_code: str):
        combined_code = snow_code + "\n" + workflow_code
        
        write_calls = self._find_write_service_calls(combined_code)
        
        for call in write_calls:
            table = call.get("table")
            payload = call.get("payload", {})
            
            if table == "mcp_submissions":
                self._validate_mcp_submissions_payload(payload, call)
            elif table:
                self.warnings.append(f"Unknown table: {table}")

    def _validate_mcp_submissions_payload(self, payload: dict, call: dict):
        required_cols = {"mcp_name", "requested_by"}
        provided_cols = set(payload.keys())
        
        missing = required_cols - provided_cols
        if missing:
            self.errors.append(
                f"write_service call missing required columns: {missing} | "
                f"table=mcp_submissions | call={call}"
            )
        
        self._log(f"Valid mcp_submissions payload: {list(provided_cols)}")

    def _check_imports_between_modules(self, snow_code: str, workflow_code: str):
        snow_imports = set(re.findall(r'(?:from|import)\s+(\w+)', snow_code))
        workflow_imports = set(re.findall(r'(?:from|import)\s+(\w+)', workflow_code))
        
        common = snow_imports & workflow_imports
        if common:
            self._log(f"Common imports: {common}")

    def _extract_classes(self, code: str) -> Set[str]:
        return set(re.findall(r'class\s+(\w+)', code))

    def _find_servicenow_handlers(self, code: str) -> List[str]:
        handler_pattern = re.compile(
            r'(def\s+(?:handle_|\w*servicenow\w*|\w*snow\w*)\w*\(.*?\))',
            re.IGNORECASE
        )
        return handler_pattern.findall(code)

    def _find_webhook_handlers(self, code: str) -> List[str]:
        handler_pattern = re.compile(
            r'(def\s+(?:handle_|\w*webhook\w*|inbound\w*)\w*\(.*?\))',
            re.IGNORECASE
        )
        return handler_pattern.findall(code)

    def _find_write_service_calls(self, code: str) -> List[Dict]:
        calls = []
        
        call_pattern = re.compile(
            r'write_service\s*\([^)]*\)',
            re.DOTALL
        )
        
        for match in call_pattern.finditer(code):
            call_text = match.group()
            
            table_match = re.search(r'["\']table["\']\s*:\s*["\'](\w+)["\']', call_text)
            table = table_match.group(1) if table_match else None
            
            rows_match = re.search(r'["\']rows["\']\s*:\s*\{([^}]*)\}', call_text, re.DOTALL)
            payload = {}
            if rows_match:
                row_content = rows_match.group(1)
                for kv in re.finditer(r'["\'](\w+)["\']\s*:\s*["\']?([^,"\']+)["\']?', row_content):
                    payload[kv.group(1)] = kv.group(2).strip()
            
            calls.append({
                "table": table,
                "payload": payload,
                "raw": call_text[:100]
            })
        
        return calls

    def _log(self, msg: str):
        self.findings.append(f"[VALIDATION] {msg}")

    def report(self) -> str:
        output = ["=" * 60]
        output.append("SNOW_CONNECTOR_APPROVAL_WIRING_VALIDATE REPORT")
        output.append("=" * 60)
        
        output.append(f"\nSTATUS: {'PASS' if len(self.errors) == 0 else 'FAIL'}")
        
        if self.errors:
            output.append("\nERRORS:")
            for e in self.errors:
                output.append(f"  [X] {e}")
        
        if self.warnings:
            output.append("\nWARNINGS:")
            for w in self.warnings:
                output.append(f"  [!] {w}")
        
        if self.findings:
            output.append("\nFINDINGS:")
            for f in self.findings:
                output.append(f"  {f}")
        
        return "\n".join(output)


def validate() -> bool:
    validator = WiringValidator()
    result = validator.validate()
    print(validator.report())
    return result


if __name__ == "__main__":
    import sys
    success = validate()
    sys.exit(0 if success else 1)