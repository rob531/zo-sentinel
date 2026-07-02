"""vuln_identity.py -- deterministic identity extraction for vuln linkage.

THE LINE (2026-07-02 council): vuln linkage is EXACT-MATCH ONLY, never fuzzy /
embedding. Both sides (a registry server's url/name, an advisory's package/repo)
are normalized to the SAME canonical identity keys here, so the linker is a
pure set-intersection with confidence 1.0. Pure functions, stdlib only,
unit-testable, no DB.

Canonical keys:
  repo:<host>/<owner>/<name>    e.g. repo:github.com/anthropics/mcp-inspector
  pkg:<ecosystem>/<name>        e.g. pkg:npm/@modelcontextprotocol/inspector
"""
from __future__ import annotations

import re
from typing import List, Optional, Set

_GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")
_REPO_RX = re.compile(
    r"(?:https?://|git@|www\.)?"
    r"(github\.com|gitlab\.com|bitbucket\.org)[/:]"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/#?].*)?$",
    re.IGNORECASE)


def repo_key(url: Optional[str]) -> Optional[str]:
    """Canonical repo identity from any git URL form, or None."""
    if not url:
        return None
    m = _REPO_RX.search(url.strip())
    if not m:
        return None
    host, owner, name = m.group(1).lower(), m.group(2).lower(), m.group(3).lower()
    if name.endswith(".git"):
        name = name[:-4]
    return f"repo:{host}/{owner}/{name}"


def pkg_key(ecosystem: Optional[str], name: Optional[str]) -> Optional[str]:
    """Canonical package identity. Ecosystem lowercased to a stable token."""
    if not ecosystem or not name:
        return None
    eco = ecosystem.strip().lower()
    eco = {"pypi": "pypi", "npm": "npm", "go": "go", "github": "github",
           "cargo": "cargo", "rubygems": "rubygems"}.get(eco, eco)
    return f"pkg:{eco}/{name.strip().lower()}"


def server_identities(url: Optional[str], name: Optional[str],
                      meta: Optional[dict] = None) -> Set[str]:
    """Every canonical identity a registry server can be matched on. Derived
    from its url (repo) + any package coordinates in metadata. Conservative:
    only emits keys it can build deterministically."""
    ids: Set[str] = set()
    rk = repo_key(url)
    if rk:
        ids.add(rk)
    # A bare github.com/owner/name in the NAME field (some registries store it).
    if name and ("github.com" in name.lower() or "/" in name):
        rk2 = repo_key(name if "github.com" in name.lower()
                       else f"github.com/{name}")
        if rk2:
            ids.add(rk2)
    if isinstance(meta, dict):
        eco, pkg = meta.get("ecosystem"), meta.get("package")
        pk = pkg_key(eco, pkg)
        if pk:
            ids.add(pk)
    return ids


def advisory_identities(ecosystem: Optional[str], package: Optional[str],
                        source_url: Optional[str],
                        repo_refs: Optional[List[str]] = None) -> Set[str]:
    """Every canonical identity an advisory can be matched on: its package
    coordinate + any repo references (from OSV 'related'/'references')."""
    ids: Set[str] = set()
    pk = pkg_key(ecosystem, package)
    if pk:
        ids.add(pk)
    for ref in (repo_refs or []):
        rk = repo_key(ref)
        if rk:
            ids.add(rk)
    return ids


if __name__ == "__main__":
    assert repo_key("https://github.com/Anthropics/MCP-Inspector") == \
        "repo:github.com/anthropics/mcp-inspector"
    assert repo_key("git@github.com:owner/repo.git") == "repo:github.com/owner/repo"
    assert repo_key("https://github.com/o/r/tree/main/sub") == "repo:github.com/o/r"
    assert repo_key("https://example.com/notrepo") is None
    assert repo_key(None) is None
    assert pkg_key("npm", "@modelcontextprotocol/inspector") == \
        "pkg:npm/@modelcontextprotocol/inspector"
    assert pkg_key("PyPI", "Requests") == "pkg:pypi/requests"
    # exact-match intersection is the whole game
    sids = server_identities("https://github.com/o/r", None,
                             {"ecosystem": "npm", "package": "foo"})
    aids = advisory_identities("npm", "foo", None, ["https://github.com/o/r"])
    assert sids & aids == {"repo:github.com/o/r", "pkg:npm/foo"}
    # no fuzzy: different repo does NOT match
    assert server_identities("https://github.com/o/r2", None) & \
        advisory_identities("npm", None, None, ["https://github.com/o/r"]) == set()
    print("PASS")
