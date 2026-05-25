import logging
import sys
import json
import hashlib
from datetime import datetime, timezone
from typing import Any

import requests

SERVICE_NAME = "audit_enrichment_blobs"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")],
)
log = logging.getLogger(__name__)

REQUIRED_SIGNAL_TYPES = {
    "domain_trust",
    "tool_description_safety",
    "permission_scope",
    "supply_chain",
    "community_signal",
    "temporal_stability",
    "supply_chain_enrichment",
    "community_signal_enrichment",
}

REQUIRED_EVIDENCE_BLOB_KEYS = {"signal_type", "confidence", "evidence_blob"}


def ws_query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    payload = {"sql": sql, "params": list(params)}
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_execute(sql: str, params: tuple = ()) -> None:
    payload = {"sql": sql, "params": list(params)}
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_row_id(row: dict[str, Any]) -> str:
    content = f"{row.get('server_id', '')}|{row.get('signal_type', '')}|{row.get('scored_at', '')}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def validate_evidence_blob(blob: Any, row_id: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(blob, dict):
        errors.append(f"row {row_id}: evidence_blob is not a dict, got {type(blob).__name__}")
        return errors

    for key in REQUIRED_EVIDENCE_BLOB_KEYS:
        if key not in blob:
            errors.append(f"row {row_id}: evidence_blob missing key '{key}'")

    if "signal_type" in blob and not isinstance(blob["signal_type"], str):
        errors.append(f"row {row_id}: signal_type in evidence_blob is not str, got {type(blob['signal_type']).__name__}")

    if "confidence" in blob:
        val = blob["confidence"]
        if not isinstance(val, (int, float)):
            errors.append(f"row {row_id}: confidence in evidence_blob is not numeric, got {type(val).__name__}")
        elif not (0.0 <= val <= 1.0):
            errors.append(f"row {row_id}: confidence out of range [0,1]: {val}")

    if "evidence_blob" in blob and not isinstance(blob["evidence_blob"], dict):
        errors.append(
            f"row {row_id}: nested evidence_blob is not dict, got {type(blob['evidence_blob']).__name__}"
        )

    return errors


def validate_signal_type(signal_type: str, row_id: str) -> list[str]:
    errors: list[str] = []
    if signal_type not in REQUIRED_SIGNAL_TYPES:
        errors.append(
            f"row {row_id}: unknown signal_type '{signal_type}' — not in spec contract {sorted(REQUIRED_SIGNAL_TYPES)}"
        )
    return errors


def check_table_exists() -> bool:
    rows = ws_query("SELECT 1 FROM information_schema.tables WHERE table_name = 'mcp_signal_enrichments' LIMIT 1")
    return len(rows) > 0


def audit_enrichment_blobs() -> dict[str, Any]:
    report: dict[str, Any] = {
        "audited_at": utc_now_iso(),
        "table": "mcp_signal_enrichments",
        "total_rows": 0,
        "valid_rows": 0,
        "malformed_rows": 0,
        "signal_types_found": [],
        "signal_types_missing_from_spec": [],
        "unknown_signal_types": [],
        "schema_violations": [],
        "sample_malformed": [],
        "sample_valid": [],
    }

    if not check_table_exists():
        log.warning("mcp_signal_enrichments table does not exist — nothing to audit")
        report["malformed_rows"] = 0
        report["valid_rows"] = 0
        report["schema_violations"].append("Table mcp_signal_enrichments does not exist")
        return report

    all_rows: list[dict[str, Any]] = []
    offset = 0
    batch = 1000
    while True:
        rows = ws_query(
            f"SELECT server_id, signal_type, score, scored_at, evidence_blob FROM mcp_signal_enrichments ORDER BY server_id LIMIT {batch} OFFSET {offset}"
        )
        if not rows:
            break
        all_rows.extend(rows)
        offset += batch
        if len(rows) < batch:
            break

    report["total_rows"] = len(all_rows)

    all_signal_types: set[str] = set()
    unknown_signal_types: dict[str, int] = {}
    schema_violations: list[str] = []
    malformed_detail: list[dict[str, Any]] = []
    valid_sample: list[dict[str, Any]] = []

    for row in all_rows:
        row_id = compute_row_id(row)
        signal_type = row.get("signal_type", "")
        all_signal_types.add(signal_type)

        all_errors: list[str] = []

        blob = row.get("evidence_blob")
        if blob is None:
            all_errors.append(f"row {row_id}: evidence_blob is NULL")
        else:
            try:
                if isinstance(blob, str):
                    blob = json.loads(blob)
                all_errors.extend(validate_evidence_blob(blob, row_id))
            except json.JSONDecodeError as e:
                all_errors.append(f"row {row_id}: evidence_blob JSON decode error: {e}")

        all_errors.extend(validate_signal_type(signal_type, row_id))

        if all_errors:
            report["malformed_rows"] += 1
            schema_violations.extend(all_errors)
            malformed_detail.append(
                {
                    "row_id": row_id,
                    "server_id": row.get("server_id"),
                    "signal_type": signal_type,
                    "errors": all_errors,
                }
            )
        else:
            report["valid_rows"] += 1
            if len(valid_sample) < 5:
                valid_sample.append(
                    {
                        "row_id": row_id,
                        "server_id": row.get("server_id"),
                        "signal_type": signal_type,
                        "score": row.get("score"),
                        "evidence_blob": blob,
                    }
                )

    if signal_type not in REQUIRED_SIGNAL_TYPES:
        unknown_signal_types[signal_type] = unknown_signal_types.get(signal_type, 0) + 1

    report["signal_types_found"] = sorted(all_signal_types)
    report["unknown_signal_types"] = [
        {"signal_type": k, "count": v} for k, v in sorted(unknown_signal_types.items())
    ]
    report["signal_types_missing_from_spec"] = sorted(
        REQUIRED_SIGNAL_TYPES - all_signal_types
    )
    report["schema_violations"] = sorted(set(schema_violations))
    report["sample_malformed"] = malformed_detail[:20]
    report["sample_valid"] = valid_sample

    return report


def write_audit_report(report: dict[str, Any]) -> None:
    payload = {
        "sql": (
            "INSERT INTO audit_log (target_server_id, event_type, actor, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)"
        ),
        "params": [
            "audit_enrichment_blobs",
            "evidence_blob_audit",
            "system",
            json.dumps(report),
            utc_now_iso(),
        ],
    }
    try:
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        log.info("Audit report written to audit_log")
    except Exception as e:
        log.warning("Could not write audit report to audit_log: %s", e)


def ensure_audit_table() -> None:
    ws_execute(
        "CREATE TABLE IF NOT EXISTS audit_log ("
        "  id INTEGER PRIMARY KEY,"
        "  target_server_id TEXT,"
        "  event_type TEXT,"
        "  actor TEXT,"
        "  detail TEXT,"
        "  created_at TIMESTAMPTZ"
        ")"
    )


def run() -> None:
    log.info("Starting enrichment evidence blob audit")
    ensure_audit_table()
    report = audit_enrichment_blobs()

    print(json.dumps(report, indent=2, default=str))

    summary = (
        f"total={report['total_rows']} "
        f"valid={report['valid_rows']} "
        f"malformed={report['malformed_rows']} "
        f"unknown_signals={len(report['unknown_signal_types'])}"
    )
    log.info(summary)

    write_audit_report(report)

    log.info("Audit complete")


if __name__ == "__main__":
    run()
    sys.exit(0)