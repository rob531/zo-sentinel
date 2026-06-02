#!/usr/bin/env python3
"""
Import Failure Analysis Diagnostic Utility

Analyzes smoke test failure logs to identify import-related failures
in target modules without attempting to import them.
"""

import sys
import re
import argparse
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import textwrap


class FailureType(Enum):
    """Classification of import failure types."""
    MISSING_MODULE = "missing_module"
    VERSION_CONFLICT = "version_conflict"
    CIRCULAR_IMPORT = "circular_import"
    ATTRIBUTE_ERROR = "attribute_error"
    UNKNOWN = "unknown"


@dataclass
class ImportFailure:
    """Represents a single import failure."""
    failure_type: FailureType
    affected_module: str
    error_message: str
    missing_module: Optional[str] = None
    traceback_frames: list[str] = field(default_factory=list)
    suggested_resolution: str = ""


@dataclass
class AnalysisReport:
    """Complete analysis report."""
    total_failures: int
    failures: list[ImportFailure]
    has_target_module_failures: bool = False
    error_summary: str = ""


class TracebackParser:
    """Parser for Python tracebacks."""
    
    @staticmethod
    def extract_missing_module_name(error_text: str) -> Optional[str]:
        """Extract the missing module from error text."""
        patterns = [
            r"No module named ['\"]([^'\"]+)['\"]",
            r"ModuleNotFoundError: ['\"]([^'\"]+)['\"]",
            r"package ['\"]([^'\"]+)['\"] is missing",
            r"['\"]([^'\"]+)['\"] .*?not found",
        ]
        for pattern in patterns:
            match = re.search(pattern, error_text)
            if match:
                module = match.group(1)
                return module.split('.')[0]
        return None
    
    @staticmethod
    def detect_version_conflict(error_text: str) -> Optional[dict]:
        """Detect version conflict patterns in error messages."""
        conflict_patterns = [
            (r"requires\s+([^\s,;]+)\s*([=><!]+)\s*([^\s,\]]+)(?:\s|;|$)", "requires"),
            (r"has requirement\s+([^\s,;]+)\s*([=<>!]+)\s*([^\s,\]]+)", "requirement"),
            (r"([^\s]+)\s*([=><!]{1,2})\s*([^\s;]+).*?(?:but found|you have)\s*([^\s\]]+)", "mismatch"),
            (r"incompatible library version: requires [^\s]+ but found ([^\s]+)", "incompatible"),
            (r"version\s+([^\s]+)\s+does not match", "version_mismatch"),
        ]
        for pattern, conflict_type in conflict_patterns:
            match = re.search(pattern, error_text, re.IGNORECASE)
            if match:
                return {
                    "type": conflict_type,
                    "package": match.group(1),
                    "expected": match.group(3) if len(match.groups()) >= 3 else match.group(2),
                    "found": match.group(4) if len(match.groups()) >= 4 else None
                }
        return None
    
    @staticmethod
    def detect_circular_import(traceback_frames: list[str]) -> Optional[list[str]]:
        """Detect circular import patterns in traceback frames.
        
        Returns the circular import path if found, None otherwise.
        """
        if len(traceback_frames) < 4:
            return None
        
        import_pattern = re.compile(r'(?:^import\s+(\w+)|^from\s+([\w.]+)\s+import)')
        module_chain = []
        
        for frame in traceback_frames:
            matches = import_pattern.findall(frame)
            for match in matches:
                module = match[0] or match[1]
                if module and module not in ('__main__',):
                    module_chain.append(module)
        
        seen = {}
        for i, module in enumerate(module_chain):
            if module in seen:
                first_idx = seen[module]
                if i - first_idx >= 2:
                    return module_chain[first_idx:i + 1]
            else:
                seen[module] = i
        
        for i in range(len(module_chain) - 1):
            if module_chain[i] == module_chain[i - 1] if i > 0 else False:
                continue
            for j in range(i + 1, len(module_chain)):
                if module_chain[i] == module_chain[j]:
                    if j - i >= 2:
                        return module_chain[i:j + 1]
        
        return None
    
    @staticmethod
    def parse_traceback(traceback_text: str) -> dict:
        """Parse a traceback block and extract structured information."""
        result = {
            'frames': [],
            'exception_type': None,
            'exception_message': ''
        }
        
        lines = traceback_text.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('File "'):
                match = re.search(r'File "([^"]+)"', line)
                if match:
                    filepath = match.group(1)
                    filename = filepath.split('/')[-1] if '/' in filepath else filepath
                    result['frames'].append({
                        'file': filepath,
                        'filename': filename,
                        'line': line
                    })
            elif re.match(r'^\w+Error:', line):
                parts = line.split(':', 1)
                result['exception_type'] = parts[0].strip()
                result['exception_message'] = parts[1].strip() if len(parts) > 1 else ''
            elif re.match(r'^\w+Exception:', line):
                parts = line.split(':', 1)
                result['exception_type'] = parts[0].strip()
                result['exception_message'] = parts[1].strip() if len(parts) > 1 else ''
        
        return result


