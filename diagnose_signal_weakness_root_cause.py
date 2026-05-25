"""
ZO-SENTINEL Diagnostic: Signal Weakness Root Cause Analysis
Diagnoses why permission_scope, temporal_stability, and tool_description_safety 
signals show only 3 distinct values each (range: 30-100, 40-90, 50-100).
"""

import sys
import os
import inspect
import ast

# Add project root to path
sys.path.insert(0, '/home/workspace/zo_sentinel')

from dataclasses import dataclass
from typing import Dict, List, Set, Any, Optional
from enum import Enum


class SignalType(Enum):
    PERMISSION_SCOPE = "permission_scope"
    TEMPORAL_STABILITY = "temporal_stability"
    TOOL_DESCRIPTION_SAFETY = "tool_description_safety"


@dataclass
class FieldAnalysis:
    """Analysis of a single field's contribution to signal."""
    field_name: str
    read_in_compute_score: bool
    cardinality_in_sample: int
    source_module: str
    contribution_weight: float


@dataclass
class SignalDiagnosis:
    """Diagnosis results for a signal."""
    signal_type: SignalType
    expected_distinct_values: int
    actual_distinct_values: int
    low_cardinality_fields: List[str]
    missing_field_reads: List[str]
    root_cause: str
    recommendation: str


class EnrichmentModuleInspector:
    """Inspects enrichment modules for field coverage."""
    
    def __init__(self):
        self.modules = {}
        self.field_coverage = {}
        
    def load_module(self, module_path: str) -> Optional[Any]:
        """Load enrichment module from path."""
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "enrichment_module", module_path
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules["enrichment_module"] = module
                spec.loader.exec_module(module)
                return module
        except Exception as e:
            print(f"Failed to load {module_path}: {e}")
        return None
    
    def extract_fields_from_function(self, func) -> Set[str]:
        """Extract field names referenced in a function using AST."""
        fields = set()
        try:
            source = inspect.getsource(func)
            tree = ast.parse(source)
            
            for node in ast.walk(tree):
                # Look for attribute access (e.g., metadata['field'])
                if isinstance(node, ast.Subscript):
                    if isinstance(node.value, ast.Name) and node.value.id == 'metadata':
                        if isinstance(node.slice, ast.Constant):
                            fields.add(node.slice.value)
                        elif isinstance(node.slice, ast.Str):  # Python 3.7 compatibility
                            fields.add(node.slice.s)
                
                # Look for .get() calls
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        if node.func.attr == 'get':
                            for arg in node.args:
                                if isinstance(arg, ast.Constant):
                                    fields.add(arg.value)
                
                # Look for .get() with keyword arguments
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg == 'default':
                            continue
                        if isinstance(keyword.value, ast.Constant):
                            fields.add(keyword.value.value if hasattr(keyword.value, 'value') else str(keyword.value))
        except Exception as e:
            print(f"Error extracting fields from {func.__name__}: {e}")
        
        return fields
    
    def find_compute_score_functions(self, module) -> Dict[str, callable]:
        """Find all compute_score functions in module."""
        compute_scores = {}
        for name, obj in inspect.getmembers(module):
            if inspect.isfunction(obj) and 'compute_score' in name.lower():
                compute_scores[name] = obj
        return compute_scores


