"""verified_host_suffix_ratchet.py -- standing report-only ratchet over
trust_gating_override.VERIFIED_HOST_SUFFIXES (the product moat's publisher-trust
allow-list).

WHY: trust_gate() caps a verified publisher's CRITICAL->MEDIUM. Host-trust is
granted when the self-reported URL's host EXACT- or dotted-suffix-matches an
entry in VERIFIED_HOST_SUFFIXES. That is only sound when the suffix proves the
PUBLISHER, not merely the HOSTER. A suffix that sits on the same domain subtree
as a MULTI-TENANT provider namespace -- one where any member of the public can
provision an arbitrary hostname (``<bucket>.s3.amazonaws.com``, ``<vault>.vault
.azure.net``, ``<site>.atlassian.net``) -- lets an attacker-provisioned host
earn ``verified_publisher:host`` and downgrade its OWN defamation verdict. That
whole class was found live (CLAUDE-D1-73 moved ``.amazonaws.com / .windows.net /
.supabase.co`` from VERIFIED to SHARED_TENANT_SUFFIXES).

This tool is the STANDING FENCE for that class (CLAUDE-D1-74, seq-73 surface
item 2): it enforces the invariant that VERIFIED_HOST_SUFFIXES is domain-DISJOINT
from an operator-curated denylist of publicly-provisionable provider namespaces,
so a future mis-add by EITHER party (re-adding a moved suffix, adding a new
tenant-admitting one) is caught -- not re-litigated by hand.

THE INVARIANT (deterministic, operator-anchored = pre-committed Control-(b)):
no entry V in VERIFIED_HOST_SUFFIXES may share a domain subtree with any tenant
namespace T in KNOWN_TENANT_PROVISIONABLE -- i.e. V must not be an ancestor of,
a descendant of, or equal to any T. An overlap means a host provisionable under
T is suffix-trusted by V. The denylist is a hardcoded, reviewed constant; the
decision never anchors on a fetched/untrusted value.

REPORT-ONLY: it never mutates trust_gating_override and never blocks a merge by
itself. ``--check`` REDs (rc=1) only on a NEW overlap not in the reviewed
baseline; a currently-known overlap (``.azure.net``, ``.atlassian.net`` -- real
latent candidates that need a live trust_gate proof before being MOVED, so they
are surfaced-not-hidden here, not silently fixed) is baselined as REVIEW_REQUIRED.
A trust module that is missing / unparseable / has an empty or non-string
VERIFIED tuple is UNKNOWN (rc=2), never silently clean (Control-(a) on the tool).

SCOPED LIMITATIONS (honest under-coverage -- the claim is "no NEW verified suffix
overlapping a denylisted provider namespace", NOT "every tenant-trust bypass"):
  - The denylist is curated, not exhaustive. A provider namespace not yet listed
    (a new PaaS, a provider's second apex) is invisible until an operator adds it.
    Additions are the maintenance surface this fence makes cheap, not automatic.
  - Overlap is computed on the domain label tree only. It does not model
    provider-specific provisioning depth (whether the attacker label sits
    directly under T or two levels down); the ancestor/descendant/equal test is
    the conservative superset of "shares a subtree", which can over-flag a
    suffix that is only a legitimate single-tenant sibling. Over-flags surface
    as REVIEW, never as an automatic move -- the author catches over-firing here.

Pure stdlib (ast + json). No app/db/network imports at module scope -> CI-safe
and bare-runnable on the Tower.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
TRUST_MODULE = os.path.join(HERE, "trust_gating_override.py")
BASELINE = os.path.join(HERE, "verified_host_suffix_ratchet_baseline.json")

# -----------------------------------------------------------------------------
# Operator-curated denylist: provider namespaces under which ANY member of the
# public can provision an arbitrary hostname. The suffix proves the HOSTER, never
# the publisher identity. Bare (no leading dot). Each entry is a reviewed fact,
# not a fetched value -- this is the pre-committed Control-(b) trust anchor.
# -----------------------------------------------------------------------------
KNOWN_TENANT_PROVISIONABLE = frozenset({
    # The 3 demonstrated + moved by CLAUDE-D1-73 (kept so a re-add is caught):
    "amazonaws.com",          # S3 buckets / API Gateway / etc. are per-account
    "windows.net",            # <acct>.blob.core.windows.net, *.servicebus.windows.net
    "supabase.co",            # <project>.supabase.co is per-project
    # Azure per-tenant resource namespaces:
    "vault.azure.net",        # <vault>.vault.azure.net (Key Vault) -- CLAUDE-D1-73 item-1
    "blob.core.windows.net", "web.core.windows.net", "queue.core.windows.net",
    "azurewebsites.net",      # <app>.azurewebsites.net (App Service)
    # Atlassian cloud customer sites are self-serve provisionable:
    "atlassian.net",          # <site>.atlassian.net (free-tier Jira/Confluence)
    # Azure/Databricks/GCS tenant namespaces demonstrated LIVE by the
    # ARGON-REFEREE-CLAUDE-D1-74 cross-referee (each caps an attacker host
    # CRITICAL->MEDIUM via verified_publisher:host today; the VERIFIED apexes
    # .azure.com / .databricks.com / .googleapis.com sit on these subtrees):
    "cloudapp.azure.com",     # <name>.<region>.cloudapp.azure.com (VM/public IP)
    "openai.azure.com",       # <resource>.openai.azure.com (Azure OpenAI)
    "documents.azure.com",    # <account>.documents.azure.com (Cosmos DB)
    "cloud.databricks.com",   # <workspace>.cloud.databricks.com
    "storage.googleapis.com", # <bucket>.storage.googleapis.com (virtual-hosted GCS)
    # Common self-serve PaaS / edge / tunnel namespaces:
    "workers.dev", "pages.dev", "r2.dev",     # Cloudflare tenant namespaces
    "vercel.app", "netlify.app", "herokuapp.com", "onrender.com",
    "web.app", "firebaseapp.com", "github.io", "fly.dev",
    "trycloudflare.com", "ngrok.io", "ngrok-free.app",
    "glama.ai", "smithery.ai",
})


# ----------------------------------------------------------------------------- domain tree

def _labels_equal_or_nested(a: str, b: str) -> bool:
    """True iff domains ``a`` and ``b`` are equal or one is a dotted-label suffix
    of the other (they share a subtree). Boundary-safe: compares on leading-dot
    forms so ``azure.net`` and ``vault.azure.net`` overlap but ``azure.net`` and
    ``notazure.net`` do not."""
    a = a.strip().lstrip(".").lower()
    b = b.strip().lstrip(".").lower()
    if not a or not b:
        return False
    if a == b:
        return True
    return ("." + a).endswith("." + b) or ("." + b).endswith("." + a)


def flag_overlaps(verified: Tuple[str, ...], tenant) -> Dict[str, List[str]]:
    """Map each VERIFIED suffix that shares a domain subtree with a tenant
    namespace to the sorted list of overlapping namespaces. Deterministic."""
    out: Dict[str, List[str]] = {}
    for v in verified:
        hits = sorted(t for t in tenant if _labels_equal_or_nested(v, t))
        if hits:
            out[v] = hits
    return out


# ----------------------------------------------------------------------------- source extraction

_VNAME = "VERIFIED_HOST_SUFFIXES"
_DNS_LABEL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


def _noncanonical_reason(s) -> Optional[str]:
    """Return None iff ``s`` is a canonical VERIFIED host suffix, else a reason.
    Canonical = exactly '.' + >=2 DNS labels, each label non-empty and composed
    only of [a-z0-9-] (case-insensitive). This is deliberately STRICT so that a
    degenerate entry the TOOL cannot reason about ('', '.', '   ', 'ssian.net',
    '.com', '..a.b') is rejected as UNKNOWN rather than silently audited CLEAN
    while ``trust_gating_override._host_verified`` (bare ``host.endswith(s)``,
    no label boundary) still grants it verified_publisher:host. Control-(a)."""
    if not isinstance(s, str):
        return f"non-string entry {s!r}"
    if s != s.strip() or any(c.isspace() for c in s):
        return f"whitespace in suffix {s!r}"
    if not s.startswith("."):
        return f"missing leading dot {s!r}"
    body = s[1:]
    if not body:
        return "empty after leading dot"
    labels = body.split(".")
    if len(labels) < 2:
        return f"fewer than 2 DNS labels {s!r}"
    for lab in labels:
        if not lab:
            return f"empty DNS label in {s!r}"
        if any(c not in _DNS_LABEL_CHARS for c in lab.lower()):
            return f"invalid DNS label char in {s!r}"
    return None


def _str_tuple_literal(node: ast.AST) -> Optional[Tuple[str, ...]]:
    """Return the string elements of a tuple/list literal, or None if ``node`` is
    not a sequence literal of string constants."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    vals: List[str] = []
    for e in node.elts:
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            vals.append(e.value)
        else:
            return None  # a non-literal element -> cannot statically trust the set
    return tuple(vals)