def classify_failure(
    exception_type: str,
    error_message: str,
    traceback_frames: list,
    target_modules: list[str]
) -> tuple[FailureType, Optional[str], str]:
    """Classify the failure type and generate resolution suggestion."""
    
    error_lower = error_message.lower()
    exception_lower = (exception_type or '').lower()
    
    if 'modulenotfounderror' in exception_lower or 'no module named' in error_lower:
        missing = TracebackParser.extract_missing_module_name(error_message)
        resolution = (
            f"pip install {missing}" if missing 
            else "Install the missing package using pip"
        )
        return FailureType.MISSING_MODULE, missing, resolution
    
    if 'importerror' in exception_lower:
        missing = TracebackParser.extract_missing_module_name(error_message)
        if missing:
            resolution = f"Install missing module: pip install {missing}"
            return FailureType.MISSING_MODULE, missing, resolution
    
    version_conflict = TracebackParser.detect_version_conflict(error_message)
    if version_conflict:
        pkg = version_conflict.get('package', 'unknown')
        expected = version_conflict.get('expected', 'required version')
        found = version_conflict.get('found')
        
        if found:
            resolution = f"Update conflicting package: pip install '{pkg}=={expected}' (currently {found})"
        else:
            resolution = f"Install correct version: pip install '{pkg}{expected}'"
        return FailureType.VERSION_CONFLICT, pkg, resolution
    
    if 'attributeerror' in exception_lower:
        resolution = "Check module version or attribute name; update package or fix attribute reference"
        return FailureType.ATTRIBUTE_ERROR, None, resolution
    
    circular_path = TracebackParser.detect_circular_import(traceback_frames)
    if circular_path:
        path_str = ' -> '.join(circular_path)
        resolution = (
            f"Break circular import chain: {path_str}. "
            "Consider deferred imports or refactoring shared code."
        )
        return FailureType.CIRCULAR_IMPORT, None, resolution
    
    return FailureType.UNKNOWN, None, "Review error details manually"


