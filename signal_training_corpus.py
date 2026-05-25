#!/usr/bin/env python3
"""
Signal Training Corpus Writer

RLSD CONTEXT (for eventual training loop):
  (1) STUDENT: small model (Qwen 0.5B / Phi-3 mini) producing scores
  (2) PRIVILEGED TEACHER: same architecture with frontier-grade prompting
      (full MCP fingerprint, README, tool schemas, network observations)
      providing dense token-level magnitude signals
  (3) ENVIRONMENT: existing frontier model (Sonnet/Opus via escalation.py)
      producing sparse correctness direction signal

This module captures paired (student, teacher) examples to populate roles 2 and 3.

MODULE CONTRACT:
  - Captures signal-scoring examples for future RLSD-style training
  - Writes to mcp_signal_training_corpus table via write_service
  - Works in two modes: live capture and backfill
  - NEVER blocks production scorer
  - Decoupled from production scorer for isolated testing
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

# -------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
CORPUS_TABLE = "mcp_signal_training_corpus"
FAILURE_LOG = "/home/workspace/logs/signal_training_corpus_failures.log"
REQUEST_TIMEOUT = 10

# Ensure log directory exists
Path("/home/workspace/logs").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("signal_training_corpus")


# -------------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------------
def _get_iso_now() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _compute_magnitude_delta(student_scores: dict, teacher_scores: dict) -> dict:
    """
    Compute per-signal numeric deltas: student minus teacher.
    This provides the dense token-level magnitude signal for teacher training.
    """
    deltas = {}
    all_signals = set(student_scores.keys()) | set(teacher_scores.keys())
    for signal in all_signals:
        student_val = student_scores.get(signal, 0)
        teacher_val = teacher_scores.get(signal, 0)
        deltas[signal] = student_val - teacher_val
    return deltas


def _serialize(obj: Any) -> str:
    """Serialize object to JSON string, handling non-serializable types."""
    return json.dumps(obj, default=str, separators=(",", ":"))


# -------------------------------------------------------------------------
# TrainingCorpusWriter
# -------------------------------------------------------------------------
class TrainingCorpusWriter:
    """
    Captures paired (student, teacher) signal-scoring examples for RLSD training.

    This class is designed to be non-blocking and resilient. If write_service
    is unavailable, it logs failures to disk and returns silently rather than
    raising exceptions.

    Usage:
        writer = TrainingCorpusWriter()
        writer.capture_pair(
            mcp_id="server_123",
            fingerprint_dict={"name": "xyz", "fingerprint": "sha256:..."},
            student_score={"trust_score": 0.7, "signal_safety": 0.8},
            teacher_score={"trust_score": 0.72, "signal_safety": 0.85},
            env_verdict="agree",
            metadata={"source": "production"}
        )

    Attributes:
        _table_initialized: Tracks whether table creation has been attempted.
    """

    def __init__(self):
        """
        Initialize the writer with table initialization deferred to first capture.
        This allows the class to be instantiated without immediate DB overhead.
        """
        self._table_initialized = False

    # -------------------------------------------------------------------------
    # Table management
    # -------------------------------------------------------------------------
    def _ensure_table(self) -> bool:
        """
        Ensure the mcp_signal_training_corpus table exists.
        Uses CREATE TABLE IF NOT EXISTS via write_service execute.
        This is idempotent - safe to call multiple times.

        Returns:
            True if table exists or was created, False on failure.
        """
        if self._table_initialized:
            return True

        create_sql = f"""
        CREATE TABLE IF NOT EXISTS {CORPUS_TABLE} (
            mcp_id VARCHAR,
            captured_at TIMESTAMP,
            fingerprint_json VARCHAR,
            student_score_json VARCHAR,
            teacher_score_json VARCHAR,
            env_verdict VARCHAR,
            magnitude_delta_json VARCHAR,
            metadata_json VARCHAR,
            PRIMARY KEY (mcp_id, captured_at)
        )
        """

        try:
            response = requests.post(
                EXECUTE_SERVICE_URL,
                json={"sql": create_sql},
                timeout=REQUEST_TIMEOUT
            )
            if response.status_code == 200:
                self._table_initialized = True
                logger.info("Training corpus table initialized")
                return True
            else:
                logger.error(
                    f"Table creation failed: {response.status_code} {response.text}"
                )
                return False
        except requests.RequestException as e:
            logger.error(f"Failed to create table: {e}")
            return False

    # -------------------------------------------------------------------------
    # Core capture method
    # -------------------------------------------------------------------------
    def capture_pair(
        self,
        mcp_id: str,
        fingerprint_dict: dict,
        student_score: dict,
        teacher_score: dict,
        env_verdict: str = "unknown",
        metadata: Optional[dict] = None
    ) -> bool:
        """
        Capture a paired (student, teacher) example to the training corpus.

        This method is designed for non-blocking operation. If write_service
        is down, it logs to the failure log and returns False silently.

        Args:
            mcp_id: Unique identifier for the MCP being scored.
            fingerprint_dict: Full MCP context (fingerprint, README, etc.).
                This provides the privileged context for teacher scoring.
            student_score: Scores from the small/cheap model (STUDENT role).
                Expected format: {"trust_score": 0.7, "signal_safety": 0.8, ...}
            teacher_score: Scores from the frontier teacher model (TEACHER role).
                May be filled lazily by a separate enrichment daemon in live mode.
            env_verdict: Sparse direction signal from ENVIRONMENT role.
                Values: 'agree' (student/teacher agree within tolerance),
                        'disagree' (materially differ),
                        'unknown' (no env signal yet).
            metadata: Optional additional metadata for the capture.

        Returns:
            True if capture succeeded, False otherwise.
        """
        # Ensure table exists (idempotent)
        if not self._ensure_table():
            self._log_failure(
                mcp_id,
                fingerprint_dict,
                student_score,
                teacher_score,
                env_verdict,
                metadata,
                "Table initialization failed"
            )
            return False

        # Serialize all score dictionaries to JSON strings for storage
        student_score_json = _serialize(student_score)
        teacher_score_json = _serialize(teacher_score)
        fingerprint_json = _serialize(fingerprint_dict)
        metadata_json = _serialize(metadata or {})

        # Compute per-signal magnitude deltas (student - teacher)
        # This is the dense token-level signal used to train the teacher
        magnitude_delta = _compute_magnitude_delta(student_score, teacher_score)
        magnitude_delta_json = _serialize(magnitude_delta)

        # Generate timestamp for captured_at
        captured_at = _get_iso_now()

        # Prepare the row data for write_service
        row = {
            "mcp_id": mcp_id,
            "captured_at": captured_at,
            "fingerprint_json": fingerprint_json,
            "student_score_json": student_score_json,
            "teacher_score_json": teacher_score_json,
            "env_verdict": env_verdict,
            "magnitude_delta_json": magnitude_delta_json,
            "metadata_json": metadata_json
        }

        # Attempt to write via write_service
        try:
            response = requests.post(
                WRITE_SERVICE_URL,
                json={"table": CORPUS_TABLE, "rows": [row], "wait": True},
                timeout=REQUEST_TIMEOUT
            )
            if response.status_code == 200:
                logger.debug(f"Captured training pair for {mcp_id}")
                return True
            else:
                logger.error(
                    f"Write failed for {mcp_id}: "
                    f"{response.status_code} {response.text}"
                )
                self._log_failure(
                    mcp_id,
                    fingerprint_dict,
                    student_score,
                    teacher_score,
                    env_verdict,
                    metadata,
                    f"Write failed: {response.text}"
                )
                return False
        except requests.RequestException as e:
            # Non-blocking: log failure and return silently
            logger.warning(f"Write service unavailable: {e}")
            self._log_failure(
                mcp_id,
                fingerprint_dict,
                student_score,
                teacher_score,
                env_verdict,
                metadata,
                f"Service unavailable: {e}"
            )
            return False

    # -------------------------------------------------------------------------
    # Flush (reserved for future batched-write optimization)
    # -------------------------------------------------------------------------
    def flush(self) -> None:
        """
        No-op for now.

        Reserved for future batched-write optimization. Currently, each
        capture_pair call writes immediately. This method will be used
        when batch buffering is implemented.
        """
        pass

    # -------------------------------------------------------------------------
    # Disagreement estimation for manual triage
    # -------------------------------------------------------------------------
    def estimate_disagreement(self, mcp_id: str) -> int:
        """
        Query the corpus and return the count of 'disagree' rows for a given MCP.

        Used by manual triage workflows to find MCPs where the student model
        reliably differs from the teacher. High disagreement counts indicate
        the student model needs retraining or the MCP has edge-case behavior.

        Args:
            mcp_id: The MCP identifier to check disagreement for.

        Returns:
            Count of disagree rows. Returns 0 if query fails or none found.
        """
        query_sql = f"""
        SELECT COUNT(*) AS disagree_count
        FROM {CORPUS_TABLE}
        WHERE mcp_id = '{mcp_id}'
          AND env_verdict = 'disagree'
        """

        try:
            response = requests.post(
                QUERY_SERVICE_URL,
                json={"sql": query_sql},
                timeout=REQUEST_TIMEOUT
            )
            if response.status_code == 200:
                result = response.json()
                rows = result.get("rows", [])
                if rows and "disagree_count" in rows[0]:
                    return int(rows[0]["disagree_count"])
            return 0
        except requests.RequestException as e:
            logger.error(f"Query failed for estimate_disagreement: {e}")
            return 0

    # -------------------------------------------------------------------------
    # Integration helper
    # -------------------------------------------------------------------------
    @classmethod
    def integration_hint(cls) -> str:
        """
        Return a multi-line string showing exactly the import + capture_pair
        call a downstream daemon should add to wire this in.

        Returns:
            Multi-line string with import statement and usage example.
        """
        return '''# Integration example for downstream daemons:
# Add these lines to your scoring logic:

from zo_sentinel.signal_training_corpus import TrainingCorpusWriter

# Instantiate once per process (consider making it a module-level singleton)
corpus_writer = TrainingCorpusWriter()

# Inside your scoring logic, after computing student and teacher scores:
# (student_score comes from your production scorer,
#  teacher_score may be filled lazily by a separate enrichment daemon)

corpus_writer.capture_pair(
    mcp_id=mcp_id,
    fingerprint_dict=fingerprint,  # your full MCP context dict
    student_score=student_scores,   # small model's scores
    teacher_score=teacher_scores,   # frontier teacher's scores
    env_verdict='unknown',          # or 'agree'/'disagree' from escalation
    metadata={'source': 'live_capture', 'batch': 1}
)

# For manual triage to find problematic MCPs:
disagree_count = corpus_writer.estimate_disagreement(mcp_id)
if disagree_count > 10:
    logger.warning(f"MCP {mcp_id} has {disagree_count} disagreements")
'''

    # -------------------------------------------------------------------------
    # Failure logging
    # -------------------------------------------------------------------------
    def _log_failure(
        self,
        mcp_id: str,
        fingerprint_dict: dict,
        student_score: dict,
        teacher_score: dict,
        env_verdict: str,
        metadata: Optional[dict],
        error_message: str
    ) -> None:
        """
        Log a failed capture attempt to the failure log file.

        This allows recovery and reprocessing of failed captures when
        write_service becomes available again.

        Args:
            mcp_id: The MCP identifier.
            fingerprint_dict: The MCP fingerprint context.
            student_score: The student model scores.
            teacher_score: The teacher model scores.
            env_verdict: The environment verdict.
            metadata: Additional metadata.
            error_message: The error that caused the failure.
        """
        failure_entry = {
            "timestamp": _get_iso_now(),
            "mcp_id": mcp_id,
            "fingerprint_json": _serialize(fingerprint_dict),
            "student_score_json": _serialize(student_score),
            "teacher_score_json": _serialize(teacher_score),
            "env_verdict": env_verdict,
            "metadata_json": _serialize(metadata or {}),
            "error": error_message
        }
        try:
            with open(FAILURE_LOG, "a") as f:
                f.write(_serialize(failure_entry) + "\n")
        except IOError as e:
            logger.error(f"Could not write to failure log: {e}")


# -------------------------------------------------------------------------
# Self-test
# -------------------------------------------------------------------------
if __name__ == "__main__":
    """
    Self-test: capture one synthetic example, verify row landed, exit 0 on success.
    """
    import sys

    logger.info("Starting TrainingCorpusWriter self-test...")

    # Hardcoded synthetic scores for self-test
    test_mcp_id = "test_mcp_self_test_001"
    test_fingerprint = {
        "server_id": test_mcp_id,
        "name": "test-mcp-self-test",
        "fingerprint": "sha256:abc123def456",
        "description": "Synthetic MCP for self-test",
        "url": "https://example.com/mcp/test"
    }
    test_student_score = {
        "trust_score": 0.7,
        "signal_safety": 0.8,
        "signal_privacy": 0.6,
        "signal_reliability": 0.75,
        "signal_maintenance": 0.65,
        "signal_network": 0.7
    }
    test_teacher_score = {
        "trust_score": 0.72,
        "signal_safety": 0.82,
        "signal_privacy": 0.58,
        "signal_reliability": 0.78,
        "signal_maintenance": 0.68,
        "signal_network": 0.72
    }

    writer = TrainingCorpusWriter()

    # Capture the synthetic pair
    logger.info(f"Capturing synthetic pair for MCP: {test_mcp_id}")
    success = writer.capture_pair(
        mcp_id=test_mcp_id,
        fingerprint_dict=test_fingerprint,
        student_score=test_student_score,
        teacher_score=test_teacher_score,
        env_verdict="unknown",
        metadata={"test": True, "self_test": True}
    )

    if not success:
        logger.error("Self-test FAILED: capture_pair returned False")
        sys.exit(1)

    # Verify the row landed by querying
    query_sql = f"""
    SELECT mcp_id, captured_at, env_verdict, metadata_json
    FROM {CORPUS_TABLE}
    WHERE mcp_id = '{test_mcp_id}'
    ORDER BY captured_at DESC
    LIMIT 1
    """

    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": query_sql},
            timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 200:
            result = response.json()
            rows = result.get("rows", [])
            if rows and len(rows) > 0:
                logger.info(f"Self-test PASSED: row found: {rows[0]}")
                logger.info("TrainingCorpusWriter self-test SUCCESS")
                sys.exit(0)
            else:
                logger.error("Self-test FAILED: no row found after capture")
                sys.exit(1)
        else:
            logger.error(
                f"Self-test FAILED: query returned {response.status_code}"
            )
            sys.exit(1)
    except requests.RequestException as e:
        logger.error(f"Self-test FAILED: query request failed: {e}")
        sys.exit(1)