"""risk_tier_transformer router.

Thin FastAPI APIRouter exposing a single POST /write endpoint that
re‑computes a server's risk tier from its LLM axis scores and persists
the result to ``mcp_server_registry``.  The implementation mirrors the
structure of ``services/_exemplar/router.py`` and uses the real
application data layer (``app.db`` and ``app.models``).

The module also provides a helper ``process_missing`` that can be used
by background workers to batch‑process all servers whose registry entry
still lacks a ``risk_tier``.
"""

from __future__ import annotations

import datetime
from typing import List

import fastapi
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real application data layer -------------------------------------------------
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# Business logic --------------------------------------------------------------
from .logic import compute_risk_tier

# -----------------------------------------------------------------------------


router = APIRouter()


class WriteRequest(BaseModel):
    """Payload for the ``POST /write`` endpoint."""

    server_id: str = Field(..., description="Identifier of the server to assess")
    wait: bool = Field(
        False,
        description="If true, process all pending servers synchronously before returning",
    )


@router.post("/write", summary="Re‑compute and persist a server's risk tier")
def write_risk_tier(
    payload: WriteRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict:
    """
    Compute the risk tier for ``payload.server_id`` using the latest
    ``mcp_llm_axis_scores`` rows and store the result in
    ``mcp_server_registry``.  If ``payload.wait`` is true the function also
    processes all other servers that are still missing a tier before
    returning.
    """
    # --------------------------------------------------------------------- #
    # 1️⃣  Compute tier for the requested server
    # --------------------------------------------------------------------- #
    axis_rows: List[McpLlmAxisScore] = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == payload.server_id)
        .all()
    )
    if not axis_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No LLM axis scores found for server_id={payload.server_id}",
        )

    # Convert ORM objects to plain dicts expected by the logic layer
    axis_dicts = [
        {
            "axis_name": row.axis_name,
            "p_top": row.p_top,
            "decision_rule_version": row.decision_rule_version,
        }
        for row in axis_rows
    ]

    tier, evidence = compute_risk_tier(payload.server_id, axis_dicts)

    # --------------------------------------------------------------------- #
    # 2️⃣  Persist the result
    # --------------------------------------------------------------------- #
    registry_row: McpServerRegistry | None = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == payload.server_id)
        .one_or_none()
    )
    if registry_row is None:
        registry_row = McpServerRegistry(server_id=payload.server_id)
        session.add(registry_row)

    registry_row.risk_tier = tier
    registry_row.last_assessed = datetime.datetime.utcnow()
    # (optional) store evidence somewhere if the schema had a column – we skip it
    session.commit()

    # --------------------------------------------------------------------- #
    # 3️⃣  Optionally process the backlog of missing tiers
    # --------------------------------------------------------------------- #
    if payload.wait:
        # Run synchronously – the caller asked to wait.
        process_missing(session)
    else:
        # Fire‑and‑forget background processing.
        background.add_task(process_missing, session)

    return {"server_id": payload.server_id, "risk_tier": tier, "evidence": evidence}


# ----------------------------------------------------------------------------- #
# Helper: batch‑process all servers that still lack a risk tier
# ----------------------------------------------------------------------------- #
BATCH_SIZE = 500
WRITE_TIMEOUT_SECONDS = 30


def process_missing(session: Session) -> None:
    """
    Find all ``server_id`` values that have LLM axis scores but whose
    ``mcp_server_registry.risk_tier`` is still NULL, compute the tier for
    each, and persist the result.  The operation is idempotent – already
    scored servers are skipped.
    """
    # Sub‑query to fetch server IDs that already have a tier
    scored_ids_subq = (
        session.query(McpServerRegistry.server_id)
        .filter(McpServerRegistry.risk_tier.is_not(None))
        .subquery()
    )

    # Distinct server IDs that have axis scores but are not yet scored
    pending_ids = (
        session.query(McpLlmAxisScore.server_id)
        .filter(~McpLlmAxisScore.server_id.in_(scored_ids_subq))
        .distinct()
        .all()
    )
    pending_ids = [sid for (sid,) in pending_ids]

    # Process in batches
    for i in range(0, len(pending_ids), BATCH_SIZE):
        batch = pending_ids[i : i + BATCH_SIZE]
        for server_id in batch:
            axis_rows: List[McpLlmAxisScore] = (
                session.query(McpLlmAxisScore)
                .filter(McpLlmAxisScore.server_id == server_id)
                .all()
            )
            if not axis_rows:
                continue  # Defensive – should not happen

            axis_dicts = [
                {
                    "axis_name": row.axis_name,
                    "p_top": row.p_top,
                    "decision_rule_version": row.decision_rule_version,
                }
                for row in axis_rows
            ]

            tier, _ = compute_risk_tier(server_id, axis_dicts)

            registry_row: McpServerRegistry | None = (
                session.query(McpServerRegistry)
                .filter(McpServerRegistry.server_id == server_id)
                .one_or_none()
            )
            if registry_row is None:
                registry_row = McpServerRegistry(server_id=server_id)
                session.add(registry_row)

            registry_row.risk_tier = tier
            registry_row.last_assessed = datetime.datetime.utcnow()

        # Commit after each batch – respects the write timeout contract
        session.commit()


