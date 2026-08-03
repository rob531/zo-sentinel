#!/usr/bin/env python3
"""Hand flyctl the credential this project already mandates -- from ONE place.

FU-151. On 2026-07-28 four independent lanes hit the same wall within six hours:
`moat-rescore-weekly` (06:13Z), `mcplookup-nightly-db-backup` (07:10Z),
`discovery-harvest-daily` (11:00Z) and `plan-200k-count-tracker` (12:12Z). Every
one of them shelled out to `flyctl`, and every one of them got

    Error: no access token available. Please login with 'flyctl auth login'

The token was never dead. flyctl enforces its OWN client-side 720h re-login
timer and it aged out at ~730h; the 665-char value in ~/.fly/config.yml still
authenticates against api.fly.io perfectly well, and exporting it as
FLY_API_TOKEN makes the SAME binary at the SAME moment answer
`flyctl auth whoami` immediately. plan-200k measured the whole delta and it is
one environment variable.

FU-137 already established the remedy and shipped it -- into exactly ONE caller
(`db_backups/backup_zo_sentinel.py::_hydrate_fly_token`). Every other flyctl
caller on the box kept reading the ambient credential, so the identical outage
recurred the next day in a different lane. That is the standing scar
[[fix_landed_in_the_watcher_not_the_actor]]: **a helper the caller was never
pointed at is an uncalled helper.** This module exists so there is exactly one
place to point them, and so the NEXT flyctl caller inherits the fix instead of
rediscovering the outage.

AUTHORITY NOTE, stated explicitly because this touches a credential path:
this grants NO new authority and creates NO new secret. The `fly` token already
exists in AgentVault, AgentVault is already this project's mandated key path,
and the callers below already hold the prod access they are using it for. What
changes is only WHICH already-granted credential path flyctl is handed. Whether
a *non-expiring org token* should replace the 30-day interactive re-login is a
separate, open chairman question (FU-134 (a) vs (b)) and is deliberately NOT
decided here.

Never raises. If the vault is unreachable we fall through to whatever ambient
credential exists and let the live proxy attempt be the judge -- a credential
helper that can itself take down the lane is a worse bargain than the bug.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_FETCH = r"D:\agentvault\fetch_secret.py"
VAULT_SERVICE = "fly"


def hydrate_fly_token(env=None, fetch_path=None, runner=None):
    """Export FLY_API_TOKEN from AgentVault if it is not already set.

    Returns (hydrated: bool, note: str). Never raises.

    `env`, `fetch_path` and `runner` are injection points for tests only; the
    production call site passes nothing.
    """
    env = os.environ if env is None else env
    if env.get("FLY_API_TOKEN"):
        return False, "FLY_API_TOKEN already present in environment"

    fetch = Path(fetch_path or env.get("AGENTVAULT_FETCH") or DEFAULT_FETCH)
    if not fetch.exists():
        return False, f"agentvault fetch_secret.py not found at {fetch}"

    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True,
                                                text=True, timeout=60))
    try:
        p = run([sys.executable, str(fetch), VAULT_SERVICE])
        tok = (p.stdout or "").strip()
        if p.returncode == 0 and tok:
            env["FLY_API_TOKEN"] = tok
            return True, f"FLY_API_TOKEN hydrated from AgentVault (len={len(tok)})"
        return False, f"agentvault returned rc={p.returncode} with no token"
    except Exception as e:  # pragma: no cover - degraded but non-fatal by contract
        return False, f"agentvault fetch failed (non-fatal): {e}"


def port_open(port, host="127.0.0.1", timeout=2):
    """True if something is already listening. A live proxy is reused, never duplicated."""
    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except OSError:
        return False


def start_proxy(port, app, err_path, log=None):
    """Hydrate, then spawn `flyctl proxy <port>:5432 -a <app>` with stderr KEPT.

    stderr goes to a FILE, never a PIPE (FU-133): on the success path the proxy
    outlives the caller by hours and nobody drains it, so a pipe would fill its
    buffer and wedge flyctl itself -- trading a silent failure for a worse one.

    Returns the Popen. Does NOT wait for the port; use wait_for_proxy().
    """
    hydrated, note = hydrate_fly_token()
    if log:
        log("fly token: " + note)
    err_path = Path(err_path)
    err_path.parent.mkdir(parents=True, exist_ok=True)
    err_f = open(err_path, "w+", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(["flyctl", "proxy", f"{port}:5432", "-a", app],
                            stdout=subprocess.DEVNULL, stderr=err_f)
    proc._fly_err_file = err_f  # keep the handle alive for the process lifetime
    return proc


def proxy_error_detail(proc, err_path):
    """The last line flyctl actually said, plus its exit code. Never raises.

    The whole point of FU-133: the harness ran the command that explains the
    failure and pointed its output at DEVNULL, then raised a message naming only
    the symptom. A diagnostic that discards the only message naming the cause is
    why this class of outage cost two lanes a day each.
    """
    detail = ""
    try:
        f = getattr(proc, "_fly_err_file", None)
        if f is not None:
            f.flush()
        said = Path(err_path).read_text(encoding="utf-8", errors="replace").strip()
        if said:
            detail = " -- flyctl said: " + said.splitlines()[-1]
    except OSError:
        pass
    rc = proc.poll() if proc is not None else None
    if rc is not None:
        detail = f" (flyctl exited {rc}){detail}"
    return detail


def wait_for_proxy(proc, port, err_path, timeout_s=60, poll_s=2, host="127.0.0.1"):
    """Block until the port answers. Raise RuntimeError NAMING flyctl's own reason.

    A flyctl that has already exited is not waited out for the remaining clock --
    waiting out a corpse is theatre.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(poll_s)
        if port_open(port, host=host):
            return True
        if proc is not None and proc.poll() is not None:
            break
    raise RuntimeError(
        f"fly proxy did not come up in {timeout_s}s"
        + proxy_error_detail(proc, err_path))


