import json
import datetime
from typing import List, Tuple

import requests
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry, McpLlmAxisScore


def compute_domain_trust_score(server_id: str, url: str, name: str) -> float:
    """
    Very simple heuristic to produce a trust score between 0 and 100.
    """
    score = 0.0

    # URL based hints
    if url.startswith("https://"):
        score += 30.0
    if "gov" in url.lower():
        score += 20.0
    if "internal" in url.lower():
        score += 10.0

    # Name based hints
    if len(name) > 10:
        score += 20.0
    if any(ch.isdigit() for ch in name):
        score -= 10.0
    if name.isupper():
        score -= 5.0

    # Clamp to 0‑100
    return max(0.0, min(100.0, score))


def _label_and_probs(p_top: float) -> Tuple[int, List[float]]:
    """
    Map a trust score (0‑100) to a label index (0‑4) and a probability vector.
    Higher scores correspond to lower‑risk labels (index 4 = low risk).
    """
    if p_top < 20:
        idx = 0  # critical
    elif p_top < 40:
        idx = 1  # danger
    elif p_top < 60:
        idx = 2  # high
    elif p_top < 80:
        idx = 3  # moderate
    else:
        idx = 4  # low

    probs = [0.1] * 5
    probs[idx] = 0.6
    return idx, probs


def run() -> List[McpLlmAxisScore]:
    """
    Iterate over all servers in the registry, compute a domain‑trust score
    for those that have not yet been scored, and write the result to the
    ``mcp_llm_axis_scores`` bus table via the write‑service.
    Returns the list of created ``McpLlmAxisScore`` objects.
    """
    session: Session = get_session()
    created: List[McpLlmAxisScore] = []

    try:
        servers = session.query(McpServerRegistry).all()
        for srv in servers:
            # Skip if already scored for this axis
            exists = (
                session.query(McpLlmAxisScore)
                .filter_by(server_id=srv.server_id, axis_name="domain_trust")
                .first()
            )
            if exists:
                continue

            p_top = compute_domain_trust_score(
                srv.server_id, srv.url or "", srv.name or ""
            )
            label_index, probs = _label_and_probs(p_top)

            score_row = McpLlmAxisScore(
                server_id=srv.server_id,
                axis_name="domain_trust",
                label_index=label_index,
                probs=json.dumps(probs),
                p_top=p_top,
                p_critical=probs[0] * 100,
                p_danger=probs[1] * 100,
                # optional fields left as None / defaults
                scored_at=datetime.datetime.utcnow(),
            )

            # Write via the external write‑service
            payload = {
                "table": "mcp_llm_axis_scores",
                "records": [
                    {
                        "server_id": score_row.server_id,
                        "axis_name": score_row.axis_name,
                        "label_index": score_row.label_index,
                        "probs": score_row.probs,
                        "p_top": score_row.p_top,
                        "p_critical": score_row.p_critical,
                        "p_danger": score_row.p_danger,
                        "scored_at": score_row.scored_at.isoformat(),
                    }
                ],
            }

            resp = requests.post("http://127.0.0.1:8772/write", json=payload)
            resp.raise_for_status()

            created.append(score_row)

        return created
    finally:
        session.close()


if __name__ == "__main__":
    # ----------------------------------------------------------------------
    # Self‑test: create an in‑memory SQLite DB, seed 5 servers, monkey‑patch
    # the external services, run the scorer and validate the results.
    # ----------------------------------------------------------------------
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory DB setup
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Override the imported get_session to return our test session
    def _test_get_session() -> Session:  # pragma: no cover
        return test_session

    get_session = _test_get_session  # type: ignore

    # Dummy write‑service that pretends to succeed
    class _DummyResponse:
        status_code = 200

        def raise_for_status(self):
            pass

    def _dummy_post(url, json):
        # In a real test we could capture the payload; here we just succeed.
        return _DummyResponse()

    requests.post = _dummy_post  # type: ignore

    # Seed test servers with varying quality
    test_servers = [
        McpServerRegistry(
            server_id="srv1",
            url="https://example.com",
            name="AlphaServer",
            confidence=1,
            description="good server",
            first_seen=datetime.datetime.utcnow(),
            last_seen=datetime.datetime.utcnow(),
            registry_source="test",
            risk_tier="low",
            scan_count=1,
            trust_score=0,
            verdict="ok",
            verdict_reasoning="",
        ),
        McpServerRegistry(
            server_id="srv2",
            url="http://insecure.org",
            name="BETA123",
            confidence=1,
            description="insecure",
            first_seen=datetime.datetime.utcnow(),
            last_seen=datetime.datetime.utcnow(),
            registry_source="test",
            risk_tier="high",
            scan_count=1,
            trust_score=0,
            verdict="ok",
            verdict_reasoning="",
        ),
        McpServerRegistry(
            server_id="srv3",
            url="https://gov.example.gov",
            name="GovServer",
            confidence=1,
            description="government",
            first_seen=datetime.datetime.utcnow(),
            last_seen=datetime.datetime.utcnow(),
            registry_source="test",
            risk_tier="medium",
            scan_count=1,
            trust_score=0,
            verdict="ok",
            verdict_reasoning="",
        ),
        McpServerRegistry(
            server_id="srv4",
            url="https://internal.local",
            name="INTERNAL",
            confidence=1,
            description="internal only",
            first_seen=datetime.datetime.utcnow(),
            last_seen=datetime.datetime.utcnow(),
            registry_source="test",
            risk_tier="low",
            scan_count=1,
            trust_score=0,
            verdict="ok",
            verdict_reasoning="",
        ),
        McpServerRegistry(
            server_id="srv5",
            url="https://short.io",
            name="S",
            confidence=1,
            description="short name",
            first_seen=datetime.datetime.utcnow(),
            last_seen=datetime.datetime.utcnow(),
            registry_source="test",
            risk_tier="low",
            scan_count=1,
            trust_score=0,
            verdict="ok",
            verdict_reasoning="",
        ),
    ]

    test_session.add_all(test_servers)
    test_session.commit()

    # Run the scorer
    results = run()

    # Assertions
    assert len(results) == 5, "expected 5 scoring results"
    for r in results:
        assert 0.0 <= r.p_top <= 100.0, f"p_top out of range: {r.p_top}"

    print("PASS")