class SignalWeaknessDiagnoser:
    """Diagnoses signal weakness in enrichment modules."""
    
    def __init__(self):
        self.inspector = EnrichmentModuleInspector()
        self.signal_modules = {
            SignalType.PERMISSION_SCOPE: self._find_module('permission'),
            SignalType.TEMPORAL_STABILITY: self._find_module('temporal'),
            SignalType.TOOL_DESCRIPTION_SAFETY: self._find_module('description'),
        }
        self.diagnoses = []
    
    def _find_module(self, keyword: str) -> Optional[Any]:
        """Find enrichment module by keyword in filename."""
        enrichment_dir = '/home/workspace/zo_sentinel/enrichment'
        if not os.path.exists(enrichment_dir):
            enrichment_dir = '/home/workspace/zo_sentinel'
        
        for filename in os.listdir(enrichment_dir):
            if keyword in filename.lower() and filename.endswith('.py'):
                path = os.path.join(enrichment_dir, filename)
                return self.inspector.load_module(path)
        return None
    
    def _get_expected_cardinality(self, signal_type: SignalType) -> int:
        """Get expected cardinality for a signal type."""
        # Based on domain knowledge - these signals should have high cardinality
        card_map = {
            SignalType.PERMISSION_SCOPE: 50,  # Permissions vary widely
            SignalType.TEMPORAL_STABILITY: 40,  # Temporal patterns vary
            SignalType.TOOL_DESCRIPTION_SAFETY: 100,  # Descriptions vary widely
        }
        return card_map.get(signal_type, 20)
    
    def _get_metadata_fields(self, signal_type: SignalType) -> List[str]:
        """Get expected metadata fields for signal type."""
        fields_map = {
            SignalType.PERMISSION_SCOPE: [
                'permissions', 'scopes', 'capabilities', 'access_level',
                'resource_access', 'privilege_level', 'authorization_scope'
            ],
            SignalType.TEMPORAL_STABILITY: [
                'created_at', 'updated_at', 'last_verified', 'version_history',
                'stability_score', 'deployment_date', 'modification_count'
            ],
            SignalType.TOOL_DESCRIPTION_SAFETY: [
                'description', 'name', 'docstring', 'parameters',
                'return_type', 'input_schema', 'safety_notes'
            ],
        }
        return fields_map.get(signal_type, [])
    
    def _analyze_compute_score_fields(self, module, signal_type: SignalType) -> Dict[str, Set[str]]:
        """Analyze which fields compute_score reads."""
        if not module:
            return {}
        
        result = {}
        compute_funcs = self.inspector.find_compute_score_functions(module)
        
        for name, func in compute_funcs.items():
            result[name] = self.inspector.extract_fields_from_function(func)
        
        return result
    
    def _check_field_coverage(self, signal_type: SignalType, 
                               compute_score_fields: Set[str]) -> Dict[str, bool]:
        """Check which expected fields are actually read."""
        expected = self._get_metadata_fields(signal_type)
        coverage = {}
        
        for field in expected:
            # Check if field or close match is in compute_score fields
            found = False
            for cf in compute_score_fields:
                if field in cf or cf in field:
                    found = True
                    break
            coverage[field] = found
        
        return coverage
    
    def _identify_root_cause(self, signal_type: SignalType,
                              coverage: Dict[str, bool],
                              compute_fields: Set[str],
                              actual_distinct: int) -> tuple[str, str]:
        """Identify root cause of signal weakness."""
        
        missing_fields = [f for f, covered in coverage.items() if not covered]
        total_expected = len(coverage)
        coverage_pct = (sum(covered for covered in coverage.values()) / total_expected * 100) if total_expected > 0 else 0
        
        if len(missing_fields) >= total_expected * 0.6:
            root_cause = f"CRITICAL: {len(missing_fields)}/{total_expected} expected fields not read in compute_score"
            recommendation = f"Add metadata field reads for: {', '.join(missing_fields[:5])}"
        
        elif len(compute_fields) < 3:
            root_cause = "compute_score reads fewer than 3 metadata fields"
            recommendation = "Expand compute_score to read more diverse metadata fields"
        
        elif actual_distinct <= 3:
            root_cause = "Score calculation has insufficient variance (binned into ~3 buckets)"
            recommendation = "Check score normalization - ensure continuous range without artificial binning"
        
        else:
            root_cause = "Input metadata lacks diversity for this signal"
            recommendation = "Enrich metadata collection for this signal type"
        
        # Check for constant value patterns
        if len(compute_fields) > 0:
            constant_patterns = [f for f in compute_fields if 'default' in f.lower() or 'fallback' in f.lower()]
            if constant_patterns:
                root_cause += f" | Uses constant defaults: {constant_patterns}"
        
        return root_cause, recommendation
    
    def diagnose_signal(self, signal_type: SignalType, actual_distinct: int) -> SignalDiagnosis:
        """Run full diagnosis for a signal."""
        module = self.signal_modules.get(signal_type)
        
        # Get compute_score fields
        all_compute_fields = self._analyze_compute_score_fields(module, signal_type)
        compute_fields = set()
        for fields in all_compute_fields.values():
            compute_fields.update(fields)
        
        # Check coverage
        coverage = self._check_field_coverage(signal_type, compute_fields)
        
        # Identify root cause
        root_cause, recommendation = self._identify_root_cause(
            signal_type, coverage, compute_fields, actual_distinct
        )
        
        # Find low cardinality fields
        low_card_fields = []
        if module:
            for name, obj in inspect.getmembers(module):
                if inspect.isfunction(obj) and hasattr(obj, '__code__'):
                    # Check for hardcoded values
                    source = inspect.getsource(obj)
                    if 'range(' in source or '[0' in source or '30, 100' in source:
                        low_card_fields.append(name)
        
        return SignalDiagnosis(
            signal_type=signal_type,
            expected_distinct_values=self._get_expected_cardinality(signal_type),
            actual_distinct_values=actual_distinct,
            low_cardinality_fields=low_card_fields,
            missing_field_reads=[f for f, covered in coverage.items() if not covered],
            root_cause=root_cause,
            recommendation=recommendation
        )
    
    def run_full_diagnosis(self) -> List[SignalDiagnosis]:
        """Run diagnosis on all weak signals."""
        
        # Signal weak data from problem statement
        weak_signals = {
            SignalType.PERMISSION_SCOPE: 3,  # range 30-100, ~3 distinct
            SignalType.TEMPORAL_STABILITY: 3,  # range 40-90, ~3 distinct
            SignalType.TOOL_DESCRIPTION_SAFETY: 3,  # range 50-100, ~3 distinct
        }
        
        diagnoses = []
        for signal_type, actual_distinct in weak_signals.items():
            diagnosis = self.diagnose_signal(signal_type, actual_distinct)
            diagnoses.append(diagnosis)
            self.diagnoses.append(diagnosis)
        
        return diagnoses
    
    def print_diagnosis_report(self, diagnoses: List[SignalDiagnosis]):
        """Print formatted diagnosis report."""
        print("=" * 80)
        print("ZO-SENTINEL: SIGNAL WEAKNESS DIAGNOSIS REPORT")
        print("=" * 80)
        print()
        
        for d in diagnoses:
            print(f"[{d.signal_type.value.upper()}]")
            print(f"  Expected Distinct Values: {d.expected_distinct_values}")
            print(f"  Actual Distinct Values:   {d.actual_distinct_values}")
            print(f"  Signal Strength:         {'WEAK' if d.actual_distinct_values < 10 else 'OK'}")
            print()
            
            if d.missing_field_reads:
                print(f"  Missing Field Reads ({len(d.missing_field_reads)}):")
                for field in d.missing_field_reads[:5]:
                    print(f"    - {field}")
                print()
            
            if d.low_cardinality_fields:
                print(f"  Low Cardinality Functions:")
                for func in d.low_cardinality_fields:
                    print(f"    - {func}")
                print()
            
            print(f"  ROOT CAUSE: {d.root_cause}")
            print(f"  RECOMMENDATION: {d.recommendation}")
            print("-" * 80)
            print()


