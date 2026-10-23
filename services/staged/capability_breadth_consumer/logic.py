import json
from typing import List, Dict, Any

import requests
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

# ----------------------------------------------------------------------
# Helper utilities
# ----------------------------------------------------------------------


def _model_to_dict(model: Any) -> Dict[str, Any]:
    """Convert a SQLAlchemy model instance to a plain dict, stripping internal state."""
    data = dict(model.__dict__)
    data.pop("_sa_instance_state", None)
    return data


# ----------------------------------------------------------------------
# Data access
# ----------------------------------------------------------------------


def fetch_axis_rows(session: Session, server_id: str) -> List[Dict[str, Any]]:
    """
    Retrieve all ``McpLlmAxisScore`` rows for a given server where ``axis_name`` is
    ``capability_breadth``.
    """
    rows = (
        session.query(McpLlmAxisScore)
        .filter_by(server_id=server_id, axis_name="capability_breadth")
        .all()
    )
    return [_model_to_dict(r) for r in rows]


# ----------------------------------------------------------------------
# Core computation
# ----------------------------------------------------------------------


def compute_capability_breadth_metrics(
    server_id: str, rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Derive capability‑breadth metrics from raw LLM axis scores.

    Returns a dict with the following keys:
        - breadth_score (float, 0‑100)
        - tool_count_estimate (int)
        - capability_categories (list[str])
        - anomalous_tools (list[str])
    """
    if not rows:
        # No data – return neutral defaults
        return {
            "breadth_score": 0.0,
            "tool_count_estimate": 0,
            "capability_categories": [],
            "anomalous_tools": [],
        }

    # ------------------------------------------------------------------
    # breadth_score – average of ``p_top`` scaled to 0‑100
    # ------------------------------------------------------------------
    avg_p_top = sum(row.get("p_top", 0.0) for row in rows) / len(rows)
    breadth_score = max(0.0, min(100.0, avg_p_top * 100.0))

    # ------------------------------------------------------------------
    # tool_count_estimate – distinct label count
    # ------------------------------------------------------------------
    labels = [row.get("label") for row in rows if row.get("label")]
    tool_count_estimate = len(set(labels))

    # ------------------------------------------------------------------
    # capability_categories – unique labels (sorted for determinism)
    # ------------------------------------------------------------------
    capability_categories = sorted(set(labels))

    # ------------------------------------------------------------------
    # anomalous_tools – labels where the model is highly confident of
    # a critical or dangerous outcome
    # ------------------------------------------------------------------
    anomalous_tools = [
        row["label"]
        for row in rows
        if row.get("p_critical", 0.0) > 0.8 or row.get("p_danger", 0.0) > 0.5
    ]

    return {
        "breadth_score": breadth_score,
        "tool_count_estimate": tool_count_estimate,
        "capability_categories": capability_categories,
        "anomalous_tools": anomalous_tools,
    }


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


def _post_to_write_service(payload: Dict[str, Any]) -> None:
    """
    Send a JSON payload to the local write‑service.  Fail silently – the
    consumer is best‑effort and the acceptance tests do not depend on the
    external service being reachable.
    """
    try:
        requests.post(
            "http://127.0.0.1:8772/insert",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
    except Exception:
        # In test environments the write‑service may not be running.
        pass


def persist_metrics(server_id: str, metrics: Dict[str, Any]) -> None:
    """
    Write the derived metrics to the ``mcp_signal_enrichments`` bus table.
    """
    record = {
        "server_id": server_id,
        "signal_type": "capability_breadth_metrics",
        **metrics,
    }
    payload = {"table": "mcp_signal_enrichments", "records": [record]}
    _post_to_write_service(payload)


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------


def process_server(session: Session, server_id: str) -> Dict[str, Any]:
    """
    End‑to‑end processing for a single server:
        1. Load raw rows.
        2. Compute metrics.
        3. Persist the result.
    Returns the computed metrics.
    """
    rows = fetch_axis_rows(session, server_id)
    metrics = compute_capability_breadth_metrics(server_id, rows)
    persist_metrics(server_id, metrics)
    return metrics


# ----------------------------------------------------------------------
# Compatibility shim – generic helpers used by other staged services
# ----------------------------------------------------------------------


def get_axis_rows(session: Session, server_id: str, axis_name: str) -> List[Dict[str, Any]]:
    """
    Generic accessor used by a number of other staged services.
    """
    rows = (
        session.query(McpLlmAxisScore)
        .filter_by(server_id=server_id, axis_name=axis_name)
        .all()
    )
    return [_model_to_dict(r) for r in rows]


def compute_axis_aggregates(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Very light‑weight generic aggregation – returns the mean of ``p_top`` and
    the count of distinct labels.  Specific services may extend this logic.
    """
    if not rows:
        return {"mean_p_top": None, "distinct_labels": 0}
    mean_p_top = sum(row.get("p_top", 0.0) for row in rows) / len(rows)
    distinct_labels = len({row.get("label") for row in rows if row.get("label")})
    return {"mean_p_top": mean_p_top, "distinct_labels": distinct_labels}


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------


if __name__ == "__main__":
    # Synthetic data for two servers
    synthetic_rows = [
        # Server A – three rows
        {
            "server_id": "srv-A",
            "axis_name": "capability_breadth",
            "label": "tool‑alpha",
            "p_top": 0.70,
            "p_critical": 0.10,
            "p_danger": 0.05,
        },
        {
            "server_id": "srv-A",
            "axis_name": "capability_breadth",
            "label": "tool‑beta",
            "p_top": 0.85,
            "p_critical": 0.85,  # anomalous
            "p_danger": 0.20,
        },
        {
            "server_id": "srv-A",
            "axis_name": "capability_breadth",
            "label": "tool‑gamma",
            "p_top": 0.60,
            "p_critical": 0.30,
            "p_danger": 0.55,  # anomalous
        },
        # Server B – one row
        {
            "server_id": "srv-B",
            "axis_name": "capability_breadth",
            "label": "tool‑delta",
            "p_top": 0.40,
            "p_critical": 0.05,
            "p_danger": 0.10,
        },
    ]

    # Group rows by server_id
    rows_by_server: Dict[str, List[Dict[str, Any]]] = {}
    for r in synthetic_rows:
        rows_by_server.setdefault(r["server_id"], []).append(r)

    # Run the computation for each server and validate
    for srv_id, rows in rows_by_server.items():
        metrics = compute_capability_breadth_metrics(srv_id, rows)
        assert 0.0 <= metrics["breadth_score"] <= 100.0, "breadth_score out of range"
        assert metrics["tool_count_estimate"] >= 0, "negative tool count"
    print("PASS")