def extract_verified(source: str) -> Tuple[Optional[Tuple[str, ...]], Optional[str]]:
    """AST-extract the VERIFIED_HOST_SUFFIXES literal from ``source``.
    Returns (suffixes, None) on success, or (None, reason) -> UNKNOWN when the
    name is absent / rebound / augmented / not a top-level plain string tuple /
    empty / contains a non-canonical suffix. Every one of those is UNKNOWN, never
    treated as an empty-or-degenerate-and-therefore-clean allow-list (Control-(a)).

    Hardening vs the D1-74 cut (ARGON-REFEREE-CLAUDE-D1-74):
      - read ONLY top-level ``tree.body`` bindings, so a function-local shadow of
        the same name cannot overwrite the module constant (ast.walk did);
      - an ``X += (...)`` augmented assignment or a ``del X`` at ANY scope makes
        the static top-level literal untrustworthy -> UNKNOWN;
      - more than one top-level binding (rebind) -> UNKNOWN;
      - each surviving suffix must be canonical (``_noncanonical_reason``)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return None, f"parse_error: {exc}"
    # Any augmented assignment or deletion of the name, at ANY scope, means the
    # final runtime value cannot be read statically from a single literal.
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == _VNAME:
            return None, f"{_VNAME} is augmented (+=) -- not statically verifiable"
        if isinstance(node, ast.Delete) and any(
                isinstance(t, ast.Name) and t.id == _VNAME for t in node.targets):
            return None, f"{_VNAME} is deleted -- not statically verifiable"
    # Only top-level (module-body) bindings define the module constant.
    literals: List[Optional[Tuple[str, ...]]] = []
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == _VNAME for t in targets):
            literals.append(_str_tuple_literal(node.value))
    if not literals:
        return None, f"{_VNAME} not found"
    if len(literals) > 1:
        return None, f"{_VNAME} rebound at top level -- not statically verifiable"
    found = literals[0]
    if found is None:
        return None, f"{_VNAME} is not a literal string tuple"
    if not found:
        return None, f"{_VNAME} is empty"
    for s in found:
        reason = _noncanonical_reason(s)
        if reason is not None:
            return None, f"non-canonical suffix: {reason}"
    return found, None


def audit(source: str, tenant=KNOWN_TENANT_PROVISIONABLE) -> Dict:
    """Pure core: text + denylist -> structured result. UNKNOWN on degenerate
    input so a caller can never read corruption as CLEAN."""
    verified, reason = extract_verified(source)
    if verified is None:
        return {"status": "UNKNOWN", "reason": reason, "overlaps": {}}
    if not tenant:
        return {"status": "UNKNOWN", "reason": "empty tenant denylist", "overlaps": {}}
    return {"status": "OK", "verified": list(verified), "overlaps": flag_overlaps(verified, tenant)}


# ----------------------------------------------------------------------------- baseline

def _load_baseline() -> Tuple[Optional[Dict[str, Dict]], Optional[str]]:
    """Return (reviewed, None) or (None, reason) -> UNKNOWN. A MISSING baseline
    is an empty-reviewed set (legitimate: nothing reviewed yet, so every overlap
    is NEW). A PRESENT-but-corrupt / non-dict / non-dict-``reviewed`` baseline is
    UNKNOWN (rc=2), never silently {} -- that would fail OPEN, reading a corrupt
    or ``{"reviewed": null}`` file as a clean bill (Control-(a) on the baseline)."""
    try:
        with open(BASELINE, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return None, f"baseline unreadable: {exc}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"baseline is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "baseline is not a JSON object"
    reviewed = data.get("reviewed", {})
    if not isinstance(reviewed, dict):
        return None, "baseline.reviewed is not an object"
    return reviewed, None


def _new_overlaps(overlaps: Dict[str, List[str]], reviewed: Dict[str, Dict]) -> Dict[str, List[str]]:
    """Overlaps NOT cleared by the reviewed baseline. An entry clears an overlap
    ONLY if it is a dict with ``status == 'REVIEW_REQUIRED'`` AND its ``tenant_ns``
    covers EVERY currently-overlapping namespace. A ``REJECTED`` status, a
    malformed entry, or a namespace that newly appears under an already-baselined
    suffix is NEW (surfaces rc=1) -- never silently suppressed by mere presence of
    the suffix key."""
    new: Dict[str, List[str]] = {}
    for v, hits in overlaps.items():
        entry = reviewed.get(v)
        if not isinstance(entry, dict) or entry.get("status") != "REVIEW_REQUIRED":
            new[v] = hits
            continue
        covered = entry.get("tenant_ns", [])
        if not isinstance(covered, list) or not set(hits).issubset(set(covered)):
            new[v] = hits
    return new


def _read_trust_source() -> Tuple[Optional[str], Optional[str]]:
    try:
        with open(TRUST_MODULE, "r", encoding="utf-8") as fh:
            return fh.read(), None
    except OSError as exc:
        return None, f"trust module unreadable: {exc}"


# ----------------------------------------------------------------------------- commands

def _cmd_report() -> int:
    src, err = _read_trust_source()
    if src is None:
        print(f"UNKNOWN: {err}")
        return 2
    res = audit(src)
    if res["status"] == "UNKNOWN":
        print(f"UNKNOWN: {res['reason']}")
        return 2
    reviewed, breason = _load_baseline()
    if reviewed is None:
        print(f"UNKNOWN: {breason}")
        return 2
    overlaps = res["overlaps"]
    print(f"verified-host-suffix ratchet: {len(res['verified'])} verified suffixes, "
          f"{len(overlaps)} overlap(s) with {len(KNOWN_TENANT_PROVISIONABLE)} denylisted namespaces")
    new = _new_overlaps(overlaps, reviewed)
    for v in sorted(overlaps):
        status = "NEW" if v in new else "REVIEW (baselined)"
        print(f"  [{status}] {v}  <- tenant-provisionable: {', '.join(overlaps[v])}")
    stale = [v for v in reviewed if v not in overlaps]
    for v in sorted(stale):
        print(f"  [RESOLVED] {v} baselined as review but no longer overlaps (moved/removed) -- --repin to drop")
    if not overlaps:
        print("  CLEAN: no verified suffix overlaps a denylisted provider namespace")
    return 0


def _cmd_check() -> int:
    src, err = _read_trust_source()
    if src is None:
        print(f"UNKNOWN: {err}")
        return 2
    res = audit(src)
    if res["status"] == "UNKNOWN":
        print(f"UNKNOWN: {res['reason']}")
        return 2
    reviewed, breason = _load_baseline()
    if reviewed is None:
        print(f"UNKNOWN: {breason}")
        return 2
    new = _new_overlaps(res["overlaps"], reviewed)
    if new:
        print(f"REGRESSION: {len(new)} NEW verified suffix(es) overlap a tenant-provisionable namespace:")
        for v in sorted(new):
            print(f"  + {v}  <- {', '.join(new[v])}  (attacker host under this earns verified_publisher:host)")
        print("Move it to SHARED_TENANT_SUFFIXES, or add it to the ratchet baseline with a review note.")
        return 1
    print(f"OK: {len(res['overlaps'])} known overlap(s), 0 new vs baseline.")
    return 0


def _cmd_repin() -> int:
    src, err = _read_trust_source()
    if src is None:
        print(f"UNKNOWN: {err}")
        return 2
    res = audit(src)
    if res["status"] == "UNKNOWN":
        print(f"UNKNOWN: {res['reason']}")
        return 2
    reviewed, _ = _load_baseline()  # repin OVERWRITES, so a corrupt prior baseline is not fatal here
    reviewed = reviewed or {}
    out = {
        "_note": ("Reviewed VERIFIED_HOST_SUFFIXES entries that overlap a KNOWN_TENANT_PROVISIONABLE "
                  "namespace. Each is a candidate MOVE to SHARED_TENANT_SUFFIXES pending a demonstrated "
                  "live trust_gate proof (anti-vanity: surface here, move only what is proven). --check "
                  "REDs only on a NEW overlap not listed here."),
        "reviewed": {},
    }
    for v, hits in sorted(res["overlaps"].items()):
        prev = reviewed.get(v, {})
        out["reviewed"][v] = {
            "tenant_ns": hits,
            "status": prev.get("status", "REVIEW_REQUIRED"),
            "ref": prev.get("ref", "surfaced by verified_host_suffix_ratchet (CLAUDE-D1-74)"),
        }
    with open(BASELINE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"repinned baseline: {len(out['reviewed'])} reviewed overlap(s) -> {BASELINE}")
    return 0


# ----------------------------------------------------------------------------- two-pole self-test

_SRC_OK = (
    'VERIFIED_HOST_SUFFIXES = (\n'
    '    ".stripe.com", ".azure.net", ".microsoft.com", ".atlassian.net",\n'
    ')\n'
)
_SRC_MISADD = (
    'VERIFIED_HOST_SUFFIXES = (\n'
    '    ".stripe.com", ".azure.net", ".amazonaws.com",\n'   # re-introduced moved suffix
    ')\n'
)
_SRC_EMPTY = 'VERIFIED_HOST_SUFFIXES = ()\n'
_SRC_NONLIT = 'VERIFIED_HOST_SUFFIXES = tuple(load_suffixes())\n'
_SRC_MISSING = 'OTHER = (".stripe.com",)\n'
_SRC_BROKEN = 'VERIFIED_HOST_SUFFIXES = (\n'   # unterminated -> SyntaxError
_TENANT = frozenset({"amazonaws.com", "vault.azure.net", "atlassian.net"})


def _selftest() -> int:
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'ok' if cond else 'FAIL'}] {label}")

    # classifier: domain-tree overlap is boundary-safe
    check("azure.net overlaps vault.azure.net (ancestor)", _labels_equal_or_nested(".azure.net", "vault.azure.net"))
    check("atlassian.net overlaps atlassian.net (equal)", _labels_equal_or_nested(".atlassian.net", "atlassian.net"))
    check("stripe.com does NOT overlap amazonaws.com", not _labels_equal_or_nested(".stripe.com", "amazonaws.com"))
    check("supabase.com does NOT overlap supabase.co (sibling)", not _labels_equal_or_nested(".supabase.com", "supabase.co"))
    check("notazure.net does NOT overlap azure.net (prefix spoof)", not _labels_equal_or_nested(".notazure.net", "azure.net"))

    # Pole A (GREEN): .azure.net + .atlassian.net are the only overlaps, all baselined -> 0 new
    a = audit(_SRC_OK, _TENANT)
    check("OK source parses", a["status"] == "OK")
    check("OK source flags exactly {.azure.net, .atlassian.net}",
          set(a["overlaps"]) == {".azure.net", ".atlassian.net"})
    check("OK source does NOT flag .stripe.com / .microsoft.com",
          ".stripe.com" not in a["overlaps"] and ".microsoft.com" not in a["overlaps"])
    baseline = {".azure.net": {}, ".atlassian.net": {}}
    new_a = {v for v in a["overlaps"] if v not in baseline}
    check("Pole A: 0 NEW vs baseline (GREEN)", new_a == set())

    # Pole B (RED): a re-added .amazonaws.com is a NEW overlap not in baseline
    b = audit(_SRC_MISADD, _TENANT)
    new_b = {v for v in b["overlaps"] if v not in baseline}
    check("Pole B: .amazonaws.com is flagged", ".amazonaws.com" in b["overlaps"])
    check("Pole B: .amazonaws.com is NEW vs baseline (RED discriminates)", new_b == {".amazonaws.com"})

    # Control-(a): degenerate verified set -> UNKNOWN, never clean
    check("empty VERIFIED -> UNKNOWN", audit(_SRC_EMPTY, _TENANT)["status"] == "UNKNOWN")
    check("non-literal VERIFIED -> UNKNOWN", audit(_SRC_NONLIT, _TENANT)["status"] == "UNKNOWN")
    check("missing VERIFIED -> UNKNOWN", audit(_SRC_MISSING, _TENANT)["status"] == "UNKNOWN")
    check("broken source -> UNKNOWN", audit(_SRC_BROKEN, _TENANT)["status"] == "UNKNOWN")
    check("empty denylist -> UNKNOWN", audit(_SRC_OK, frozenset())["status"] == "UNKNOWN")

    # Control-(a) HARDENING (ARGON-REFEREE-CLAUDE-D1-74 gaps 1 & 2): a degenerate
    # suffix that trust_gate STILL trusts must be UNKNOWN here, never silent-CLEAN.
    check("empty-string suffix -> UNKNOWN", audit('VERIFIED_HOST_SUFFIXES = ("",)\n', _TENANT)["status"] == "UNKNOWN")
    check("bare-dot suffix -> UNKNOWN", audit('VERIFIED_HOST_SUFFIXES = (".",)\n', _TENANT)["status"] == "UNKNOWN")
    check("whitespace suffix -> UNKNOWN", audit('VERIFIED_HOST_SUFFIXES = ("   ",)\n', _TENANT)["status"] == "UNKNOWN")
    check("missing-leading-dot suffix -> UNKNOWN", audit('VERIFIED_HOST_SUFFIXES = ("ssian.net",)\n', _TENANT)["status"] == "UNKNOWN")
    check("single-label suffix -> UNKNOWN", audit('VERIFIED_HOST_SUFFIXES = (".com",)\n', _TENANT)["status"] == "UNKNOWN")
    check("double-dot suffix -> UNKNOWN", audit('VERIFIED_HOST_SUFFIXES = ("..a.b",)\n', _TENANT)["status"] == "UNKNOWN")
    # canonical multi-label entry still parses CLEAN (no over-fire of the validator)
    check("canonical .cloud.google.com stays OK", audit('VERIFIED_HOST_SUFFIXES = (".cloud.google.com",)\n', _TENANT)["status"] == "OK")
    # AST evasion: AugAssign / top-level rebind / del -> UNKNOWN; function-local shadow reads the MODULE constant
    check("AugAssign (+=) -> UNKNOWN",
          audit('VERIFIED_HOST_SUFFIXES = (".stripe.com",)\nVERIFIED_HOST_SUFFIXES += (".amazonaws.com",)\n', _TENANT)["status"] == "UNKNOWN")
    check("top-level rebind -> UNKNOWN",
          audit('VERIFIED_HOST_SUFFIXES = (".stripe.com",)\nVERIFIED_HOST_SUFFIXES = (".azure.net",)\n', _TENANT)["status"] == "UNKNOWN")
    check("del VERIFIED -> UNKNOWN",
          audit('VERIFIED_HOST_SUFFIXES = (".azure.net",)\ndel VERIFIED_HOST_SUFFIXES\n', _TENANT)["status"] == "UNKNOWN")
    shadow = audit('VERIFIED_HOST_SUFFIXES = (".amazonaws.com",)\ndef _l():\n    VERIFIED_HOST_SUFFIXES = (".stripe.com",)\n', _TENANT)
    check("function-local shadow reads MODULE constant (flags .amazonaws.com)",
          shadow["status"] == "OK" and ".amazonaws.com" in shadow["overlaps"])

    # Control-(a) HARDENING (gap 3): baseline status-awareness via _new_overlaps.
    ov = {".azure.net": ["vault.azure.net"]}
    check("REVIEW_REQUIRED+covering clears overlap",
          _new_overlaps(ov, {".azure.net": {"status": "REVIEW_REQUIRED", "tenant_ns": ["vault.azure.net"]}}) == {})
    check("REJECTED status does NOT clear (NEW)",
          _new_overlaps(ov, {".azure.net": {"status": "REJECTED", "tenant_ns": ["vault.azure.net"]}}) == ov)
    check("uncovered new namespace does NOT clear (NEW)",
          _new_overlaps({".azure.net": ["vault.azure.net", "openai.azure.net"]},
                        {".azure.net": {"status": "REVIEW_REQUIRED", "tenant_ns": ["vault.azure.net"]}})
          == {".azure.net": ["vault.azure.net", "openai.azure.net"]})
    check("bare-key (no status) does NOT clear (NEW)", _new_overlaps(ov, {".azure.net": {}}) == ov)

    # gap 4: the 5 live-demonstrated provider namespaces are in the denylist
    for ns in ("cloudapp.azure.com", "openai.azure.com", "documents.azure.com",
               "cloud.databricks.com", "storage.googleapis.com"):
        check(f"denylist contains {ns}", ns in KNOWN_TENANT_PROVISIONABLE)

    # live module sanity: the real trust_gating_override must at least parse here
    src, err = _read_trust_source()
    if src is not None:
        live = audit(src)
        check("live trust module parses (OK, not UNKNOWN)", live["status"] == "OK")

    print("PASS" if ok else "SELFTEST-FAILED")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="ratchet vs baseline (rc=1 NEW overlap, rc=2 unknown)")
    ap.add_argument("--repin", action="store_true", help="rewrite the reviewed baseline from the live module")
    ap.add_argument("--self-test", action="store_true", help="two-pole fixture self-test (no repo needed)")
    args = ap.parse_args(argv)
    if args.self_test:
        return _selftest()
    if args.repin:
        return _cmd_repin()
    if args.check:
        return _cmd_check()
    return _cmd_report()


if __name__ == "__main__":
    raise SystemExit(main())