def analyze_log(log_text: str, target_modules: list[str]) -> AnalysisReport:
    """Analyze log text for import failures in target modules."""
    
    failures = []
    target_module_patterns = [re.escape(m) for m in target_modules]
    combined_pattern = '|'.join(target_module_patterns)
    traceback_pattern = re.compile(
        r'Traceback \(most recent call last\):\s*\n(.*?)(?=\n[A-Z][a-z]+(?:Error|Exception):|\n---|\Z)',
        re.DOTALL
    )
    
    tracebacks = traceback_pattern.findall(log_text)
    
    for traceback in tracebacks:
        parsed = TracebackParser.parse_traceback(traceback)
        
        if not parsed['exception_type']:
            continue
        
        relevant_exceptions = {
            'importerror', 'modulenotfounderror', 'attributeerror',
            'typeerror', 'runtimeerror'
        }
        
        if parsed['exception_type'].lower() not in relevant_exceptions:
            continue
        
        affected_module = None
        
        for frame in parsed['frames']:
            filename = frame['filename']
            if filename in target_modules:
                affected_module = filename
                break
            if combined_pattern and re.search(combined_pattern, filename, re.IGNORECASE):
                for tm in target_modules:
                    if tm.lower() in filename.lower():
                        affected_module = tm
                        break
        
        if not affected_module:
            for tm in target_modules:
                if tm.lower() in traceback.lower():
                    affected_module = tm
                    break
        
        failure_type, missing_module, resolution = classify_failure(
            parsed['exception_type'],
            parsed['exception_message'],
            [f['line'] for f in parsed['frames']],
            target_modules
        )
        
        frame_summary = [f['line'] for f in parsed['frames'][:10]]
        
        failure = ImportFailure(
            failure_type=failure_type,
            affected_module=affected_module or "unknown",
            error_message=parsed['exception_message'],
            missing_module=missing_module,
            traceback_frames=frame_summary,
            suggested_resolution=resolution
        )
        failures.append(failure)
    
    has_target_failures = any(
        f.affected_module in target_modules for f in failures
    )
    
    summary_parts = []
    missing = [f for f in failures if f.failure_type == FailureType.MISSING_MODULE]
    version = [f for f in failures if f.failure_type == FailureType.VERSION_CONFLICT]
    circular = [f for f in failures if f.failure_type == FailureType.CIRCULAR_IMPORT]
    attr = [f for f in failures if f.failure_type == FailureType.ATTRIBUTE_ERROR]
    
    if missing:
        modules = [f.missing_module for f in missing if f.missing_module]
        summary_parts.append(f"missing_modules={','.join(set(modules))}")
    if version:
        packages = [f.missing_module for f in version if f.missing_module]
        summary_parts.append(f"version_conflicts={len(version)}")
    if circular:
        summary_parts.append(f"circular_imports={len(circular)}")
    if attr:
        summary_parts.append(f"attribute_errors={len(attr)}")
    
    error_summary = "; ".join(summary_parts) if summary_parts else "analysis_complete"
    
    return AnalysisReport(
        total_failures=len(failures),
        failures=failures,
        has_target_module_failures=has_target_failures,
        error_summary=error_summary
    )


def generate_report(report: AnalysisReport, target_modules: list[str]) -> str:
    """Generate structured analysis report."""
    
    lines = [
        "=" * 70,
        "IMPORT FAILURE ANALYSIS REPORT",
        "=" * 70,
        "",
        f"total_failures: {report.total_failures}",
        f"has_target_module_failures: {str(report.has_target_module_failures).lower()}",
        f"target_modules: {', '.join(target_modules)}",
        "",
        "-" * 70,
        "FAILURE SUMMARY",
        "-" * 70,
    ]
    
    for ftype in FailureType:
        count = sum(1 for ff in report.failures if ff.failure_type == ftype)
        if count > 0:
            lines.append(f"  {ftype.value}: {count}")
    
    if report.total_failures == 0:
        lines.extend([
            "",
            "RESULT: No import failures detected in log.",
        ])
    else:
        lines.extend([
            "",
            "-" * 70,
            "DETAILED FAILURES",
            "-" * 70,
        ])
        
        for i, failure in enumerate(report.failures, 1):
            lines.extend([
                "",
                f"Failure #{i}:",
                f"  root_cause_classification: {failure.failure_type.value}",
                f"  affected_module: {failure.affected_module}",
                f"  error_message: {failure.error_message}",
            ])
            
            if failure.missing_module:
                lines.append(f"  missing_module: {failure.missing_module}")
            
            lines.extend([
                f"  suggested_resolution: {failure.suggested_resolution}",
            ])
            
            if failure.traceback_frames:
                lines.append("  traceback_frames:")
                for frame in failure.traceback_frames[:8]:
                    lines.append(f"    {frame}")
    
    lines.extend([
        "",
        "=" * 70,
        f"analysis_complete",
        f"error_summary: {report.error_summary}",
        "=" * 70,
    ])
    
    return '\n'.join(lines)


