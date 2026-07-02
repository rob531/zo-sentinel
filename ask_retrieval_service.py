"""ask_retrieval_service.py -- deterministic lexical retrieval (Appendix G).

Scores ask_corpus_index docs against a free-text query by weighted term
overlap: name matches > verdict/tier/source > axis labels > description.
Stdlib scoring, NO embeddings, NO network, NO LLM -- v1 retrieval is exact,
explainable, and free. Every hit carries PROVENANCE (which fields matched,
which terms), because Ask answers may only cite retrieved rows.

Synonym layer: a small, fixed, auditable map from common query words to axis
vocabulary (e.g. "weak auth" -> auth_strength=WEAK) -- deterministic, not
learned.
"""
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AskCorpusDoc
from ask_corpus_indexer import tokenize

FIELD_WEIGHTS = {"name": 4.0, "verdict": 3.0, "axes": 2.0, "desc": 1.0}
DEFAULT_K = 10
CANDIDATE_LIMIT = 5000
MIN_SCORE = 2.0   # below this the caller must answer INSUFFICIENT, not guess
# Fully-assessed servers (a real published tier in the snippet) outrank
# unassessed rows at equal term relevance -- treewalk finding 2026-07-02: the
# first prod query surfaced mostly tier=unassessed rows.
ASSESSED_BOOST = 1.5
_ASSESSED_MARKERS = ("tier=CRITICAL", "tier=HIGH", "tier=MEDIUM",
                     "tier=LOW", "tier=MINIMAL")


def _is_assessed(snippet: str) -> bool:
    return any(m in (snippet or "") for m in _ASSESSED_MARKERS)

# Fixed query-side expansions into the corpus vocabulary (auditable).
SYNONYMS = {
    "auth": ["auth_strength"], "authentication": ["auth_strength"],
    "weak": ["weak"], "insecure": ["weak", "high"],
    "risky": ["high", "critical"], "dangerous": ["high", "critical"],
    "safe": ["low", "strong"], "trusted": ["established", "low"],
    "egress": ["network_egress"], "exfiltration": ["network_egress"],
    "sensitive": ["data_sensitivity"], "exploit": ["exploit_surface"],
    "maintainer": ["maintainer_trust"],
}


def expand_query(query: str) -> List[str]:
    terms = tokenize(query)
    out = list(terms)
    for t in terms:
        out.extend(SYNONYMS.get(t, []))
    return sorted(set(out))


def score_doc(query_terms: List[str], terms: Dict[str, List[str]]) -> tuple:
    """(score, provenance). Weighted overlap between query terms and each
    field's term set."""
    score = 0.0
    matched_fields: List[str] = []
    matched_terms: List[str] = []
    qset = set(query_terms)
    for field, weight in FIELD_WEIGHTS.items():
        hits = qset & set(terms.get(field, []))
        if hits:
            score += weight * len(hits)
            matched_fields.append(field)
            matched_terms.extend(sorted(hits))
    return score, {"matched_fields": matched_fields,
                   "matched_terms": sorted(set(matched_terms))}


def retrieve(db: Session, query: str, k: int = DEFAULT_K) -> List[dict]:
    """Top-k [{server_id, score, snippet, provenance}] for a query. Bounded:
    scores at most CANDIDATE_LIMIT docs in-process."""
    q_terms = expand_query(query)
    if not q_terms:
        return []
    hits: List[dict] = []
    for doc in db.execute(select(AskCorpusDoc).limit(CANDIDATE_LIMIT)).scalars():
        s, prov = score_doc(q_terms, doc.terms or {})
        if s > 0:
            if _is_assessed(doc.snippet or ""):
                s += ASSESSED_BOOST
            hits.append({"server_id": doc.server_id, "score": round(s, 2),
                         "snippet": doc.snippet, "provenance": prov})
    hits.sort(key=lambda h: (-h["score"], h["server_id"]))
    return hits[:k]


if __name__ == "__main__":
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    docs = [
        AskCorpusDoc(server_id="weak1", snippet="weak github thing",
                     terms={"name": ["github", "server"],
                            "verdict": ["high", "github"],
                            "axes": ["auth_strength", "weak"],
                            "desc": ["weak", "authentication"]}),
        AskCorpusDoc(server_id="strong1", snippet="strong npm thing",
                     terms={"name": ["npm", "server"],
                            "verdict": ["low", "npm"],
                            "axes": ["auth_strength", "strong"],
                            "desc": ["secure"]}),
        AskCorpusDoc(server_id="misc1", snippet="unrelated",
                     terms={"name": ["calculator"], "verdict": ["low"],
                            "axes": [], "desc": ["math"]}),
    ]
    s.add_all(docs)
    s.commit()
    hits = retrieve(s, "weak auth github server")
    assert hits and hits[0]["server_id"] == "weak1", hits
    prov = hits[0]["provenance"]
    assert "axes" in prov["matched_fields"] and "name" in prov["matched_fields"]
    assert "weak" in prov["matched_terms"]
    assert retrieve(s, "") == []
    print("PASS")
