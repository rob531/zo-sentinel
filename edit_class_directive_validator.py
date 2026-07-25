import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "edit_class_directive_validator"
SERVICE_PORT = 0
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


def get_write_url():
    return WRITE_SERVICE_URL


def get_query_url():
    return QUERY_SERVICE_URL


def get_execute_url():
    return EXECUTE_SERVICE_URL


def ws_write(table, rows):
    url = get_write_url()
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql):
    url = get_query_url()
    payload = {"sql": sql}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql):
    url = get_execute_url()
    payload = {"sql": sql}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def check_single_instance():
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            log.warning(f"Stale PID file found for {old_pid}, removing")
            pid_path.unlink()
    pid_path.write_text(str(os.getpid()))


def remove_pid_file():
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    log.info(f"Received {sig_name}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status="running", meta=None):
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta or {},
    }
    try:
        ws_write("service_health", [row])
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


class DirectiveSchema:
    VALID_HANDLERS = {
        "signal_analyser",
        "mcp_scanner",
        "trust_synthesiser",
        "attestation_engine",
        "threat_intel_ingestor",
        "rug_pull_monitor",
        "policy_engine",
        "risk_ranker",
        "deduplicator",
        "search_api",
        "registry_api",
        "approval_workflow",
        "mcp_fingerprinter",
        "trend_analyser",
        "dashboard_api",
        "alert_manager",
        "webhook_dispatcher",
        "audit_trail",
        "backup_service",
        "retention_sweeper",
        "exemption_expirer",
        "stale_data_cleaner",
        "pattern_learner",
        "false_positive_tracker",
        "trend_analyser",
        "cve_enricher",
        "similarity_scorer",
        "behavioral_analyser",
        "anomaly_detector",
        "threat_correlator",
        "npm_typo_squatter",
        "prompt_injection_scanner",
        "context_manipulation_detector",
        "sybil_burst_detector",
        "mcp_impersonation_detector",
        "dependency_chain_auditor",
        "threat_feed_aggregator",
        "cross_registry_correlator",
        "runtime_behaviour_profiler",
        "mcp_age_risk_scorer",
        "approval_anomaly_detector",
        "vendor_concentration_monitor",
        "certificate_analyser",
        "manual_override_api",
        "advanced_filter_api",
        "forensic_detail_api",
        "email_guid_auth",
        "compliance_export_service",
        "api_gateway",
        "metrics_exporter",
        "snow_connector",
        "github_pr_checker",
        "mesh_bridge",
        "registry_reconciler",
        "auto_dependency_resolver",
        "graphql_schema_builder",
        "pi_corpus_ingest",
        "pi_scorer",
        "pi_harness_runner",
        "pi_quarantine_reviewer",
        "pi_quarantine_promoter",
        "attestation_refresher",
        "signal_enrichment_aggregator",
        "supply_chain_enrichment",
        "domain_trust_enrichment",
        "community_signal_enrichment",
        "temporal_stability_enrichment",
        "permission_scope_enrichment",
        "tool_description_safety_enrichment",
        "injection_resilience_enrichment",
        "context_efficiency_enrichment",
        "evidence_density_enrichment",
        "registry_breadth_enrichment",
        "vendor_concentration_enrichment",
        "arcade_toolbench_ingestor",
        "directive_validator",
        "directive_queue_health",
        "goose_runner",
        "candidate_promoter",
        "candidate_npm_promoter",
        "candidate_smithery_promoter",
        "candidate_github_promoter",
        "mcp_directory_ingestor",
        "discovery_npm_paginator",
        "discovery_github_paginator",
        "discovery_pypi_paginator",
        "stale_signal_refresher",
        "sft_dataset_preflight",
        "sft_results_drive_watcher",
        "snow_approval_integration",
        "snow_connector_approval_wiring",
        "snow_inbound_webhook",
        "snow_integration",
        "snow_webhook_inbound",
        "github_pr_webhook_handler",
        "github_pr_verdict_gate",
        "aidr_commit_gateway",
        "aidr_verdict_gate",
        "aidr_verdict_enforcer",
        "ui_server",
        "inference_router",
    }

    VALID_COMPLEXITIES = {"low", "medium", "high", "ui", "blocking"}
    REQUIRED_FIELDS = ["task", "description", "handler", "complexity"]
    FORBIDDEN_PATTERNS = [
        r"[<>{}\\]",
        r"\bexec\s*\(",
        r"\beval\s*\(",
        r"__import__",
        r"\brm\s+-rf",
        r"\bsudo\s",
        r"\bchmod\s+777",
    ]
    DESCRIPTION_MIN_WORDS = 5
    DESCRIPTION_MAX_WORDS = 500