class EnrichmentContractValidator:
    """Validates that compute_score functions read required fields per contract."""
    
    def __init__(self):
        self.contract_requirements = {
            'permission_scope': [
                'permissions', 'scopes', 'capabilities', 'access_level',
                'resource_access', 'privilege_level', 'authorization_scope'
            ],
            'temporal_stability': [
                'created_at', 'updated_at', 'last_verified', 'version_history',
                'stability_score', 'deployment_date', 'modification_count'
            ],
            'tool_description_safety': [
                'description', 'name', 'docstring', 'parameters',
                'return_type', 'input_schema', 'safety_notes'
            ],
        }
    
    def validate_enrichment_module(self, module_path: str, contract_name: str) -> Dict[str, Any]:
        """Validate enrichment module against contract."""
        required_fields = self.contract_requirements.get(contract_name, [])
        
        result = {
            'module_path': module_path,
            'contract': contract_name,
            'required_fields': required_fields,
            'fields_found': [],
            'fields_missing': [],
            'validation_status': 'UNKNOWN',
        }
        
        try:
            spec = importlib.util.spec_from_file_location(
                "contract_module", module_path
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules["contract_module"] = module
                spec.loader.exec_module(module)
                
                # Find compute_score
                for name, obj in inspect.getmembers(module):
                    if inspect.isfunction(obj) and 'compute_score' in name.lower():
                        fields = self._extract_fields(obj)
                        result['fields_found'] = list(fields)
                        result['fields_missing'] = [f for f in required_fields 
                                                   if not any(f in sf or sf in f for sf in fields)]
                        
                        coverage = len(result['fields_found']) / len(required_fields) * 100 if required_fields else 0
                        result['validation_status'] = 'PASS' if coverage >= 70 else 'FAIL'
                        result['coverage_percent'] = coverage
                        break
                        
        except Exception as e:
            result['error'] = str(e)
            result['validation_status'] = 'ERROR'
        
        return result
    
    def _extract_fields(self, func) -> Set[str]:
        """Extract fields from function."""
        inspector = EnrichmentModuleInspector()
        return inspector.extract_fields_from_function(func)


def main():
    """Run signal weakness diagnosis."""
    print("Starting ZO-SENTINEL Signal Weakness Diagnosis...")
    print()
    
    # Run diagnostic
    diagnoser = SignalWeaknessDiagnoser()
    diagnoses = diagnoser.run_full_diagnosis()
    diagnoser.print_diagnosis_report(diagnoses)
    
    # Validate enrichment contracts
    print()
    print("=" * 80)
    print("ENRICHMENT CONTRACT VALIDATION")
    print("=" * 80)
    print()
    
    validator = EnrichmentContractValidator()
    
    # Check enrichment directory
    enrichment_dir = '/home/workspace/zo_sentinel/enrichment'
    if not os.path.exists(enrichment_dir):
        enrichment_dir = '/home/workspace/zo_sentinel'
    
    for filename in os.listdir(enrichment_dir):
        if filename.endswith('.py') and 'enrich' in filename.lower():
            path = os.path.join(enrichment_dir, filename)
            
            # Determine contract based on filename
            contract = None
            if 'permission' in filename.lower():
                contract = 'permission_scope'
            elif 'temporal' in filename.lower():
                contract = 'temporal_stability'
            elif 'description' in filename.lower() or 'safety' in filename.lower():
                contract = 'tool_description_safety'
            
            if contract:
                result = validator.validate_enrichment_module(path, contract)
                
                print(f"Module: {filename}")
                print(f"  Contract: {result['contract']}")
                print(f"  Status: {result['validation_status']}")
                if 'coverage_percent' in result:
                    print(f"  Coverage: {result['coverage_percent']:.1f}%")
                if result['fields_missing']:
                    print(f"  Missing Fields: {result['fields_missing']}")
                print()
    
    # Summary
    print("=" * 80)
    print("DIAGNOSIS SUMMARY")
    print("=" * 80)
    
    all_missing = []
    for d in diagnoses:
        all_missing.extend(d.missing_field_reads)
    
    unique_missing = list(set(all_missing))
    print(f"\nTotal missing field reads across all signals: {len(unique_missing)}")
    print("\nTop missing fields causing signal weakness:")
    for field in unique_missing[:10]:
        count = sum(1 for d in diagnoses if field in d.missing_field_reads)
        print(f"  - {field} (missing in {count} signals)")
    
    print("\nRoot causes identified:")
    for d in diagnoses:
        print(f"  [{d.signal_type.value}]: {d.root_cause}")
    
    print("\n" + "=" * 80)
    print("Diagnosis complete. Recommended fixes in RECOMMENDATION fields above.")
    print("=" * 80)


if __name__ == '__main__':
    main()