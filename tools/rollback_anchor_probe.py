#!/usr/bin/env python3
"""Prove the staged ROLLBACK ANCHOR is still pullable -- from the registry, not from a record.

WHY THIS EXISTS (FU-191, 2026-07-30). `prod_deploy_staged.md` has named a rollback
image on every stage since 2026-07-25, and until today nothing had ever established
that the named image can actually be fetched. Two prior attempts to close that gap
both stopped short of the question:

  * The release record NAMES an image. A name is not a manifest. `flyctl releases`
    will happily keep printing an ImageRef for a v60 release whose layers are gone.
  * 2026-07-30 05:08Z proved the anchor exists by observing that BOTH prod machines
    were running it (`flyctl machine list`). That is a correct runtime proof and it
    is also the ONE case that does not need proving -- the anchor you can prove this
    way is, by construction, the image already running. The moment prod fires v66,
    the rollback target is v65, which NO machine carries, and the machine-list proof
    evaporates exactly when you need it. That run named the residual hazard honestly
    ("Fly's retention of superseded deployment images has not been established") and
    could find no $0 way to test it. There is one: the registry itself.

WHAT IT MEASURES: a read-only Docker Registry v2 manifest GET against
registry.fly.io, authenticated with the Fly token this project already mandates
(AgentVault -> `tools/fly_token.hydrate_fly_token`; no new credential, no new
authority, and nothing is pulled -- a manifest is a few KiB of JSON).

THE NEGATIVE CONTROL IS MANDATORY, NOT OPTIONAL (R4). A probe that reports 200 for
everything reports 200 for the anchor too, and would have been indistinguishable
from success on the day the anchor was actually gone. So every run ALSO probes a
tag that cannot exist in the same repository and REQUIRES a 404. If the control
does not come back 404 the probe is not discriminating, and the verdict is UNKNOWN
-- never PULLABLE. This is the one guard the class of bug in this ledger keeps
teaching: an assertion never seen red is not evidence.

EXIT CODES -- read the CODE, not the printed line:
    0  PULLABLE   manifest fetched AND the negative control returned 404
    1  MISSING    anchor 404s while the control also 404s (a real, discriminated red)
    2  UNKNOWN    cannot evaluate: auth failure, transport failure, unresolvable ref,
                  or a negative control that failed to discriminate

2 IS NOT A PASS AND IT IS ALSO NOT A FAIL. An auth expiry (flyctl's 720h client
timer, FU-151) must never be rendered as "the rollback image is gone", and a
network blip must never be rendered as "the rollback image is fine".
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REGISTRY_HOST = "registry.fly.io"
DEFAULT_APP = "mcplookup"

# A tag that cannot exist: ULIDs are Crockford base32 and exclude I, L, O and U.
CONTROL_TAG = "deployment-01IIIIIIIIIIIIIIIIIIIIIIII"

MANIFEST_ACCEPT = ",".join([
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
])

RC_PULLABLE, RC_MISSING, RC_UNKNOWN = 0, 1, 2


# --------------------------------------------------------------------------- token
def _hydrate_token(env=None):
    """Reuse the ONE sanctioned token path. Returns (token, note)."""
    env = os.environ if env is None else env
    if env.get("FLY_API_TOKEN"):
        return env["FLY_API_TOKEN"], "FLY_API_TOKEN already in environment"
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from fly_token import hydrate_fly_token  # noqa: WPS433 - deliberate late import
        _, note = hydrate_fly_token(env=env)
        return env.get("FLY_API_TOKEN"), note
    except Exception as exc:  # pragma: no cover - degraded path
        return env.get("FLY_API_TOKEN"), f"fly_token helper unavailable: {exc}"


# ----------------------------------------------------------------------- ref lookup
def split_ref(ref):
    """'registry.fly.io/mcplookup:deployment-01ABC' -> ('mcplookup', 'deployment-01ABC')."""
    if not ref or "/" not in ref or ":" not in ref.rsplit("/", 1)[-1]:
        raise ValueError(f"not a tagged registry ref: {ref!r}")
    repo_and_tag = ref.rsplit("/", 1)[-1]
    repo, tag = repo_and_tag.split(":", 1)
    if not repo or not tag:
        raise ValueError(f"not a tagged registry ref: {ref!r}")
    return repo, tag


def releases(app, runner=None):
    """flyctl releases --json -> list of dicts. Raises on failure (caller maps to UNKNOWN)."""
    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True,
                                                timeout=120))
    proc = run(["flyctl", "releases", "--app", app, "--json"])
    if proc.returncode != 0:
        raise RuntimeError(f"flyctl releases rc={proc.returncode}: "
                           f"{(proc.stderr or proc.stdout or '').strip()[:300]}")
    data = json.loads(proc.stdout)
    if not isinstance(data, list) or not data:
        raise RuntimeError("flyctl releases returned no releases")
    return data


def resolve_anchor(app, version=None, runner=None):
    """The rollback anchor = the image of the CURRENT prod release, unless asked otherwise.

    Returns (image_ref, basis) so the number always ships with how it was resolved (R5).
    """
    rels = releases(app, runner=runner)
    if version is None:
        rel = rels[0]
    else:
        matches = [r for r in rels if str(r.get("Version")) == str(version)]
        if not matches:
            raise RuntimeError(f"no release v{version} for app {app}")
        rel = matches[0]
    ref = rel.get("ImageRef") or ""
    if not ref:
        raise RuntimeError(f"release v{rel.get('Version')} carries no ImageRef")
    return ref, (f"flyctl releases --json -> v{rel.get('Version')} "
                 f"status={rel.get('Status')} created={rel.get('CreatedAt')}")


# --------------------------------------------------------------------------- probe
def _fetch(url, token, opener=None):
    """GET a manifest. Returns (status, digest). Never raises for HTTP status."""
    if opener is not None:
        return opener(url, token)
    basic = base64.b64encode(f"x:{token}".encode()).decode()
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"Basic {basic}",
        "Accept": MANIFEST_ACCEPT,
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, resp.headers.get("Docker-Content-Digest", "")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:
        raise RuntimeError(f"transport failure for {url}: {exc}") from exc


def probe(image_ref, token, opener=None, control_tag=CONTROL_TAG):
    """Probe the anchor AND the negative control. Returns a verdict dict.

    The control is not a courtesy check -- its 404 is what licenses any reading of
    the anchor's status at all.
    """
    out = {"image_ref": image_ref, "control_tag": control_tag}
    if not token:
        out.update(verdict="UNKNOWN", rc=RC_UNKNOWN,
                   reason="no Fly token available (AgentVault + env both empty); "
                          "auth absence is NOT evidence about the image")
        return out

    try:
        repo, tag = split_ref(image_ref)
    except ValueError as exc:
        out.update(verdict="UNKNOWN", rc=RC_UNKNOWN, reason=str(exc))
        return out

    base = f"https://{REGISTRY_HOST}/v2/{repo}/manifests/"
    try:
        ctrl_status, _ = _fetch(base + control_tag, token, opener=opener)
        anchor_status, digest = _fetch(base + tag, token, opener=opener)
    except RuntimeError as exc:
        out.update(verdict="UNKNOWN", rc=RC_UNKNOWN, reason=str(exc))
        return out

    out["control_status"] = ctrl_status
    out["anchor_status"] = anchor_status
    out["digest"] = digest

    if ctrl_status != 404:
        out.update(verdict="UNKNOWN", rc=RC_UNKNOWN,
                   reason=f"negative control returned {ctrl_status}, not 404 -- the probe "
                          f"is not discriminating, so its 200s mean nothing "
                          f"(401/403 here means the token cannot read this repo at all)")
        return out

    if anchor_status == 200:
        out.update(verdict="PULLABLE", rc=RC_PULLABLE,
                   reason=f"manifest 200 (digest {digest or 'n/a'}) with a discriminating "
                          f"404 control")
        return out
    if anchor_status == 404:
        out.update(verdict="MISSING", rc=RC_MISSING,
                   reason="anchor manifest 404 while the control also 404 -- the rollback "
                          "target named in the staged sequence CANNOT be pulled")
        return out
    out.update(verdict="UNKNOWN", rc=RC_UNKNOWN,
               reason=f"anchor returned {anchor_status}: neither present nor absent")
    return out


# ------------------------------------------------------------------------ self-test
def _self_test():
    """Offline. Every assertion below has been seen RED against a wrong implementation."""
    fails = []

    def check(name, cond):
        print(("  ok   " if cond else "  FAIL ") + name)
        if not cond:
            fails.append(name)

    def fake(mapping):
        def _o(url, _token):
            tag = url.rsplit("/", 1)[-1]
            return mapping.get(tag, 404), ("sha256:deadbeef" if mapping.get(tag) == 200 else "")
        return _o

    ref = "registry.fly.io/mcplookup:deployment-01REAL"
    good = fake({"deployment-01REAL": 200})
    r = probe(ref, "tok", opener=good)
    check("present anchor + 404 control => PULLABLE rc0", r["rc"] == RC_PULLABLE)

    r = probe(ref, "tok", opener=fake({}))
    check("absent anchor + 404 control => MISSING rc1", r["rc"] == RC_MISSING)

    # The load-bearing one: a registry that says 200 to everything must NOT pass.
    r = probe(ref, "tok", opener=lambda u, t: (200, "sha256:x"))
    check("control 200 (non-discriminating) => UNKNOWN rc2, never PULLABLE",
          r["rc"] == RC_UNKNOWN and r["verdict"] != "PULLABLE")

    r = probe(ref, "tok", opener=lambda u, t: (401, ""))
    check("401 everywhere => UNKNOWN rc2, never MISSING", r["rc"] == RC_UNKNOWN)

    r = probe(ref, "", opener=good)
    check("no token => UNKNOWN rc2 (auth absence is not image absence)",
          r["rc"] == RC_UNKNOWN)

    r = probe("mcplookup-no-tag", "tok", opener=good)
    check("untagged ref => UNKNOWN rc2", r["rc"] == RC_UNKNOWN)

    def boom(_u, _t):
        raise RuntimeError("transport failure: simulated")
    r = probe(ref, "tok", opener=boom)
    check("transport failure => UNKNOWN rc2", r["rc"] == RC_UNKNOWN)

    r = probe(ref, "tok", opener=fake({"deployment-01REAL": 500}))
    check("anchor 500 => UNKNOWN rc2 (neither present nor absent)", r["rc"] == RC_UNKNOWN)

    check("split_ref parses a real fly ref",
          split_ref("registry.fly.io/mcplookup:deployment-01ABC") == ("mcplookup",
                                                                      "deployment-01ABC"))
    check("control tag is not a legal ULID (excluded letter I)", "I" in CONTROL_TAG)

    print(f"\nself-test: {len(fails)} failure(s)")
    return 1 if fails else 0


# ----------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--app", default=DEFAULT_APP)
    ap.add_argument("--image", help="full registry ref; default = current prod release image")
    ap.add_argument("--release", help="probe this release version's image instead of the current")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="offline assertions, no network, no flyctl")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    token, token_note = _hydrate_token()

    if args.image:
        ref, basis = args.image, "supplied on the command line"
    else:
        try:
            ref, basis = resolve_anchor(args.app, version=args.release)
        except Exception as exc:
            out = {"verdict": "UNKNOWN", "rc": RC_UNKNOWN,
                   "reason": f"could not resolve the anchor ref: {exc}",
                   "token_note": token_note}
            print(json.dumps(out, indent=2) if args.json else
                  f"UNKNOWN rc=2 -- {out['reason']}")
            return RC_UNKNOWN

    out = probe(ref, token, opener=None)
    out["basis"] = basis
    out["token_note"] = token_note
    out["app"] = args.app

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"anchor  : {out['image_ref']}")
        print(f"basis   : {basis}")
        print(f"control : {out.get('control_status')} (must be 404)")
        print(f"anchor  : {out.get('anchor_status')}")
        print(f"VERDICT : {out['verdict']} rc={out['rc']} -- {out['reason']}")
    return out["rc"]


if __name__ == "__main__":
    sys.exit(main())