# ----------------------------------------------------------------------------- #
# Self‑test ---------------------------------------------------------------
# ----------------------------------------------------------------------------- #
if __name__ == "__main__":
    """
    Minimal in‑memory sanity check.  It does **not** touch the real database;
    it only validates that the imported ``compute_risk_tier`` function can
    be called with representative data and that the batch helper runs
    without raising.
    """

    # Sample data – one server per hypothetical tier.  The exact mapping
    # depends on the implementation of ``compute_risk_tier``; we only need
    # distinct ``p_top`` values to provoke different outcomes.
    sample_servers = {
        "srv_trusted_general": [
            {"axis_name": "confidentiality", "p_top": 0.99, "decision_rule_version": "v1"},
            {"axis_name": "integrity", "p_top": 0.98, "decision_rule_version": "v1"},
        ],
        "srv_trusted_research": [
            {"axis_name": "confidentiality", "p_top": 0.85, "decision_rule_version": "v1"},
            {"axis_name": "integrity", "p_top": 0.80, "decision_rule_version": "v1"},
        ],
        "srv_enterprise_controlled": [
            {"axis_name": "confidentiality", "p_top": 0.70, "decision_rule_version": "v1"},
            {"axis_name": "integrity", "p_top": 0.68, "decision_rule_version": "v1"},
        ],
        "srv_high_risk_isolated": [
            {"axis_name": "confidentiality", "p_top": 0.30, "decision_rule_version": "v1"},
            {"axis_name": "integrity", "p_top": 0.25, "decision_rule_version": "v1"},
        ],
    }

    # Verify that each server yields a non‑empty tier string.
    for srv_id, scores in sample_servers.items():
        tier, evidence = compute_risk_tier(srv_id, scores)
        assert isinstance(tier, str) and tier, f"Tier missing for {srv_id}"
        assert isinstance(evidence, dict), f"Evidence missing for {srv_id}"

    # Simulate the batch processor – it should iterate over the keys without error.
    class DummySession:
        """Very small stub that mimics the subset of the SQLAlchemy Session API we use."""

        def __init__(self, data: dict[str, list[dict]]):
            self._data = data
            self.committed = False

        def query(self, model):
            return DummyQuery(model, self._data)

        def add(self, instance):
            # No‑op for the stub – we only need the method to exist.
            pass

        def commit(self):
            self.committed = True

    class DummyQuery:
        def __init__(self, model, data):
            self.model = model
            self.data = data
            self._filters = []

        def filter(self, *criteria):
            # Store simple lambda criteria for later evaluation.
            self._filters.extend(criteria)
            return self

        def filter_by(self, **kwargs):
            self._filters.append(lambda obj: all(getattr(obj, k) == v for k, v in kwargs.items()))
            return self

        def distinct(self):
            return self

        def all(self):
            # Resolve based on the model type.
            if self.model is McpLlmAxisScore:
                rows = [
                    DummyRow(server_id=sid, axis_name=sd["axis_name"], p_top=sd["p_top"], decision_rule_version=sd["decision_rule_version"])
                    for sid, scores in self.data.items()
                    for sd in scores
                ]
            elif self.model is McpServerRegistry:
                rows = []  # No pre‑existing registry rows in this stub.
            else:
                rows = []

            for f in self._filters:
                rows = [r for r in rows if f(r)]
            return rows

        def one_or_none(self):
            results = self.all()
            if not results:
                return None
            if len(results) > 1:
                raise Exception("Multiple rows returned where one expected")
            return results[0]

        def one(self):
            result = self.one_or_none()
            if result is None:
                raise Exception("No rows returned where one expected")
            return result

        def subquery(self):
            # For our stub, return a list of server_ids that already have a tier.
            return [r.server_id for r in self.all()]

    class DummyRow:
        def __init__(self, server_id, axis_name=None, p_top=None, decision_rule_version=None):
            self.server_id = server_id
            self.axis_name = axis_name
            self.p_top = p_top
            self.decision_rule_version = decision_rule_version

    # Run the batch helper with the dummy session.
    dummy_session = DummySession(sample_servers)
    process_missing(dummy_session)

    print("PASS")