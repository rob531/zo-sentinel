"""family_rules: deterministic canonical-family key derivation.

Lineage: Commit-B canonicalizer (Feb-Apr 2026) locked the doctrine --
deterministic rules only, sticky assignment, provenance stamped. Its
ecosyste.ms cousin KV was lost in a mesh rebuild; these are the surviving
deterministic rules, identical to the measurement rule used by
registry_family_dedup_report / family_count (2026-07-16..18 dup analyses),
plus the predecessor's pkg:self fallback for continuity.

Rule order (first hit wins):
  repo_metadata  metadata.repository(.url) normalized to forge owner/repo
  url            row url normalized (forge urls collapse to owner/repo)
  self           pkg:self/<server_id[:16]>  (no evidence of siblings)
"""
import json
import re

_FORGE = re.compile(r"(github\.com|gitlab\.com|bitbucket\.org)/([^/]+/[^/#?]+)")


def norm_url(u):
    """Normalize a repo/homepage URL to a stable family key. None if empty."""
    if not u:
        return None
    u = str(u).strip().lower()
    if not u:
        return None
    u = re.sub(r"^git\+|\.git$", "", u)
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = re.sub(r"/+$", "", u)
    m = _FORGE.match(u)
    if m:
        return m.group(1) + "/" + m.group(2)
    return u or None


def repo_from_metadata(meta):
    """Extract repository url from a registry metadata JSON string."""
    if not meta or not str(meta).startswith("{"):
        return None
    try:
        j = json.loads(meta)
    except Exception:
        return None
    repo = j.get("repository") or j.get("repository_url")
    if isinstance(repo, dict):
        repo = repo.get("url")
    return repo if isinstance(repo, str) else None


def derive_family(server_id, url, metadata):
    """Return (canonical_family, rule). Deterministic, total."""
    rk = norm_url(repo_from_metadata(metadata))
    if rk:
        return rk, "repo_metadata"
    uk = norm_url(url)
    if uk:
        return uk, "url"
    return "pkg:self/%s" % str(server_id)[:16], "self"
