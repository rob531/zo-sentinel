"""smoke_seed.py -- seed a file-backed sqlite DB for the CI treewalk smoke.

Encodes the 2026-07-03 admin treewalk data preconditions: ~300 scored servers
spanning all risk tiers / several registry sources, all 7 model axes scored at
one model_version, one saved perspective (HIGH+CRITICAL) with a baseline
snapshot, and a built Ask corpus. Run BEFORE tests/ci/smoke_entry.py.

CI-only file: does not touch shipped app code. Usage:
    DATABASE_URL=sqlite:////tmp/smoke.db python tests/ci/smoke_seed.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/smoke.db")
# Repo root on sys.path so `app.*` and the root-level feature modules import.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  (register mappers)
from app.models import McpLlmAxisScore, McpServerRegistry  # noqa: E402

TIERS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
SOURCES = ("github", "npm", "pypi", "glama", "smithery")
VERDICTS = ("OK", "REVIEW", "FLAGGED")
AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")
AXIS_LABELS = {
    "auth_strength": ("NONE", "WEAK", "MODERATE", "STRONG"),
    "capability_breadth": ("NARROW", "MODERATE", "BROAD", "SWEEPING"),
    "data_sensitivity": ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "REGULATED"),
    "network_egress": ("NONE", "LIMITED", "BROAD", "UNRESTRICTED"),
    "maintainer_trust": ("UNKNOWN", "COMMUNITY", "COMMUNITY", "UNVERIFIED"),
    "exploit_surface": ("MINIMAL", "MODERATE", "LARGE", "SEVERE"),
}
MODEL_VERSION = "smoke-v1"
N_SERVERS = 300
PERSPECTIVE_NAME = "High & Critical risk (CI)"


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        if db.query(McpServerRegistry).count():
            print("registry already seeded; skipping (delete the sqlite file to reseed)")
            return

        servers, scores = [], []
        next_id = [1]  # sqlite: BIGINT PK is not a rowid alias -> assign ids explicitly

        def score_id() -> int:
            next_id[0] += 1
            return next_id[0] - 1

        for i in range(N_SERVERS):
            tier = TIERS[i % 4]
            src = SOURCES[i % len(SOURCES)]
            sid = f"smoke-{src}-{i:04d}"
            servers.append(McpServerRegistry(
                server_id=sid,
                name=f"smoke-{tier.lower()}-mcp-{i:04d}",
                url=f"https://github.com/smoke-org/{sid}",
                registry_source=src,
                description=(f"Synthetic smoke server {i} from {src}: {tier} risk, "
                             f"weak auth touching sensitive data" if tier in ("HIGH", "CRITICAL")
                             else f"Synthetic smoke server {i} from {src}: {tier} risk, benign tool"),
                trust_score=float(i % 101),
                verdict=VERDICTS[i % 3],
                risk_tier=tier,
                last_assessed=now - timedelta(days=1),
            ))
            for ax in AXES:
                label = tier if ax == "overall_risk" else AXIS_LABELS[ax][i % 4]
                scores.append(McpLlmAxisScore(
                    id=score_id(),
                    server_id=sid, axis_name=ax, label=label,
                    label_index=i % 4, p_top=0.55 + (i % 40) / 100.0,
                    model_version=MODEL_VERSION,
                    scored_at=now - timedelta(hours=2),
                ))

        # Two rows the /scan example config resolves deterministically.
        for sid, name, url in (
            ("smoke-scan-inspector", "mcp-inspector",
             "https://github.com/anthropics/mcp-inspector"),
            ("smoke-scan-filesystem", "filesystem",
             "https://github.com/modelcontextprotocol/servers"),
        ):
            servers.append(McpServerRegistry(
                server_id=sid, name=name, url=url, registry_source="github",
                description=f"Smoke scan target {name}", trust_score=80.0,
                verdict="OK", risk_tier="LOW", last_assessed=now))
            for ax in AXES:
                scores.append(McpLlmAxisScore(
                    id=score_id(),
                    server_id=sid, axis_name=ax,
                    label="LOW" if ax == "overall_risk" else AXIS_LABELS[ax][0],
                    label_index=0, p_top=0.9, model_version=MODEL_VERSION,
                    scored_at=now - timedelta(hours=2)))

        db.add_all(servers)
        db.add_all(scores)
        db.commit()

        # Saved perspective + baseline snapshot (trust-diff reference set).
        from perspective_model import create_perspective
        from perspective_diff_service import snapshot_perspective
        p = create_perspective(db, name=PERSPECTIVE_NAME,
                               facet_filters={"risk_tier": ["HIGH", "CRITICAL"]},
                               created_by="ci_admin",
                               description="CI treewalk smoke perspective")
        snap = snapshot_perspective(db, p.id)

        # Raw-SQL side tables the API layer expects (Alembic owns these in prod):
        # api_usage (lookup metering; /api/me queries it even for admins) and
        # app_stats (precomputed dashboard summary cache -- seed it so the
        # dashboard serves the full distribution instead of the warming stub).
        import json
        from sqlalchemy import text
        db.execute(text("CREATE TABLE IF NOT EXISTS api_usage ("
                        "user_id TEXT NOT NULL, day DATE NOT NULL, "
                        "lookups INTEGER NOT NULL DEFAULT 0, "
                        "PRIMARY KEY (user_id, day))"))
        db.execute(text("CREATE TABLE IF NOT EXISTS app_stats ("
                        "key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"))
        from dashboard_summary_api import compute_summary
        db.execute(text("INSERT INTO app_stats(key, value, updated_at) "
                        "VALUES ('dashboard_summary', :v, :t)"),
                   {"v": json.dumps(compute_summary(db)), "t": now.isoformat()})
        db.commit()

        # Ask corpus so /api/ask can return grounded results.
        from ask_corpus_indexer import reindex
        idx = reindex(db)

        n_srv = db.query(McpServerRegistry).count()
        n_sc = db.query(McpLlmAxisScore).count()
        print(f"seeded: servers={n_srv} axis_scores={n_sc} "
              f"perspective={p.id} snapshot_members={len(snap.membership or {})} "
              f"ask_index={idx}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
