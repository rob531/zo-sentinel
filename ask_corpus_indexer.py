"""ask_corpus_indexer.py -- the Ask-MCPLookup corpus builder (Appendix G).

For each scored server, compose a normalized snippet (name + description head
+ verdict + risk_tier + the latest-model axis labels) and a field-weighted
term index, upserted into ask_corpus_index (app.models.AskCorpusDoc). Sources
are ONLY mcp_server_registry + mcp_llm_axis_scores. Idempotent: unchanged
input -> identical rows, no duplicates (server_id is the PK; content hash
short-circuits rewrites). Bounded batches; resumable by construction (upsert).

Run:  python3 ask_corpus_indexer.py            (CLI full pass, prints stats)
API:  POST /api/ask/reindex                    (admin-only)
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpLlmAxisScore, McpServerRegistry
from facet_enum_service import latest_global_model_version
from verdict_breakdown_api import Principal, require_admin

router = APIRouter(prefix="/api", tags=["ask"])

DESC_HEAD_CHARS = 400
_TOKEN_RX = re.compile(r"[a-z0-9_]{2,40}")


def tokenize(text: Optional[str]) -> List[str]:
    return sorted(set(_TOKEN_RX.findall((text or "").lower())))


def build_doc(server: McpServerRegistry,
              axis_labels: Dict[str, str]) -> dict:
    """One corpus doc: snippet (human-readable, citation-ready) + field-scoped
    terms so retrieval can weight name > verdict/tier > axes > description."""
    desc_head = (server.description or "")[:DESC_HEAD_CHARS]
    axis_text = " ".join(f"{a}={l}" for a, l in sorted(axis_labels.items()))
    snippet = (f"{server.name or server.server_id} | verdict={server.verdict} "
               f"tier={server.risk_tier} source={server.registry_source} | "
               f"{axis_text} | {desc_head}")
    terms = {
        "name": tokenize(server.name),
        "verdict": tokenize(f"{server.verdict} {server.risk_tier} "
                            f"{server.registry_source}"),
        "axes": tokenize(" ".join(list(axis_labels.keys()) +
                                  list(axis_labels.values()))),
        "desc": tokenize(desc_head),
    }
    return {"server_id": server.server_id, "snippet": snippet, "terms": terms}


def _content_hash(doc: dict) -> str:
    return hashlib.sha256(repr(sorted(doc["terms"].items())).encode()
                          + doc["snippet"].encode()).hexdigest()[:16]


def reindex(db: Session, batch_size: int = 1000, limit: int = 0) -> dict:
    """Full corpus pass. Upsert-by-PK => idempotent; unchanged docs skipped
    via content hash stored on the row (indexed_at only bumps on change)."""
    mv = latest_global_model_version(db)
    stats = {"scanned": 0, "written": 0, "unchanged": 0, "model_version": mv}

    axis_map: Dict[str, Dict[str, str]] = {}
    if mv:
        for sid, axis, label in db.execute(
                select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
                       McpLlmAxisScore.label)
                .where(McpLlmAxisScore.model_version == mv)):
            if label:
                axis_map.setdefault(sid, {})[axis] = str(label)

    q = select(McpServerRegistry)
    if limit:
        q = q.limit(limit)
    n_in_batch = 0
    for server in db.execute(q).scalars():
        stats["scanned"] += 1
        doc = build_doc(server, axis_map.get(server.server_id, {}))
        h = _content_hash(doc)
        row = db.get(AskCorpusDoc, server.server_id)
        if row is not None and row.content_hash == h:
            stats["unchanged"] += 1
            continue
        if row is None:
            row = AskCorpusDoc(server_id=server.server_id)
            db.add(row)
        row.snippet = doc["snippet"]
        row.terms = doc["terms"]
        row.content_hash = h
        row.indexed_at = datetime.now(timezone.utc)
        stats["written"] += 1
        n_in_batch += 1
        if n_in_batch >= batch_size:
            db.commit()
            n_in_batch = 0
    db.commit()
    return stats


@router.post("/ask/reindex")
def api_reindex(db: Session = Depends(get_session),
                principal: Principal = Depends(require_admin)) -> dict:
    return reindex(db)


if __name__ == "__main__":
    import os, sys
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    if "--live" in sys.argv:
        from app.db import SessionLocal
        print(reindex(SessionLocal()))
        raise SystemExit(0)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpLlmAxisScore as A, McpServerRegistry as R
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([
        R(server_id="s1", name="Weak Auth Github Server", verdict="HIGH",
          risk_tier="HIGH", registry_source="github",
          description="A server with weak authentication."),
        A(id=1, server_id="s1", axis_name="auth_strength", label="WEAK", model_version="v3"),
    ])
    s.commit()
    st1 = reindex(s)
    assert st1["written"] == 1, st1
    doc = s.get(AskCorpusDoc, "s1")
    assert "verdict=HIGH" in doc.snippet and "auth_strength=WEAK" in doc.snippet
    st2 = reindex(s)   # idempotent: second run writes nothing
    assert st2["written"] == 0 and st2["unchanged"] == 1, st2
    print("PASS")