def run_self_test() -> bool:
    """Run self-test against sample log snippets."""
    
    sample_logs = [
        {
            'name': 'missing_module',
            'log': textwrap.dedent("""
                Traceback (most recent call last):
                  File "test_smoke.py", line 45, in <module>
                    from registry_api import RegistryClient
                  File "/app/registry_api.py", line 12, in <module>
                    import redis
                ModuleNotFoundError: No module named 'redis'
            """).strip(),
            'expected_type': FailureType.MISSING_MODULE,
            'expected_module': 'redis'
        },
        {
            'name': 'version_conflict',
            'log': textwrap.dedent("""
                Traceback (most recent call last):
                  File "tests/test_signal.py", line 23, in test_analyser
                    from signal_analyser import SignalProcessor
                  File "/app/signal_analyser.py", line 8, in <module>
                    import pandas as pd
                ImportError: this program requires pandas>=1.5.0 but you have version 1.3.0
            """).strip(),
            'expected_type': FailureType.VERSION_CONFLICT,
            'expected_module': 'pandas'
        },
        {
            'name': 'circular_import',
            'log': textwrap.dedent("""
                Traceback (most recent call last):
                  File "app/rug_pull_monitor.py", line 15, in <module>
                    from registry_api import RegistryClient
                  File "app/registry_api.py", line 10, in <module>
                    from rug_pull_monitor import MonitorBase
                  File "app/rug_pull_monitor.py", line 8, in <module>
                    import registry_api
                ImportError: cannot import name 'RegistryClient' from partially initialized module 'registry_api'
            """).strip(),
            'expected_type': FailureType.CIRCULAR_IMPORT,
            'expected_module': None
        },
        {
            'name': 'attribute_error',
            'log': textwrap.dedent("""
                Traceback (most recent call last):
                  File "/app/signal_analyser.py", line 25, in initialize
                    processor = signal_analyser.create_processor()
                  File "/app/signal_analyser/processor.py", line 12, in create_processor
                    from .core import SignalProcessor
                AttributeError: module 'signal_analyser' has no attribute 'create_processor'
            """).strip(),
            'expected_type': FailureType.ATTRIBUTE_ERROR,
            'expected_module': None
        },
        {
            'name': 'target_module_detection',
            'log': textwrap.dedent("""
                Traceback (most recent call last):
                  File "test_runner.py", line 100, in run_tests
                    import registry_api
                  File "app/registry_api.py", line 5, in <module>
                    import missing_pkg
                ModuleNotFoundError: No module named 'missing_pkg'
            """).strip(),
            'expected_type': FailureType.MISSING_MODULE,
            'expected_module': 'registry_api'
        },
        {
            'name': 'no_failures',
            'log': textwrap.dedent("""
                Running smoke tests...
                All tests passed successfully.
                42 tests completed in 3.2s
            """).strip(),
            'expected_type': None,
            'expected_module': None
        }
    ]
    
    target_modules = ['registry_api.py', 'rug_pull_monitor.py', 'signal_analyser.py']
    alt_targets = ['registry_api', 'rug_pull_monitor', 'signal_analyser']
    
    all_passed = True
    print("Running self-test against sample log snippets...")
    print("-" * 60)
    
    for sample in sample_logs:
        report = analyze_log(sample['log'], alt_targets)
        
        if sample['expected_type'] is None:
            if report.total_failures == 0:
                print(f"  PASS: {sample['name']} - correctly identified no failures")
            else:
                print(f"  FAIL: {sample['name']} - expected no failures, got {report.total_failures}")
                all_passed = False
            continue
        
        if report.total_failures == 0:
            print(f"  FAIL: {sample['name']} - expected failure, got none")
            all_passed = False
            continue
        
        failure = report.failures[0]
        
        type_match = failure.failure_type == sample['expected_type']
        module_match = (
            sample['expected_module'] is None or 
            failure.affected_module == sample['expected_module']
        )
        
        if type_match and module_match:
            print(f"  PASS: {sample['name']} - {sample['expected_type'].value}")
        else:
            actual_type = failure.failure_type.value
            actual_module = failure.affected_module
            print(f"  FAIL: {sample['name']}")
            print(f"        Expected: type={sample['expected_type'].value}, module={sample['expected_module']}")
            print(f"        Got: type={actual_type}, module={actual_module}")
            all_passed = False
    
    print("-" * 60)
    
    report = analyze_log("This is not a traceback", alt_targets)
    if report.total_failures == 0:
        print("  PASS: empty/invalid input handling")
    else:
        print("  FAIL: empty/invalid input handling")
        all_passed = False
    
    return all_passed


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Import Failure Analysis Diagnostic Utility',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Exit codes