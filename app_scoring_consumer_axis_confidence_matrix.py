"""app_scoring_consumer_axis_confidence_matrix.py

Pure scoring‑consumer utilities for converting raw LLM axis scores into a
per‑server confidence matrix and summarising it for dashboard consumption.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from app.db import get_session, Base  # noqa: F401  (Base used only in __main__ test)
from app.models import McpLlmAxisScore

# --------------------------------------------------------------------------- #
# Core computation helpers
# --------------------------------------------------------------------------- #


def compute_axis_confidence_matrix(
    rows: List[Dict],
) -> Dict[str, Dict[str, Dict[str, object]]]:
    """
    Convert a list of raw axis‑score rows into a nested confidence matrix.

    The returned structure is:
        {
            server_id: {
                axis_name: {
                    "confidence": float,   # derived from p_top, clipped to [0, 1]
                    "p_top": float,
                    "escalated": bool,
                },
                ...
            },
            ...
        }
    """
    matrix: Dict[str, Dict[str, Dict[str, object]]] = defaultdict(dict)

    for row in rows:
        server_id = row["server_id"]
        axis_name = row["axis_name"]
        p_top = float(row.get("p_top", 0.0) or 0.0)
        escalated = bool(row.get("escalated", False))

        # Confidence is defined as the top‑probability, bounded to [0, 1].
        confidence = max(0.0, min(1.0, p_top))

        matrix[server_id][axis_name] = {
            "confidence": confidence,
            "p_top": p_top,
            "escalated": escalated,
        }

    return dict(matrix)


def summarize_confidence(
    matrix: Dict[str, Dict[str, Dict[str, object]]],
) -> Dict[str, object]:
    """
    Produce a high‑level summary of a confidence matrix.

    Returns a dict with:
        - server_count: total number of servers represented
        - mean_confidence: average confidence across all axes of all servers
        - low_confidence_servers: list of server_ids whose mean axis confidence
          is below 0.5
    """
    server_count = len(matrix)
    total_confidence = 0.0
    total_axes = 0
    low_confidence_servers: List[str] = []

    for server_id, axes in matrix.items():
        axis_confidences = [info["confidence"] for info in axes.values()]
        if not axis_confidences:
            continue
        mean_server_conf = sum(axis_confidences) / len(axis_confidences)
        if mean_server_conf < 0.5:
            low_confidence_servers.append(server_id)

        total_confidence += sum(axis_confidences)
        total_axes += len(axis_confidences)

    mean_confidence = total_confidence / total_axes if total_axes else 0.0

    return {
        "server_count": server_count,
        "mean_confidence": mean_confidence,
        "low_confidence_servers": low_confidence_servers,
    }


def read_axis_confidence_matrix(
    session,
    server_id: str,
) -> Dict[str, Dict[str, object]]:
    """
    Helper that reads raw rows for a single server from the DB and returns the
    per‑axis confidence mapping for that server.
    """
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    dict_rows = [
        {
            "server_id": r.server_id,
            "axis_name": r.axis_name,
            "label": r.label,
            "p_top": r.p_top,
            "p_critical": r.p_critical,
            "p_danger": r.p_danger,
            "escalated": r.escalated,
        }
        for r in rows
    ]
    matrix = compute_axis_confidence_matrix(dict_rows)
    return matrix.get(server_id, {})


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # The self‑test builds an in‑memory SQLite DB, seeds three servers with
    # deterministic scores and validates the public helpers.
    from datetime import datetime
    from fastapi import FastAPI, Depends
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # In‑memory DB setup
    # ------------------------------------------------------------------- #
    TEST_ENGINE = create_engine("sqlite:///:memory:", future=True, echo=False)
    TEST_SESSION_LOCAL = sessionmaker(bind=TEST_ENGINE, autoflush=False, autocommit=False)

    # Create tables using the same Base that the real app uses.
    Base.metadata.create_all(TEST_ENGINE)

    # Dependency override for the test FastAPI instance.
    def get_test_session():
        return TEST_SESSION_LOCAL()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed data
    # ------------------------------------------------------------------- #
    with TEST_SESSION_LOCAL() as sess:
        # Server A – high confidence on two axes
        sess.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server_a",
                    axis_name="availability",
                    model_version="v1",
                    p_top=0.92,
                    p_critical=0.04,
                    p_danger=0.04,
                    escalated=False,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server_a",
                    axis_name="integrity",
                    model_version="v1",
                    p_top=0.88,
                    p_critical=0.07,
                    p_danger=0.05,
                    escalated=False,
                ),
            ]
        )
        # Server B – mixed confidence
        sess.add_all(
            [
                McpLlmAxisScore(
                    id=3,
                    server_id="server_b",
                    axis_name="availability",
                    model_version="v1",
                    p_top=0.6,
                    p_critical=0.2,
                    p_danger=0.2,
                    escalated=False,
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server_b",
                    axis_name="integrity",
                    model_version="v1",
                    p_top=0.55,
                    p_critical=0.25,
                    p_danger=0.2,
                    escalated=False,
                ),
            ]
        )
        # Server C – low confidence on all axes
        sess.add_all(
            [
                McpLlmAxisScore(
                    id=5,
                    server_id="server_c",
                    axis_name="availability",
                    model_version="v1",
                    p_top=0.1,
                    p_critical=0.45,
                    p_danger=0.45,
                    escalated=True,
                ),
                McpLlmAxisScore(
                    id=6,
                    server_id="server_c",
                    axis_name="integrity",
                    model_version="v1",
                    p_top=0.15,
                    p_critical=0.4,
                    p_danger=0.45,
                    escalated=True,
                ),
            ]
        )
        sess.commit()

        # ------------------------------------------------------------------- #
        # Run helpers
        # ------------------------------------------------------------------- #
        raw_rows = [
            {
                "server_id": r.server_id,
                "axis_name": r.axis_name,
                "label": r.label,
                "p_top": r.p_top,
                "p_critical": r.p_critical,
                "p_danger": r.p_danger,
                "escalated": r.escalated,
            }
            for r in sess.query(McpLlmAxisScore).all()
        ]

        matrix = compute_axis_confidence_matrix(raw_rows)

        # Assertions required by the acceptance criteria
        assert len(matrix) == 3, "Expected three servers in the matrix"
        for srv_axes in matrix.values():
            for axis_info in srv_axes.values():
                conf = axis_info["confidence"]
                assert 0.0 <= conf <= 1.0, "Confidence out of bounds"

        summary = summarize_confidence(matrix)
        assert summary["server_count"] == 3, "Server count mismatch"
        assert summary["low_confidence_servers"] == ["server_c"], "Low‑confidence server detection failed"

        print("PASS")