class DirectiveValidationError(Exception):
    def __init__(self, field, message, severity="error"):
        self.field = field
        self.message = message
        self.severity = severity
        super().__init__(f"[{field}] {message}")


class EditClassDirectiveValidator:
    def __init__(self):
        self.schema = DirectiveSchema()
        self.validation_count = 0
        self.error_count = 0
        self.warning_count = 0
        self.started_at = utc_now_iso()
        log.info("EditClassDirectiveValidator initialized")

    def validate_directive(self, directive, directive_id=None):
        errors = []
        warnings = []
        task_id = directive_id or directive.get("id", "unknown")

        try:
            self._validate_required_fields(directive, errors)
            self._validate_task_name(directive.get("task", ""), errors)
            self._validate_description(directive.get("description", ""), errors, warnings)
            self._validate_handler(directive.get("handler", ""), errors)
            self._validate_complexity(directive.get("complexity", ""), errors)
            self._validate_idempotency(directive.get("idempotent", True), errors, warnings)
            self._validate_timeout(directive.get("timeout", 300), errors, warnings)
            self._validate_retry_policy(directive.get("retry_policy", {}), errors, warnings)
            self._validate_dependencies(directive.get("depends_on", []), errors)
            self._validate_metadata(directive.get("metadata", {}), warnings)
            self._validate_forbidden_patterns(directive, errors)
        except Exception as e:
            log.error(f"Validation exception for directive {task_id}: {e}")
            errors.append({"field": "general", "message": str(e), "severity": "error"})

        self.validation_count += 1
        self.error_count += len([e for e in errors if e.get("severity") == "error"])
        self.warning_count += len(warnings)

        return {
            "directive_id": task_id,
            "valid": len([e for e in errors if e.get("severity") == "error"]) == 0,
            "errors": errors,
            "warnings": warnings,
            "validated_at": utc_now_iso(),
        }

    def _validate_required_fields(self, directive, errors):
        for field in self.schema.REQUIRED_FIELDS:
            if field not in directive or not directive[field]:
                errors.append({
                    "field": field,
                    "message": f"Required field '{field}' is missing or empty",
                    "severity": "error",
                })

    def _validate_task_name(self, task, errors):
        if not task:
            return
        if not isinstance(task, str):
            errors.append({
                "field": "task",
                "message": "Task must be a string",
                "severity": "error",
            })
            return
        if len(task) < 3:
            errors.append({
                "field": "task",
                "message": "Task name must be at least 3 characters",
                "severity": "error",
            })
        if len(task) > 100:
            errors.append({
                "field": "task",
                "message": "Task name must not exceed 100 characters",
                "severity": "error",
            })
        if not re.match(r"^[a-zA-Z0-9_\-]+$", task):
            errors.append({
                "field": "task",
                "message": "Task name must contain only alphanumeric characters, underscores, and hyphens",
                "severity": "error",
            })

    def _validate_description(self, description, errors, warnings):
        if not description:
            return
        if not isinstance(description, str):
            errors.append({
                "field": "description",
                "message": "Description must be a string",
                "severity": "error",
            })
            return
        word_count = len(description.split())
        if word_count < self.schema.DESCRIPTION_MIN_WORDS:
            errors.append({
                "field": "description",
                "message": f"Description must have at least {self.schema.DESCRIPTION_MIN_WORDS} words (got {word_count})",
                "severity": "error",
            })
        if word_count > self.schema.DESCRIPTION_MAX_WORDS:
            warnings.append({
                "field": "description",
                "message": f"Description is very long ({word_count} words). Consider shortening.",
                "severity": "warning",
            })
        if description.strip() != description:
            warnings.append({
                "field": "description",
                "message": "Description has leading or trailing whitespace",
                "severity": "warning",
            })

    def _validate_handler(self, handler, errors):
        if not handler:
            return
        if not isinstance(handler, str):
            errors.append({
                "field": "handler",
                "message": "Handler must be a string",
                "severity": "error",
            })
            return
        if handler not in self.schema.VALID_HANDLERS:
            errors.append({
                "field": "handler",
                "message": f"Unknown handler '{handler}'. Valid handlers: {', '.join(sorted(self.schema.VALID_HANDLERS))}",
                "severity": "error",
            })

    def _validate_complexity(self, complexity, errors):
        if not complexity:
            return
        if not isinstance(complexity, str):
            errors.append({
                "field": "complexity",
                "message": "Complexity must be a string",
                "severity": "error",
            })
            return
        if complexity not in self.schema.VALID_COMPLEXITIES:
            errors.append({
                "field": "complexity",
                "message": f"Invalid complexity '{complexity}'. Valid values: {', '.join(sorted(self.schema.VALID_COMPLEXITIES))}",
                "severity": "error",
            })

    def _validate_idempotency(self, idempotent, errors, warnings):
        if not isinstance(idempotent, bool):
            warnings.append({
                "field": "idempotent",
                "message": "idempotent should be a boolean value",
                "severity": "warning",
            })

    def _validate_timeout(self, timeout, errors, warnings):
        if not isinstance(timeout, (int, float)):
            errors.append({
                "field": "timeout",
                "message": "timeout must be a number",
                "severity": "error",
            })
            return
        if timeout < 0:
            errors.append({
                "field": "timeout",
                "message": "timeout cannot be negative",
                "severity": "error",
            })
        if timeout > 3600:
            warnings.append({
                "field": "timeout",
                "message": "timeout exceeds 1 hour. Consider breaking into smaller tasks.",
                "severity": "warning",
            })
        if timeout < 10:
            warnings.append({
                "field": "timeout",
                "message": "timeout is less than 10 seconds. Task may not complete.",
                "severity": "warning",
            })

    def _validate_retry_policy(self, retry_policy, errors, warnings):
        if not isinstance(retry_policy, dict):
            errors.append({
                "field": "retry_policy",
                "message": "retry_policy must be a dictionary",
                "severity": "error",
            })
            return
        max_retries = retry_policy.get("max_retries", 0)
        if not isinstance(max_retries, int) or max_retries < 0:
            errors.append({
                "field": "retry_policy.max_retries",
                "message": "max_retries must be a non-negative integer",
                "severity": "error",
            })
        backoff = retry_policy.get("backoff_factor", 1.0)
        if not isinstance(backoff, (int, float)) or backoff < 0:
            errors.append({
                "field": "retry_policy.backoff_factor",
                "message": "backoff_factor must be a non-negative number",
                "severity": "error",
            })

    def _validate_dependencies(self, depends_on, errors):
        if not isinstance(depends_on, list):
            errors.append({
                "field": "depends_on",
                "message": "depends_on must be a list",
                "severity": "error",
            })
            return
        for dep in depends_on:
            if not isinstance(dep, str):
                errors.append({
                    "field": "depends_on",
                    "message": f"Dependency must be a string, got {type(dep).__name__}",
                    "severity": "error",
                })
            elif dep == "self":
                errors.append({
                    "field": "depends_on",
                    "message": "Directive cannot depend on itself",
                    "severity": "error",
                })

    def _validate_metadata(self, metadata, warnings):
        if not isinstance(metadata, dict):
            warnings.append({
                "field": "metadata",
                "message": "metadata should be a dictionary",
                "severity": "warning",
            })
            return
        allowed_keys = {"author", "created_at", "tags", "priority", "source", "version"}
        for key in metadata:
            if key not in allowed_keys:
                warnings.append({
                    "field": f"metadata.{key}",
                    "message": f"Non-standard metadata key '{key}'. Consider removing.",
                    "severity": "warning",
                })

    def _validate_forbidden_patterns(self, directive, errors):
        directive_str = str(directive)
        for pattern in self.schema.FORBIDDEN_PATTERNS:
            if re.search(pattern, directive_str, re.IGNORECASE):
                errors.append({
                    "field": "general",
                    "message": f"Forbidden pattern detected: {pattern}",
                    "severity": "error",
                })

    def validate_batch(self, directives):
        results = []
        for directive in directives:
            directive_id = directive.get("id", directive.get("task", "unknown"))
            result = self.validate_directive(directive, directive_id)
            results.append(result)
        return {
            "batch_size": len(directives),
            "valid_count": sum(1 for r in results if r["valid"]),
            "invalid_count": sum(1 for r in results if not r["valid"]),
            "results": results,
            "validated_at": utc_now_iso(),
        }

    def get_stats(self):
        return {
            "validation_count": self.validation_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "success_rate": (
                (self.validation_count - self.error_count) / self.validation_count
                if self.validation_count > 0
                else 1.0
            ),
            "started_at": self.started_at,
        }


