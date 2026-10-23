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

import re
from typing import Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AskCorpusDoc
from ask_corpus_indexer import tokenize

FIELD_WEIGHTS = {"name": 4.0, "verdict": 3.0, "axes": 2.0, "cve": 2.0,
                 "desc": 1.0}  # "cve" emitted only for docs with vuln links (FU-264)
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


# Identifier-shaped tokens (CVE-2025-49596, GHSA-xxxx-..., RFC-9110, ...).
# THE 2026-07-02 CVE BUG: tokenize() splits identifiers on hyphens, so a
# "CVE-2025-49596" query degenerated into cve+2025+49596 and matched any doc
# mentioning "2025" or "CVE" -- confidently-cited garbage. Identifiers are now
# matched ONLY as exact substrings of the snippet; their fragments are STRIPPED
# from generic term scoring so year-soup can never clear the answer threshold.
IDENTIFIER_RX = re.compile(r"\b([A-Za-z]{2,10}(?:-[A-Za-z0-9]{2,10}){1,4})\b")
IDENTIFIER_MATCH_SCORE = 8.0


def extract_identifiers(query: str) -> Tuple[List[str], str]:
    """(identifiers_lowercase, query_with_identifiers_removed)."""
    idents = []
    stripped = query
    for m in IDENTIFIER_RX.finditer(query):
        token = m.group(1)
        if any(ch.isdigit() for ch in token):   # word-only hyphenations stay generic
            idents.append(token.lower())
    for i in idents:
        stripped = re.sub(re.escape(i), " ", stripped, flags=re.IGNORECASE)
    return sorted(set(idents)), stripped


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
            # digit-only tokens (years, bare numbers) are weak evidence: half
            # weight, so they can support a match but never carry one alone.
            score += weight * sum(0.5 if t.isdigit() else 1.0 for t in hits)
            matched_fields.append(field)
            matched_terms.extend(sorted(hits))
    return score, {"matched_fields": matched_fields,
                   "matched_terms": sorted(set(matched_terms))}


def retrieve(db: Session, query: str, k: int = DEFAULT_K) -> List[dict]:
    """Top-k [{server_id, score, snippet, provenance}] for a query. Bounded:
    scores at most CANDIDATE_LIMIT docs in-process.

    Identifier semantics: identifier-shaped tokens (CVE-..., GHSA-...) match
    ONLY as exact snippet substrings; their fragments never enter generic term
    scoring. A pure-identifier query with no exact match returns [] -> the
    caller answers INSUFFICIENT instead of year-fragment garbage."""
    idents, stripped = extract_identifiers(query)
    q_terms = expand_query(stripped)
    if not q_terms and not idents:
        return []
    hits: List[dict] = []
    for doc in db.execute(select(AskCorpusDoc).limit(CANDIDATE_LIMIT)).scalars():
        snippet_l = (doc.snippet or "").lower()
        s, prov = score_doc(q_terms, doc.terms or {})
        matched_ids = [i for i in idents if i in snippet_l]
        if matched_ids:
            s += IDENTIFIER_MATCH_SCORE * len(matched_ids)
            prov["matched_fields"] = prov["matched_fields"] + ["identifier"]
            prov["matched_terms"] = sorted(set(prov["matched_terms"] + matched_ids))
        if idents and not matched_ids and not q_terms:
            continue   # pure-identifier query: exact evidence or nothing
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

    # THE CVE BUG (2026-07-02): identifier queries must never degrade into
    # year/fragment soup. A doc mentioning "2025" or "cve" must NOT match a
    # CVE id it doesn't contain...
    s.add(AskCorpusDoc(server_id="conf2025", snippet="SpringOne 2025 conference tool",
                       terms={"name": ["springone", "2025"], "verdict": ["low"],
                              "axes": [], "desc": ["cve", "2025", "talks"]}))
    s.commit()
    assert retrieve(s, "CVE-2025-49596") == [], "identifier fragments leaked"
    # ...while a doc that ACTUALLY contains the id matches with provenance.
    s.add(AskCorpusDoc(server_id="vuln1",
                       snippet="mcp-inspector | tier=HIGH | affected by CVE-2025-49596",
                       terms={"name": ["mcp", "inspector"], "verdict": ["high"],
                              "axes": ["exploit_surface", "broad"], "desc": ["cve"]}))
    s.commit()
    hits = retrieve(s, "CVE-2025-49596")
    assert len(hits) == 1 and hits[0]["server_id"] == "vuln1"
    assert "identifier" in hits[0]["provenance"]["matched_fields"]
    assert "cve-2025-49596" in hits[0]["provenance"]["matched_terms"]
    # mixed query: identifier + generic terms still works, id-doc first
    hits = retrieve(s, "CVE-2025-49596 inspector")
    assert hits[0]["server_id"] == "vuln1"
    print("PASS")
