from dataclasses import dataclass
from typing import Optional
import ast
import re


@dataclass
class ValidationResult:
    check_name: str
    passed: bool
    details: str
    line_ref: Optional[int] = None


class AIDRCommitGatewayVerdictValidator:
    
    VERDICT_ENFORCEMENT_RULES = {
        'CAUTION_LIMITED': {
            'auto_commit_allowed': False,
            'requires_override': True,
            'required_payload_fields': ['injection_resilience_score']
        },
        'HIGH_RISK_ISOLATED': {
            'auto_commit_allowed': False,
            'requires_override': True,
            'required_payload_fields': ['injection_resilience_score']
        },
        'VERDICT_CLEAR': {
            'auto_commit_allowed': True,
            'requires_override': False,
            'required_payload_fields': ['injection_resilience_score']
        },
        'VERDICT_SAFE': {
            'auto_commit_allowed': True,
            'requires_override': False,
            'required_payload_fields': ['injection_resilience_score']
        }
    }
    
    def __init__(self, gateway_path: str, test_file_path: Optional[str] = None):
        self.gateway_path = gateway_path
        self.test_file_path = test_file_path
        self.validation_results: list[ValidationResult] = []
        self.last_test_error: Optional[str] = None
        
    def _load_gateway_source(self) -> str:
        with open(self.gateway_path, 'r') as f:
            return f.read()
    
    def _load_test_last_error(self) -> Optional[str]:
        if not self.test_file_path:
            return None
        try:
            with open(self.test_file_path, 'r') as f:
                content = f.read()
            last_error_match = re.search(r'last_error\s*=\s*["\']([^"\']+)["\']', content)
            if last_error_match:
                return last_error_match.group(1)
            return None
        except FileNotFoundError:
            return None
    
    def _parse_verdict_enforcement(self, source: str) -> dict:
        enforcement = {
            'verdict_checks': [],
            'override_checks': [],
            'payload_field_checks': []
        }
        
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return enforcement
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_source = ast.unparse(node)
                if 'verdict' in func_source.lower():
                    enforcement['verdict_checks'].append({
                        'name': node.name,
                        'lineno': node.lineno
                    })
                if 'override' in func_source.lower():
                    enforcement['override_checks'].append({
                        'name': node.name,
                        'lineno': node.lineno
                    })
                if 'payload' in func_source.lower() or 'injection_resilience' in func_source.lower():
                    enforcement['payload_field_checks'].append({
                        'name': node.name,
                        'lineno': node.lineno
                    })
        
        for verdict in ['CAUTION_LIMITED', 'HIGH_RISK_ISOLATED']:
            if f"'{verdict}'" in source or f'"{verdict}"' in source:
                enforcement['verdict_checks'].append({
                    'verdict': verdict,
                    'found': True
                })
        
        return enforcement
    
    def _check_no_auto_commit_caution_limited(self, source: str) -> ValidationResult:
        lines = source.split('\n')
        in_commit_function = False
        found_caution_check = False
        found_override_check = False
        line_ref = None
        
        for i, line in enumerate(lines, 1):
            if 'def' in line and ('commit' in line.lower() or 'write' in line.lower()):
                in_commit_function = True
            elif 'def ' in line and in_commit_function:
                in_commit_function = False
            
            if in_commit_function and 'CAUTION_LIMITED' in line:
                found_caution_check = True
                line_ref = i
                if 'override' in line.lower() or 'force' in line.lower():
                    found_override_check = True
        
        if found_caution_check and not found_override_check:
            return ValidationResult(
                check_name='NO_AUTO_COMMIT_CAUTION_LIMITED',
                passed=False,
                details=f'CAUTION_LIMITED found in commit path at line {line_ref} without override check',
                line_ref=line_ref
            )
        
        return ValidationResult(
            check_name='NO_AUTO_COMMIT_CAUTION_LIMITED',
            passed=True,
            details='CAUTION_LIMITED requires explicit override for commit',
            line_ref=line_ref
        )
    
    def _check_no_auto_commit_high_risk_isolated(self, source: str) -> ValidationResult:
        lines = source.split('\n')
        in_commit_function = False
        found_risk_check = False
        found_override_check = False
        line_ref = None
        
        for i, line in enumerate(lines, 1):
            if 'def' in line and ('commit' in line.lower() or 'write' in line.lower()):
                in_commit_function = True
            elif 'def ' in line and in_commit_function:
                in_commit_function = False
            
            if in_commit_function and 'HIGH_RISK_ISOLATED' in line:
                found_risk_check = True
                line_ref = i
                if 'override' in line.lower() or 'force' in line.lower():
                    found_override_check = True
        
        if found_risk_check and not found_override_check:
            return ValidationResult(
                check_name='NO_AUTO_COMMIT_HIGH_RISK_ISOLATED',
                passed=False,
                details=f'HIGH_RISK_ISOLATED found in commit path at line {line_ref} without override check',
                line_ref=line_ref
            )
        
        return ValidationResult(
            check_name='NO_AUTO_COMMIT_HIGH_RISK_ISOLATED',
            passed=True,
            details='HIGH_RISK_ISOLATED requires explicit override for commit',
            line_ref=line_ref
        )
    
    def _check_injection_resilience_in_payload(self, source: str) -> ValidationResult:
        has_injection_resilience = 'injection_resilience' in source.lower()
        
        has_payload_construction = False
        has_in_commit = False
        
        lines = source.split('\n')
        for i, line in enumerate(lines, 1):
            if 'payload' in line.lower() and ('commit' in line.lower() or 'write' in line.lower()):
                has_payload_construction = True
            if has_payload_construction and 'injection_resilience' in line.lower():
                has_in_commit = True
                return ValidationResult(
                    check_name='INJECTION_RESILIENCE_IN_PAYLOAD',
                    passed=True,
                    details='injection_resilience_score included in commit payload',
                    line_ref=i
                )
        
        if not has_injection_resilience:
            return ValidationResult(
                check_name='INJECTION_RESILIENCE_IN_PAYLOAD',
                passed=False,
                details='injection_resilience_score NOT found in source',
                line_ref=None
            )
        
        return ValidationResult(
            check_name='INJECTION_RESILIENCE_IN_PAYLOAD',
            passed=False,
            details='injection_resilience_score not included in commit payload construction',
            line_ref=None
        )
    
    def _check_verdict_exclusion_logic(self, source: str) -> ValidationResult:
        excluded_verdicts = ['CAUTION_LIMITED', 'HIGH_RISK_ISOLATED']
        found_exclusions = []
        
        for verdict in excluded_verdicts:
            if f"'{verdict}'" in source or f'"{verdict}"' in source:
                found_exclusions.append(verdict)
        
        if len(found_exclusions) < len(excluded_verdicts):
            return ValidationResult(
                check_name='VERDICT_EXCLUSION_LOGIC',
                passed=False,
                details=f'Missing exclusion for: {[v for v in excluded_verdicts if v not in found_exclusions]}',
                line_ref=None
            )
        
        return ValidationResult(
            check_name='VERDICT_EXCLUSION_LOGIC',
            passed=True,
            details=f'All restricted verdicts have exclusion logic: {found_exclusions}',
            line_ref=None
        )
    
    def validate(self) -> dict:
        self.validation_results = []
        self.last_test_error = self._load_test_last_error()
        
        try:
            source = self._load_gateway_source()
        except FileNotFoundError as e:
            return {
                'status': 'ERROR',
                'error': f'Gateway file not found: {self.gateway_path}',
                'validation_results': []
            }
        
        self.validation_results.append(
            self._check_no_auto_commit_caution_limited(source)
        )
        self.validation_results.append(
            self._check_no_auto_commit_high_risk_isolated(source)
        )
        self.validation_results.append(
            self._check_injection_resilience_in_payload(source)
        )
        self.validation_results.append(
            self._check_verdict_exclusion_logic(source)
        )
        
        passed = sum(1 for r in self.validation_results if r.passed)
        total = len(self.validation_results)
        
        return {
            'status': 'PASS' if passed == total else 'FAIL',
            'passed_checks': passed,
            'total_checks': total,
            'last_test_error': self.last_test_error,
            'validation_results': [
                {
                    'check_name': r.check_name,
                    'passed': r.passed,
                    'details': r.details,
                    'line_ref': r.line_ref
                }
                for r in self.validation_results
            ]
        }


def validate(
    gateway_path: str = '/home/workspace/zo_sentinel/aidr_commit_gateway.py',
    test_file_path: str = '/home/workspace/zo_sentinel/aidr_commit_gateway_verdict_test_v2.py'
) -> dict:
    validator = AIDRCommitGatewayVerdictValidator(
        gateway_path=gateway_path,
        test_file_path=test_file_path
    )
    return validator.validate()


if __name__ == '__main__':
    import json
    result = validate()
    print(json.dumps(result, indent=2))