#!/usr/bin/env python3
"""
Smoke Cascade Diagnostic

Diagnostic utility to trace import chain failures causing cascading smoke failures in:
- registry_api.py
- rug_pull_monitor.py
- signal_analyser.py

All three modules reportedly show identical traceback pattern: line 10, frozen importlib.
This diagnostic identifies the actual root cause module in the import chain.

NO DB writes — purely diagnostic, read-only operation.
"""

import subprocess
import sys
import re
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass, field


@dataclass
class ImportResult:
    """Result of an import attempt."""
    module_name: str
    success: bool
    returncode: int
    stdout: str
    stderr: str
    exception_type: Optional[str] = None
    exception_msg: Optional[str] = None
    traceback_segments: List[str] = field(default_factory=list)
    cause_chain: List[str] = field(default_factory=list)
    context_chain: List[str] = field(default_factory=list)
    suppress_context: bool = False
    frames: List[str] = field(default_factory=list)


# Target modules to diagnose
TARGET_MODULES = [
    "registry_api",
    "rug_pull_monitor",
    "signal_analyser"
]


def get_import_trace(module_name: str) -> ImportResult:
    """
    Attempt to import a module and capture full exception chain including
    __cause__, __context__, and __suppress_context__.
    """
    # Comprehensive test script that captures all chain information
    test_script = f'''
import sys
import traceback

try:
    import {module_name}
    print("__IMPORT_SUCCESS__")
except Exception as e:
    # Print full traceback
    traceback.print_exc()
    
    # Separator for chain components
    print("__CHAIN_SEPARATOR__")
    
    # Print __cause__ chain
    cause = e.__cause__
    while cause is not None:
        print("__CAUSE_ITEM__")
        traceback.print_exception(type(cause), cause, cause.__traceback__)
        cause = cause.__cause__
    
    print("__CHAIN_SEPARATOR__")
    
    # Print __context__ chain
    ctx = e.__context__
    ctx_seen = set()
    while ctx is not None and id(ctx) not in ctx_seen:
        ctx_seen.add(id(ctx))
        print("__CONTEXT_ITEM__")
        traceback.print_exception(type(ctx), ctx, ctx.__traceback__)
        ctx = ctx.__context__
    
    print("__CHAIN_SEPARATOR__")
    print(f"__SUPPRESS_CONTEXT__: {{e.__suppress_context__}}")
'''

    result = subprocess.run(
        [sys.executable, "-c", test_script],
        capture_output=True,
        text=True
    )

    import_result = ImportResult(
        module_name=module_name,
        success=result.returncode == 0 and "__IMPORT_SUCCESS__" in result.stdout,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr
    )

    if not import_result.success:
        parse_import_failure(import_result, result.stdout, result.stderr)

    return import_result


def parse_import_failure(result: ImportResult, stdout: str, stderr: str) -> None:
    """Parse the failure output to extract exception chain information."""
    combined = stdout + "\n" + stderr

    # Split by separator to get traceback segments
    if "__CHAIN_SEPARATOR__" in combined:
        parts = combined.split("__CHAIN_SEPARATOR__")

        for part in parts:
            if "Traceback (most recent call last)" in part:
                result.traceback_segments.append(part.strip())
    else:
        # Fallback: just use stderr if no separators found
        if stderr:
            result.traceback_segments.append(stderr.strip())

    # Parse __cause__ chain
    if "__CAUSE_ITEM__" in combined:
        causes = combined.split("__CAUSE_ITEM__")
        for cause in causes:
            if cause.strip() and "Traceback" in cause:
                result.cause_chain.append(cause.strip())

    # Parse __context__ chain
    if "__CONTEXT_ITEM__" in combined:
        contexts = combined.split("__CONTEXT_ITEM__")
        for ctx in contexts:
            if ctx.strip() and "Traceback" in ctx:
                result.context_chain.append(ctx.strip())

    # Parse suppress context flag
    suppress_match = re.search(r"__SUPPRESS_CONTEXT__: (True|False)", combined)
    if suppress_match:
        result.suppress_context = suppress_match.group(1) == "True"

    # Extract exception type and message from first traceback
    if result.traceback_segments:
        first_tb = result.traceback_segments[0]
        exc_match = re.search(r"(\w+(?:\.\w+)*(?:Error|Exception|Warning)): (.+)$", first_tb, re.MULTILINE)
        if exc_match:
            result.exception_type = exc_match.group(1)
            result.exception_msg = exc_match.group(2)

    # Extract frames from traceback
    result.frames = extract_frames(combined)


