"""Deterministic rule tests for canonical_family derivation.

These freeze the family-key contract so the materialized column, the dup
analyses, and any future ecosystems re-enrichment all speak the same key.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools", "canonical"))
from family_rules import derive_family, norm_url


def test_forge_urls_collapse_to_owner_repo():
    assert norm_url("https://www.github.com/Foo/Bar.git") == "github.com/foo/bar"
    assert norm_url("git+https://github.com/foo/bar") == "github.com/foo/bar"
    assert norm_url("https://github.com/foo/bar/tree/main/packages/x") == "github.com/foo/bar"
    assert norm_url("https://gitlab.com/o/r/") == "gitlab.com/o/r"


def test_non_forge_urls_survive_normalized():
    assert norm_url("https://Example.com/mcp/thing/") == "example.com/mcp/thing"
    assert norm_url("") is None and norm_url(None) is None


def test_repo_metadata_beats_url():
    meta = json.dumps({"repository": {"url": "https://github.com/real/proj"}})
    fam, rule = derive_family("sid1", "https://npmjs.com/package/x", meta)
    assert (fam, rule) == ("github.com/real/proj", "repo_metadata")


def test_url_fallback_when_no_repo_metadata():
    fam, rule = derive_family("sid2", "https://github.com/a/b", "{}")
    assert (fam, rule) == ("github.com/a/b", "url")


def test_self_fallback_is_stable_and_prefixed():
    fam, rule = derive_family("abcdef0123456789extra", None, None)
    assert fam == "pkg:self/abcdef0123456789" and rule == "self"
    fam2, _ = derive_family("abcdef0123456789extra", "", "not-json")
    assert fam2 == fam  # deterministic + total


def test_malformed_metadata_never_raises():
    for meta in (None, "", "not-json", "{", json.dumps({"repository": 7})):
        fam, rule = derive_family("s", "https://github.com/x/y", meta)
        assert fam == "github.com/x/y" and rule == "url"
