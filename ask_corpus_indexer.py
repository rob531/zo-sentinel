"""ask_corpus_indexer.py -- the Ask-MCPLookup corpus builder (Appendix G).

For each scored server, compose a normalized snippet (name + description head
+ verdict + risk_tier + the latest-model axis labels) and a field-weighted
term index, upserted into ask_corpus_index (app.models.AskCorpusDoc). Sources
are ONLY mcp_server_registry + mcp_llm_axis_scores. Idempotent: unchanged
input -> identical rows, no duplicates (server_id is the PK; content hash
short-circuits rewrites). Bounded batches; resumable by construction (upsert).

MEMORY-BOUNDED (2026-07-19): the previous implementation loaded a global
sid->axis-label map (~1.6M entries at 232K servers) plus an unstreamed
full-registry ORM scan; the 1GB Fly worker was OOM-killed at ~790MB two
minutes in (cadence runs 24/26 died as zombie 'running' rows and took the
co-resident snapshots worker with them). This version holds at most ONE
keyset-paginated chunk of servers, their axis labels, and their existing
corpus rows; every chunk ends with a commit.

Run:  python3 ask_corpus_indexer.py            (CLI full pass, prints stats)
API:  POST /api/ask/reindex                    (admin-only)
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (AskCorpusDoc, McpLlmAxisScore, McpServerRegistry,
                        VulnAdvisory, VulnLink)
from facet_enum_service import latest_global_model_version
from verdict_breakdown_api import Principal, require_admin

router = APIRouter(prefix="/api", tags=["ask"])

DESC_HEAD_CHARS = 400
_TOKEN_RX = re.compile(r"[a-z0-9_]{2,40}")


def tokenize(text: Optional[str]) -> List[str]:
    return sorted(set(_TOKEN_RX.findall((text or "").lower())))


def build_doc(server: McpServerRegistry,
              axis_labels: Dict[str, str],
              cves: Optional[List[str]] = None) -> dict:
    """One corpus doc: snippet (human-readable, citation-ready) + field-scoped
    terms so retrieval can weight name > verdict/tier > axes > description.

    FU-264: linked advisories (VulnLink -> VulnAdvisory) land in the snippet as
    a `cve=` segment -- the retrieval identifier path matches CVE/GHSA ids ONLY
    as exact snippet substrings, so until the ids are IN the snippet the
    matcher can only ever return []. The segment and the `cve` terms field are
    emitted ONLY when links exist, so unlinked docs keep their content_hash and
    a reindex touches just the linked rows."""
    desc_head = (server.description or "")[:DESC_HEAD_CHARS]
    axis_text = " ".join(f"{a}={l}" for a, l in sorted(axis_labels.items()))
    cve_text = " ".join(cves or [])
    cve_seg = f"cve={cve_text} | " if cve_text else ""
    snippet = (f"{server.name or server.server_id} | verdict={server.verdict} "
               f"tier={server.risk_tier} source={server.registry_source} | "
               f"{axis_text} | {cve_seg}{desc_head}")
    terms = {
        "name": tokenize(server.name),
        "verdict": tokenize(f"{server.verdict} {server.risk_tier} "
                            f"{server.registry_source}"),
        "axes": tokenize(" ".join(list(axis_labels.keys()) +
                                  list(axis_labels.values()))),
        "desc": tokenize(desc_head),
    }
    if cve_text:
        terms["cve"] = tokenize(cve_text)
    return {"server_id": server.server_id, "snippet": snippet, "terms": terms}


def _content_hash(doc: dict) -> str:
    return hashlib.sha256(repr(sorted(doc["terms"].items())).encode()
                          + doc["snippet"].encode()).hexdigest()[:16]


def _axis_labels_for(db: Session, sids: Sequence[str],
                     mv: Optional[str]) -> Dict[str, Dict[str, str]]:
    """Axis labels for ONE chunk of server_ids only -- never the whole fleet."""
    out: Dict[str, Dict[str, str]] = {}
    if mv and sids:
        for sid, axis, label in db.execute(
                select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
                       McpLlmAxisScore.label)
                .where(McpLlmAxisScore.model_version == mv,
                       McpLlmAxisScore.server_id.in_(list(sids)))):
            if label:
                out.setdefault(sid, {})[axis] = str(label)
    return out


def _cve_ids_for(db: Session, sids: Sequence[str]) -> Dict[str, List[str]]:
    """Linked advisory ids (+severity) for ONE chunk of server_ids only --
    never the whole fleet (mirrors _axis_labels_for; this pattern is what
    keeps the indexer inside a 1GB Fly machine). Deterministic: sorted ids."""
    out: Dict[str, List[str]] = {}
    if sids:
        for sid, aid, sev in db.execute(
                select(VulnLink.server_id, VulnLink.advisory_id,
                       VulnAdvisory.severity)
                .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
                .where(VulnLink.server_id.in_(list(sids)))):
            out.setdefault(sid, []).append(f"{aid}:{sev or 'UNKNOWN'}")
    return {k: sorted(v) for k, v in out.items()}


def reindex(db: Session, batch_size: int = 1000, limit: int = 0,
            chunk_size: int = 0) -> dict:
    """Full corpus pass in bounded chunks. Upsert-by-PK => idempotent;
    unchanged docs skipped via content hash stored on the row (indexed_at
    only bumps on change). batch_size is retained for call compatibility;
    the working-set bound is chunk_size (env ASK_REINDEX_CHUNK, default
    2000 rows ~= a few MB, safe on a 1GB Fly machine)."""
    if chunk_size <= 0:
        try:
            chunk_size = max(1, int(os.environ.get("ASK_REINDEX_CHUNK", "2000")))
        except (TypeError, ValueError):
            chunk_size = 2000
    mv = latest_global_model_version(db)
    stats = {"scanned": 0, "written": 0, "unchanged": 0, "model_version": mv,
             "chunk_size": chunk_size}
    last_sid = ""
    while True:
        if limit and stats["scanned"] >= limit:
            break
        take = chunk_size if not limit else min(chunk_size,
                                                limit - stats["scanned"])
        servers = db.execute(
            select(McpServerRegistry)
            .where(McpServerRegistry.server_id > last_sid)
            .order_by(McpServerRegistry.server_id)
            .limit(take)).scalars().all()
        if not servers:
            break
        sids = [s.server_id for s in servers]
        last_sid = sids[-1]
        axis_map = _axis_labels_for(db, sids, mv)
        cve_map = _cve_ids_for(db, sids)
        existing = {r.server_id: r for r in db.execute(
            select(AskCorpusDoc)
            .where(AskCorpusDoc.server_id.in_(sids))).scalars()}
        for server in servers:
            stats["scanned"] += 1
            doc = build_doc(server, axis_map.get(server.server_id, {}),
                            cve_map.get(server.server_id))
            h = _content_hash(doc)
            row = existing.get(server.server_id)
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
        # Commit per chunk: bounds the transaction and expires chunk
        # objects (expire_on_commit); the weak identity map lets them be
        # GC'd when the next chunk rebinds servers/existing/axis_map.
        # NEVER expunge_all here: callers (treewalk seed, cadence worker)
        # hold live objects on this same session (CI caught the detach).
        db.commit()
    return stats


@router.post("/ask/reindex")
def api_reindex(db: Session = Depends(get_session),
                principal: Principal = Depends(require_admin)) -> dict:
    return reindex(db)


if __name__ == "__main__":
    import sys
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
        R(server_id="s2", name="Beta MCP", verdict="LOW",
          risk_tier="LOW", registry_source="npm",
          description="A quiet low-risk server."),
        R(server_id="s3", name="Gamma MCP", verdict="MEDIUM",
          risk_tier="MEDIUM", registry_source="pypi",
          description="A middling server."),
        A(id=1, server_id="s1", axis_name="auth_strength", label="WEAK",
          model_version="v3"),
        VulnAdvisory(id="CVE-2025-49596", feed="nvd", severity="CRITICAL",
                     source_url="https://nvd.nist.gov/vuln/detail/CVE-2025-49596"),
        VulnLink(advisory_id="CVE-2025-49596", server_id="s1",
                 match_basis="package_exact", match_value="weak-auth-github",
                 match_confidence=1.0),
    ])
    s.commit()
    # chunk_size=2 across 3 rows exercises the keyset chunk boundary
    st1 = reindex(s, chunk_size=2)
    assert st1["written"] == 3 and st1["scanned"] == 3, st1
    doc = s.get(AskCorpusDoc, "s1")
    assert "verdict=HIGH" in doc.snippet and "auth_strength=WEAK" in doc.snippet
    # FU-264: the linked advisory must be IN the snippet (the retrieval
    # identifier path matches ids only as exact snippet substrings) ...
    assert "cve=CVE-2025-49596:CRITICAL" in doc.snippet, doc.snippet
    assert "critical" in doc.terms.get("cve", []), doc.terms
    # ... and an UNLINKED doc must not change shape (hash stability: a fleet
    # reindex may only touch linked rows).
    doc2 = s.get(AskCorpusDoc, "s2")
    assert "cve=" not in doc2.snippet and "cve" not in doc2.terms, doc2.snippet
    st2 = reindex(s, chunk_size=2)   # idempotent: second run writes nothing
    assert st2["written"] == 0 and st2["unchanged"] == 3, st2
    st3 = reindex(s, chunk_size=1000)  # chunk-size independence
    assert st3["written"] == 0 and st3["unchanged"] == 3, st3
    st4 = reindex(s, limit=2, chunk_size=2)  # limit still honored
    assert st4["scanned"] == 2, st4
    # FU-264 closing predicate, end-to-end: an identifier query now returns
    # the linked server; negative control: an UNSEEN id still returns [].
    from ask_retrieval_service import retrieve
    hits = retrieve(s, "CVE-2025-49596")
    assert hits and hits[0]["server_id"] == "s1", hits
    assert "identifier" in hits[0]["provenance"]["matched_fields"], hits
    assert retrieve(s, "CVE-2099-00001") == []
    print("PASS")
