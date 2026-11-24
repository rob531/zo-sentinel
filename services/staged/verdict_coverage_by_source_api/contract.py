"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.staged.verdict_coverage_by_source_api.contract` -- and a zero
exit is the proof that the service boots, mounts, serves 200, and returns a
schema-valid body over the REAL data layer (overridden to an in-memory SQLite
for hermeticity). A non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not a
silent skip.

Run:  python -m services.staged.verdict_coverage_by_source_api.contract
   ->  prints PASS / FAIL, exits 0/1
"""
from __future__ import annotations

import sys
from datetime import datetime


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng, tables=[McpServerRegistry.__table__])
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    assessed = datetime(2024, 5, 17, 9, 30, 0)

    s = TestingSession()
    s.add(McpServerRegistry(
        server_id="gh-1", name="gh-one", registry_source="github",
        verdict="SAFE", verdict_reasoning="scanned and signed",
        confidence=0.92, risk_tier="LOW", trust_score=0.9, last_assessed=assessed,
    ))
    s.add(McpServerRegistry(
        server_id="gh-2", name="gh-two", registry_source="github",
        verdict="RISKY", verdict_reasoning="exposed admin tool",
        confidence=0.41, risk_tier="HIGH", trust_score=0.35, last_assessed=assessed,
    ))
    s.add(McpServerRegistry(
        server_id="npm-1", name="npm-one", registry_source="npm",
        verdict="SAFE", verdict_reasoning="scanned",
        confidence=0.77, risk_tier="MEDIUM", trust_score=0.7, last_assessed=assessed,
    ))
    s.add(McpServerRegistry(
        server_id="npm-2", name="npm-two", registry_source="npm",
    ))
    s.commit()
    s.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    c = TestClient(app)

    resp = None
    for path in ("/api/verdict-coverage/by-source", "/verdict-coverage/by-source"):
        candidate = c.get(path)
        if candidate.status_code == 200:
            resp = candidate
            break
    assert resp is not None, "no 200 from the verdict-coverage by-source route"
    body = resp.json()

    generated_at = body.get("generated_at")
    assert isinstance(generated_at, str) and generated_at, body
    assert body.get("total_servers") == 4, body

    sources = body.get("sources")
    assert isinstance(sources, list), body
    assert len(sources) == 2, body

    counts = [row["servers"] for row in sources]
    assert counts == sorted(counts, reverse=True), body

    by_source = {row["registry_source"]: row for row in sources}
    assert set(by_source) == {"github", "npm"}, body

    gh = by_source["github"]
    assert gh["servers"] == 2, body
    assert gh["with_verdict"] == 2, body
    assert gh["with_risk_tier"] == 2, body
    assert gh["with_confidence"] == 2, body
    assert abs(float(gh["scored_ratio"]) - 1.0) < 1e-9, body
    assert gh.get("last_assessed_at"), body

    npm = by_source["npm"]
    assert npm["servers"] == 2, body
    assert npm["with_verdict"] == 1, body
    assert npm["with_risk_tier"] == 1, body
    assert npm["with_confidence"] == 1, body
    assert abs(float(npm["scored_ratio"]) - 0.5) < 1e-9, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)