def ensure_proxy(port, app, err_path, timeout_s=60, log=None, host="127.0.0.1"):
    """Idempotent: reuse a live proxy, else hydrate + spawn + wait. Converges."""
    if port_open(port, host=host):
        return None
    if log:
        log(f"starting fly proxy {port}:5432 -a {app}")
    proc = start_proxy(port, app, err_path, log=log)
    wait_for_proxy(proc, port, err_path, timeout_s=timeout_s, host=host)
    return proc


def _selftest():
    """ACCEPTANCE: run me directly. No network, no vault, no flyctl required."""
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    # 1. a token already in the environment is never overwritten
    env = {"FLY_API_TOKEN": "preset"}
    hy, note = hydrate_fly_token(env=env)
    check("preset token untouched", hy is False and env["FLY_API_TOKEN"] == "preset"
          and "already present" in note)

    # 2. a missing vault is non-fatal and says so
    env = {}
    hy, note = hydrate_fly_token(env=env, fetch_path=r"Z:\definitely\not\here.py")
    check("missing vault non-fatal", hy is False and "not found" in note
          and "FLY_API_TOKEN" not in env)

    # 3. a working vault hydrates
    here = Path(__file__)

    class R:
        returncode, stdout = 0, "  fm2_tok  \n"
    env = {}
    hy, note = hydrate_fly_token(env=env, fetch_path=str(here), runner=lambda c: R())
    check("vault hydrates", hy is True and env.get("FLY_API_TOKEN") == "fm2_tok")

    # 4. a vault that errors is non-fatal and leaves the env alone
    env = {}

    def boom(cmd):
        raise OSError("vault down")
    hy, note = hydrate_fly_token(env=env, fetch_path=str(here), runner=boom)
    check("vault failure non-fatal", hy is False and "non-fatal" in note
          and "FLY_API_TOKEN" not in env)

    # 5. the error detail carries flyctl's own last line, not just the symptom
    import tempfile

    class P:
        def poll(self):
            return 1
    with tempfile.TemporaryDirectory() as d:
        ep = Path(d) / "e.err"
        ep.write_text("some noise\nError: no access token available\n")
        detail = proxy_error_detail(P(), ep)
    check("error names the cause", "no access token available" in detail
          and "exited 1" in detail)

    # 6. a missing stderr file must not raise
    check("absent stderr file is survivable",
          isinstance(proxy_error_detail(P(), Path("Z:/nope/none.err")), str))

    print(("SELFTEST FAILED: " + ", ".join(fails)) if fails else "SELFTEST OK (6/6)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
