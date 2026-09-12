#!/usr/bin/env python3
"""Fail on invalid GITHUB_TOKEN permission keys in .github/workflows/*.yml.

An invalid key does not produce a helpful error. GitHub fails the run instantly
with zero jobs, no annotation and no retrievable log -- indistinguishable from
a recursion-guard ghost run. See pr-relander run 33553962560, which cost an
afternoon to attribute. This check turns that into a one-line message.
"""
import sys, pathlib, yaml

VALID = {
    "actions", "attestations", "checks", "contents", "deployments",
    "discussions", "id-token", "issues", "models", "packages", "pages",
    "pull-requests", "repository-projects", "security-events", "statuses",
}

def keys_of(perms):
    # `permissions: read-all | write-all | {}` are all legal scalars/maps.
    return set(perms) if isinstance(perms, dict) else set()

def main() -> int:
    root = pathlib.Path(".github/workflows")
    if not root.is_dir():
        print("no .github/workflows directory; nothing to check")
        return 0
    bad = []
    for f in sorted(root.glob("*.y*ml")):
        try:
            doc = yaml.safe_load(f.read_text()) or {}
        except yaml.YAMLError as e:
            bad.append(f"{f}: unparseable YAML: {e}")
            continue
        scopes = [("<workflow>", doc.get("permissions"))]
        for jn, jb in (doc.get("jobs") or {}).items():
            if isinstance(jb, dict):
                scopes.append((jn, jb.get("permissions")))
        for where, perms in scopes:
            for k in sorted(keys_of(perms) - VALID):
                bad.append(f"{f}: {where}: invalid permission key '{k}'")
    if bad:
        print("INVALID workflow permission keys found:\n  " + "\n  ".join(bad))
        print("\nValid keys: " + ", ".join(sorted(VALID)))
        return 1
    print(f"workflow permissions OK ({len(list(root.glob('*.y*ml')))} file(s))")
    return 0

if __name__ == "__main__":
    sys.exit(main())