class DirectiveValidatorTableManager:
    TABLE_NAME = "directive_validation_log"

    def __init__(self, validator):
        self.validator = validator
        self._ensure_table()

    def _ensure_table(self):
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
            directive_id VARCHAR,
            task VARCHAR,
            valid BOOLEAN,
            error_count INTEGER,
            warning_count INTEGER,
            errors_json VARCHAR,
            warnings_json VARCHAR,
            validated_at TIMESTAMPTZ,
            PRIMARY KEY (directive_id, validated_at)
        )
        """
        try:
            ws_execute(create_sql)
            log.info(f"Ensured table {self.TABLE_NAME} exists")
        except Exception as e:
            log.warning(f"Table creation warning (may already exist): {e}")

    def log_validation(self, result):
        row = {
            "directive_id": result["directive_id"],
            "task": result.get("task", ""),
            "valid": result["valid"],
            "error_count": len([e for e in result["errors"] if e.get("severity") == "error"]),
            "warning_count": len(result["warnings"]),
            "errors_json": str(result["errors"]),
            "warnings_json": str(result["warnings"]),
            "validated_at": result["validated_at"],
        }
        try:
            ws_write(self.TABLE_NAME, [row])
        except Exception as e:
            log.error(f"Failed to log validation result: {e}")

    def get_recent_validations(self, limit=100):
        sql = f"""
        SELECT * FROM {self.TABLE_NAME}
        ORDER BY validated_at DESC
        LIMIT {limit}
        """
        try:
            result = ws_query(sql)
            return result.get("rows", [])
        except Exception as e:
            log.error(f"Failed to query recent validations: {e}")
            return []


def cycle():
    log.info("Directive validator cycle complete")
    return True


def run():
    log.info(f"Starting {SERVICE_NAME}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    validator = EditClassDirectiveValidator()
    table_manager = DirectiveValidatorTableManager(validator)

    POLL_SECS = 60
    log.info(f"Running in monitoring mode with {POLL_SECS}s poll interval")

    try:
        while True:
            cycle()
            stats = validator.get_stats()
            send_heartbeat(
                status="running",
                meta={
                    "validations": stats["validation_count"],
                    "errors": stats["error_count"],
                    "warnings": stats["warning_count"],
                    "success_rate": f"{stats['success_rate']:.2%}",
                },
            )
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        log.info("Received keyboard interrupt")
    finally:
        remove_pid_file()
        log.info(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    run()