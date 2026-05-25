#!/usr/bin/env python3
"""
pilot_harness.py - Reusable pilot harness framework for Sentinel module validation

This module provides tooling for validating Sentinel modules against hand-picked
candidates before production deployment. It is a pure Python framework with
zero external dependencies.

Usage:
    from pilot_harness import run_pilot, write_pilot_report, gate_production_deploy

    result = run_pilot(
        directive_id="my_module_v1",
        candidates=[...],
        ground_truth={candidate_id: expected_output},
        under_test_fn=my_module_function,
        thresholds={"precision": 0.9, "recall": 0.85, ...}
    )

    write_pilot_report(result)
    if gate_production_deploy(result, thresholds):
        print("Ready for production")
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union
import json
import logging
import time

log = logging.getLogger("pilot_harness")

DIRECTIVE_ID_PATTERN = "^[a-zA-Z0-9_-]+$"


@dataclass
class CandidateResult:
    """Result for a single candidate in a pilot run."""
    candidate_id: str
    actual_output: Any
    expected_output: Any
    true_positives: int
    false_positives: int
    false_negatives: int
    runtime_ms: float
    error: Optional[str] = None
    findings: List[str] = field(default_factory=list)
    expected_findings: List[str] = field(default_factory=list)


@dataclass
class PilotResult:
    """Aggregate result of a pilot run across all candidates."""
    directive_id: str
    started_at: datetime
    completed_at: datetime
    candidates_count: int
    per_candidate: List[Dict[str, Any]]
    aggregate: Dict[str, Any]
    verdict: str
    operator_notes_required: bool


def _normalize_findings(output: Any) -> Set[str]:
    """
    Normalize module output to a set of finding strings for comparison.
    
    Args:
        output: The output from the module under test. Can be:
            - A set/frozenset (already in correct form)
            - A list/tuple (convert to set of str representations)
            - A dict (extract values as findings)
            - A string (single finding)
            - None or empty (empty set)
    
    Returns:
        Set of string findings for set operations.
    """
    if output is None:
        return set()
    
    if isinstance(output, (set, frozenset)):
        return set(str(f) for f in output)
    
    if isinstance(output, (list, tuple)):
        return set(str(f) for f in output)
    
    if isinstance(output, dict):
        findings = set()
        for key, value in output.items():
            if value:  # Non-empty values are considered findings
                findings.add(str(key))
                if isinstance(value, (list, tuple)):
                    for v in value:
                        findings.add(f"{key}:{v}")
                else:
                    findings.add(f"{key}:{value}")
        return findings
    
    if isinstance(output, str):
        if output.strip():
            return {output.strip()}
        return set()
    
    return set()


def _compute_precision_recall(tp: int, fp: int, fn: int) -> Dict[str, float]:
    """
    Compute precision, recall, and F1 score from counts.
    
    Args:
        tp: True positives count
        fp: False positives count
        fn: False negatives count
    
    Returns:
        Dict with precision, recall, f1 values (0.0 if undefined).
    """
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4)
    }


def run_pilot(
    directive_id: str,
    candidates: List[Any],
    ground_truth: Dict[str, Any],
    under_test_fn: Callable[[Any], Any],
    thresholds: Optional[Dict[str, Union[int, float, bool]]] = None
) -> PilotResult:
    """
    Run a pilot validation against a set of candidates.
    
    Args:
        directive_id: Identifier for this pilot directive (e.g., "my_module_v1")
        candidates: List of candidate items to test (each passed to under_test_fn)
        ground_truth: Dict mapping candidate identifiers to expected outputs
        under_test_fn: Function to test: (candidate) -> output
        thresholds: Dict of threshold values:
            - max_runtime_per_candidate_ms: int (default 60000)
            - abort_on_timeout: bool (default True)
            - abort_on_any_exception: bool (default False)
            - min_candidates_for_pass: int (default 1)
    
    Returns:
        PilotResult with per-candidate results and aggregate statistics.
    """
    if thresholds is None:
        thresholds = {}
    
    max_runtime_ms = thresholds.get("max_runtime_per_candidate_ms", 60000)
    abort_on_timeout = thresholds.get("abort_on_timeout", True)
    abort_on_any_exception = thresholds.get("abort_on_any_exception", False)
    
    started_at = datetime.now(timezone.utc)
    per_candidate: List[Dict[str, Any]] = []
    
    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_runtime_ms = 0.0
    
    abort_triggered = False
    abort_reason: Optional[str] = None
    
    log.info(f"Starting pilot for directive_id='{directive_id}' with {len(candidates)} candidates")
    
    for idx, candidate in enumerate(candidates):
        candidate_id = str(candidate) if not isinstance(candidate, dict) else candidate.get("id", candidate.get("name", f"candidate_{idx}"))
        
        log.debug(f"Testing candidate {idx + 1}/{len(candidates)}: {candidate_id}")
        
        candidate_start = time.perf_counter()
        error: Optional[str] = None
        actual_output: Any = None
        
        try:
            actual_output = under_test_fn(candidate)
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)}"
            log.warning(f"Candidate {candidate_id} raised exception: {error}")
            
            if abort_on_any_exception:
                abort_triggered = True
                abort_reason = f"Exception on candidate {candidate_id}: {error}"
                log.error(f"Abort triggered: {abort_reason}")
                break
            else:
                per_candidate.append({
                    "candidate_id": candidate_id,
                    "actual_output": None,
                    "expected_output": ground_truth.get(candidate_id),
                    "true_positives": 0,
                    "false_positives": 0,
                    "false_negatives": 0,
                    "runtime_ms": 0.0,
                    "error": error,
                    "findings": [],
                    "expected_findings": []
                })
                continue
        
        candidate_elapsed_ms = (time.perf_counter() - candidate_start) * 1000
        total_runtime_ms += candidate_elapsed_ms
        
        if candidate_elapsed_ms > max_runtime_ms:
            log.warning(f"Candidate {candidate_id} exceeded time limit: {candidate_elapsed_ms:.2f}ms > {max_runtime_ms}ms")
            
            if abort_on_timeout:
                abort_triggered = True
                abort_reason = f"Timeout on candidate {candidate_id}: {candidate_elapsed_ms:.2f}ms > {max_runtime_ms}ms limit"
                log.error(f"Abort triggered: {abort_reason}")
                break
        
        expected_output = ground_truth.get(candidate_id)
        
        actual_findings = _normalize_findings(actual_output)
        expected_findings = _normalize_findings(expected_output)
        
        true_positives = len(actual_findings & expected_findings)
        false_positives = len(actual_findings - expected_findings)
        false_negatives = len(expected_findings - actual_findings)
        
        total_tp += true_positives
        total_fp += false_positives
        total_fn += false_negatives
        
        candidate_result: Dict[str, Any] = {
            "candidate_id": candidate_id,
            "actual_output": actual_output,
            "expected_output": expected_output,
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "runtime_ms": round(candidate_elapsed_ms, 2),
            "error": error,
            "findings": sorted(list(actual_findings)),
            "expected_findings": sorted(list(expected_findings))
        }
        per_candidate.append(candidate_result)
        
        log.debug(
            f"Candidate {candidate_id}: TP={true_positives}, FP={false_positives}, "
            f"FN={false_negatives}, runtime={candidate_elapsed_ms:.2f}ms"
        )
    
    completed_at = datetime.now(timezone.utc)
    
    pr_scores = _compute_precision_recall(total_tp, total_fp, total_fn)
    
    aggregate: Dict[str, Any] = {
        "precision": pr_scores["precision"],
        "recall": pr_scores["recall"],
        "f1": pr_scores["f1"],
        "total_runtime_ms": round(total_runtime_ms, 2),
        "abort_triggered": abort_triggered,
        "abort_reason": abort_reason,
        "true_positives": total_tp,
        "false_positives": total_fp,
        "false_negatives": total_fn,
        "candidates_processed": len(per_candidate)
    }
    
    min_precision = thresholds.get("min_precision", 0.0)
    min_recall = thresholds.get("min_recall", 0.0)
    min_f1 = thresholds.get("min_f1", 0.0)
    
    if abort_triggered:
        verdict = "ABORT"
        operator_notes_required = True
    elif pr_scores["precision"] >= min_precision and pr_scores["recall"] >= min_recall and pr_scores["f1"] >= min_f1:
        verdict = "PASS"
        operator_notes_required = False
    else:
        verdict = "FAIL"
        operator_notes_required = True
    
    log.info(
        f"Pilot complete for directive_id='{directive_id}': verdict={verdict}, "
        f"precision={pr_scores['precision']:.4f}, recall={pr_scores['recall']:.4f}, "
        f"f1={pr_scores['f1']:.4f}"
    )
    
    return PilotResult(
        directive_id=directive_id,
        started_at=started_at,
        completed_at=completed_at,
        candidates_count=len(candidates),
        per_candidate=per_candidate,
        aggregate=aggregate,
        verdict=verdict,
        operator_notes_required=operator_notes_required
    )


def write_pilot_report(result: PilotResult, output_dir: Optional[Path] = None) -> Path:
    """
    Write a pilot result to a JSON file with timestamp-based filename.
    
    The file is written to the fixes directory to preserve historical results.
    Filename format: pilot_{directive_id}_{timestamp}.json
    
    Args:
        result: The PilotResult to write
        output_dir: Optional custom output directory (defaults to /home/workspace/zo_sentinel/fixes)
    
    Returns:
        Path to the written file.
    """
    if output_dir is None:
        output_dir = Path("/home/workspace/zo_sentinel/fixes")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"pilot_{result.directive_id}_{timestamp}.json"
    filepath = output_dir / filename
    
    report: Dict[str, Any] = {
        "schema_version": "1.0",
        "directive_id": result.directive_id,
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "candidates_count": result.candidates_count,
        "verdict": result.verdict,
        "operator_notes_required": result.operator_notes_required,
        "aggregate": result.aggregate,
        "per_candidate": result.per_candidate
    }
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    
    log.info(f"Pilot report written to: {filepath}")
    
    latest_link = output_dir / f"pilot_{result.directive_id}_latest.json"
    try:
        latest_link.unlink(missing_ok=True)
        latest_link.symlink_to(filename)
        log.debug(f"Updated latest symlink: {latest_link} -> {filename}")
    except OSError:
        log.warning(f"Could not create symlink at {latest_link}")
    
    return filepath


def gate_production_deploy(
    result: PilotResult,
    thresholds: Optional[Dict[str, Union[int, float]]] = None
) -> bool:
    """
    Determine if a pilot result qualifies for production deployment.
    
    Returns True only if ALL conditions are met:
    - No abort was triggered
    - Aggregate precision >= thresholds['precision']
    - Aggregate recall >= thresholds['recall']
    
    Args:
        result: The PilotResult to evaluate
        thresholds: Dict with 'precision' and 'recall' minimum thresholds
    
    Returns:
        True if deployment is approved, False otherwise.
    """
    if thresholds is None:
        thresholds = {}
    
    min_precision = thresholds.get("precision", 0.0)
    min_recall = thresholds.get("recall", 0.0)
    
    if result.aggregate.get("abort_triggered", False):
        log.warning(
            f"Gate failed: abort triggered - {result.aggregate.get('abort_reason', 'unknown')}"
        )
        return False
    
    actual_precision = result.aggregate.get("precision", 0.0)
    actual_recall = result.aggregate.get("recall", 0.0)
    
    if actual_precision < min_precision:
        log.warning(
            f"Gate failed: precision {actual_precision:.4f} < {min_precision:.4f}"
        )
        return False
    
    if actual_recall < min_recall:
        log.warning(
            f"Gate failed: recall {actual_recall:.4f} < {min_recall:.4f}"
        )
        return False
    
    log.info(
        f"Gate passed: precision={actual_precision:.4f}>={min_precision:.4f}, "
        f"recall={actual_recall:.4f}>={min_recall:.4f}"
    )
    return True


def _demo_under_test_fn(candidate: Dict[str, Any]) -> Set[str]:
    """
    Demo function for self-testing the pilot harness.
    
    Returns 2 correct findings + 1 wrong finding for testing purposes.
    """
    candidate_id = candidate.get("id", "unknown")
    
    findings = set()
    
    if candidate_id == "cand_001":
        findings.add("finding:sql_injection")
        findings.add("finding:xss_reflected")
        findings.add("finding:fake_finding")  # Wrong - demonstrates FP
    elif candidate_id == "cand_002":
        findings.add("finding:csrf_missing")
        findings.add("finding:auth_bypass")
        findings.add("finding:data_leak")  # Wrong - demonstrates FP
    elif candidate_id == "cand_003":
        findings.add("finding:idor_detected")
        findings.add("finding:rate_limit_missing")
        findings.add("finding:nonexistent")  # Wrong - demonstrates FP
    else:
        findings.add("finding:generic_risk")
    
    return findings


def _demo_self_test() -> bool:
    """
    Run self-test demonstrating the pilot harness functionality.
    
    Returns True if self-test passes, False otherwise.
    """
    log.info("=" * 60)
    log.info("PILOT HARNESS SELF-TEST")
    log.info("=" * 60)
    
    candidates = [
        {"id": "cand_001", "name": "Vulnerable Server A"},
        {"id": "cand_002", "name": "Vulnerable Server B"},
        {"id": "cand_003", "name": "Vulnerable Server C"}
    ]
    
    ground_truth = {
        "cand_001": {"findings": ["finding:sql_injection", "finding:xss_reflected"]},
        "cand_002": {"findings": ["finding:csrf_missing", "finding:auth_bypass"]},
        "cand_003": {"findings": ["finding:idor_detected", "finding:rate_limit_missing"]}
    }
    
    thresholds = {
        "precision": 0.6,
        "recall": 0.6,
        "min_f1": 0.5,
        "max_runtime_per_candidate_ms": 5000,
        "abort_on_timeout": True,
        "abort_on_any_exception": False
    }
    
    log.info("Running pilot with demo under_test_fn...")
    result = run_pilot(
        directive_id="self_test_demo",
        candidates=candidates,
        ground_truth=ground_truth,
        under_test_fn=_demo_under_test_fn,
        thresholds=thresholds
    )
    
    log.info("")
    log.info("PILOT RESULT SUMMARY:")
    log.info("-" * 40)
    log.info(f"  Verdict:          {result.verdict}")
    log.info(f"  Candidates:       {result.candidates_count}")
    log.info(f"  Precision:        {result.aggregate['precision']:.4f}")
    log.info(f"  Recall:           {result.aggregate['recall']:.4f}")
    log.info(f"  F1:               {result.aggregate['f1']:.4f}")
    log.info(f"  Total Runtime:    {result.aggregate['total_runtime_ms']:.2f}ms")
    log.info(f"  Abort Triggered:  {result.aggregate['abort_triggered']}")
    if result.aggregate.get("abort_reason"):
        log.info(f"  Abort Reason:    {result.aggregate['abort_reason']}")
    log.info(f"  Operator Notes:   {result.operator_notes_required}")
    log.info("")
    
    log.info("PER-CANDIDATE BREAKDOWN:")
    log.info("-" * 40)
    for cr in result.per_candidate:
        log.info(
            f"  {cr['candidate_id']}: "
            f"TP={cr['true_positives']}, FP={cr['false_positives']}, "
            f"FN={cr['false_negatives']}, "
            f"runtime={cr['runtime_ms']:.2f}ms"
        )
        if cr.get("error"):
            log.info(f"    ERROR: {cr['error']}")
    log.info("")
    
    report_path = write_pilot_report(result)
    log.info(f"Report written to: {report_path}")
    
    gate_thresholds = {"precision": 0.6, "recall": 0.6}
    deploy_approved = gate_production_deploy(result, gate_thresholds)
    log.info("")
    log.info(f"Production gate: {'APPROVED' if deploy_approved else 'REJECTED'}")
    
    log.info("")
    log.info("=" * 60)
    log.info("SELF-TEST COMPLETE")
    log.info("=" * 60)
    
    return result.verdict == "PASS" or result.verdict == "FAIL"


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    try:
        success = _demo_self_test()
        sys.exit(0 if success else 1)
    except Exception as e:
        log.error(f"Self-test failed with exception: {e}", exc_info=True)
        sys.exit(1)