def extract_frames(combined_output: str) -> List[str]:
    """Extract all frames from traceback output."""
    frames = []
    # Match File "..." lines in tracebacks
    frame_pattern = re.compile(r'File "(.*?)", line (\d+), in (.+)')
    for match in frame_pattern.finditer(combined_output):
        frames.append(f"{match.group(1)}:{match.group(2)} in {match.group(3)}")
    return frames


def format_traceback_segment(segment: str, indent: int = 2) -> str:
    """Format a traceback segment for display."""
    lines = segment.split('\n')
    prefix = ' ' * indent
    return '\n'.join(prefix + line if line else '' for line in lines)


def analyze_root_cause(results: List[ImportResult]) -> Dict:
    """Analyze import results to find common root cause across failures."""
    failed_results = [r for r in results if not r.success]

    if not failed_results:
        return {"type": "none", "modules": []}

    # Collect all frames from failed imports
    all_frames = []
    for result in failed_results:
        all_frames.extend(result.frames)

    # Count frame occurrences
    frame_counts: Dict[str, int] = {}
    for frame in all_frames:
        frame_counts[frame] = frame_counts.get(frame, 0) + 1

    # Find frames that appear in multiple failed imports
    common_frames = {f: c for f, c in frame_counts.items() if c >= 2}

    # Check for identical tracebacks
    tb_signatures = {}
    for result in failed_results:
        if result.traceback_segments:
            tb_sig = hash(tuple(result.traceback_segments))
            if tb_sig not in tb_signatures:
                tb_signatures[tb_sig] = []
            tb_signatures[tb_sig].append(result.module_name)

    identical_tbs = [mods for mods in tb_signatures.values() if len(mods) >= 2]

    # Extract module names from frames
    import_modules = set()
    for frame in all_frames:
        # Try to extract the module being imported
        match = re.search(r'<frozen (\w+)>', frame)
        if match:
            import_modules.add(match.group(1))
        match = re.search(r'File "([^"]+/|\.)?(\w+(?:_\w+)*)\.py"', frame)
        if match:
            import_modules.add(match.group(2))

    return {
        "type": "root_cause",
        "modules": list(import_modules),
        "failed_modules": [r.module_name for r in failed_results],
        "common_frames": common_frames,
        "identical_tracebacks": identical_tbs,
        "frame_counts": frame_counts
    }


def print_header() -> None:
    """Print diagnostic header."""
    print("=" * 70)
    print("SMOKE CASCADE DIAGNOSTIC")
    print("=" * 70)
    print()
    print("Purpose: Trace import chain failures causing cascading smoke failures")
    print()
    print("Target Modules:")
    for mod in TARGET_MODULES:
        print(f"  - {mod}")
    print()
    print("=" * 70)
    print()


def print_result(result: ImportResult) -> None:
    """Print detailed result for a single module import test."""
    print(f"{'-' * 70}")
    print(f"  Module: {result.module_name}")

    if result.success:
        print(f"  Status: [+ PASS] - Import successful")
    else:
        print(f"  Status: [! FAIL] - Import failed")

    print(f"  Return Code: {result.returncode}")
    print()

    # Print exception type and message
    if result.exception_type:
        print("  Exception Type:")
        print(f"    {result.exception_type}")
        if result.exception_msg:
            print("  Exception Message:")
            print(f"    {result.exception_msg}")
        print()

    # Print main traceback
    if result.traceback_segments:
        print("  Traceback (direct exception):")
        for i, segment in enumerate(result.traceback_segments):
            print(f"    --- Traceback Level {i + 1} ---")
            print(format_traceback_segment(segment, indent=4))

    # Print __cause__ chain
    if result.cause_chain:
        print()
        print("  Explicit Exception Chain (__cause__):")
        for i, cause in enumerate(result.cause_chain):
            print(f"    --- Cause Level {i + 1} ---")
            print(format_traceback_segment(cause, indent=4))

    # Print __context__ chain
    if result.context_chain:
        print()
        print("  Implicit Exception Chain (__context__):")
        for i, ctx in enumerate(result.context_chain):
            print(f"    --- Context Level {i + 1} ---")
            print(format_traceback_segment(ctx, indent=4))

    # Print __suppress_context__ flag
    if not result.success:
        print()
        print(f"  __suppress_context__: {result.suppress_context}")

    # Print frames summary
    if result.frames:
        print()
        print("  Frames in traceback:")
        for frame in result.frames:
            print(f"    - {frame}")

    print()


def print_summary(analysis: Dict, results: List[ImportResult]) -> None:
    """Print root cause analysis summary."""
    print("=" * 70)
    print("ROOT CAUSE SUMMARY")
    print("=" * 70)
    print()

    failed_results = [r for r in results if not r.success]
    passed_results = [r for r in results if r.success]

    print(f"Total modules tested: {len(results)}")
    print(f"Successful imports: {len(passed_results)}")
    print(f"Failed imports: {len(failed_results)}")
    print()

    if passed_results:
        print("Successfully imported:")
        for r in passed_results:
            print(f"  [+ ] {r.module_name}")
        print()

    if failed_results:
        print("Failed imports:")
        for r in failed_results:
            print(f"  [! ] {r.module_name}")
        print()

    # Analyze and report root cause
    if analysis["type"] == "none":
        print("All imports successful - no root cause analysis needed.")
    elif failed_results:
        print("ROOT CAUSE IDENTIFICATION:")
        print()

        if analysis["identical_tracebacks"]:
            print("  Identical tracebacks detected across modules:")
            for mods in analysis["identical_tracebacks"]:
                print(f"    {', '.join(mods)}")
            print()

        if analysis["modules"]:
            print("  Module(s) involved in failure chain:")
            for mod in analysis["modules"]:
                print(f"    - {mod}")
            print()

        if analysis["common_frames"]:
            print("  Common frames (appear in multiple failures):")
            for frame, count in analysis["common_frames"].items():
                print(f"    - {frame} (appears {count} times)")
            print()

        # Identify the root cause
        # Look for 'frozen' imports or common dependency
        frozen_frames = [f for f in analysis["frame_counts"].keys() if 'frozen' in f.lower()]

        if frozen_frames:
            print("  ROOT CAUSE ANALYSIS:")
            print()
            print("  The following frozen importlib frames appear across failures:")
            for frame in frozen_frames:
                count = analysis["frame_counts"][frame]
                print(f"    {frame} ({count} occurrence(s))")
            print()

            # Check for line 10 specifically (as mentioned in spec)
            line_10_frames = [f for f in frozen_frames if ':10' in f]
            if line_10_frames:
                print("  Line 10 frozen importlib frames detected:")
                for frame in line_10_frames:
                    print(f"    {frame}")
                print()

        # Frame count analysis
        if analysis["frame_counts"]:
            print("  All frames ranked by occurrence:")
            sorted_frames = sorted(analysis["frame_counts"].items(), key=lambda x: -x[1])
            for frame, count in sorted_frames[:10]:
                marker = " <-- ROOT" if count >= len(failed_results) else ""
                print(f"    {frame}: {count} time(s){marker}")
            print()

    print("=" * 70)


def main() -> int:
    """Run the smoke cascade diagnostic."""
    print_header()

    results: List[ImportResult] = []

    for module in TARGET_MODULES:
        print(f"Testing import of '{module}'...")
        result = get_import_trace(module)
        results.append(result)
        print_result(result)

    # Analyze root cause
    analysis = analyze_root_cause(results)

    # Print summary
    print_summary(analysis, results)

    return 0


if __name__ == '__main__':
    sys.exit(main())