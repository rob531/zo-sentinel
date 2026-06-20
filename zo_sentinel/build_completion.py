"""
build_completion.py -- the single definition of "a directive actually built".

A directive is DONE only when its declared output_file lands on disk. goose_runner
historically stamped `<id>.done.json` whenever the goose PROCESS exited 0 -- even
when no file was produced -- so a directive goose "ran" but never built got a
permanent .done sentinel and was skipped forever after ("non-eligible"). Those
ghost-completions accumulated into a graveyard that starves the build loop.

These are PURE helpers (no host paths baked in beyond an overridable default, no
import-time side effects) so both sides agree on one definition:
  - goose_runner uses output_confirmed() to gate completion (PREVENT), and the
    ghost-attempt counters to cap retries before giving up.
  - tools/sweep_ghost_done.py uses declared_output()/output_present() to find and
    clear bogus .done sentinels left by the regression (REMEDIATE).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_HOME = "/home/workspace/zo_sentinel"
MIN_OUTPUT_BYTES = 32        # a real module/file is never smaller; rejects empty stubs
MAX_GHOST_ATTEMPTS = 3       # ghost runs tolerated before a directive is failed, not done

# Edit-class task verbs MODIFY existing files (or wire/migrate things together)
# rather than creating a new <task>.py -- so output-file existence can't verify
# them; trust process success (the directive's own smoke-test is the real check).
# The directive_architect's propose_directive requires an output_file, so it
# stamps a bogus output_file=<task>.py on these, which made the ghost-guard fail
# them forever (e.g. wire_admin_submissions_html_to_registry_api.py, never
# created). This list makes the guard ignore that. Keep in sync with the copy in
# zo_sentinel/mcp_servers/directive_mcp.py.
EDIT_TASK_PREFIXES = ("wire_", "rewire_", "unwire_", "integrate_",
                      "migrate_", "refactor_", "patch_")


def is_edit_task(directive: dict) -> bool:
    """True if the directive edits existing files / wires things together rather
    than creating a new output file (so output-file existence can't verify it)."""
    name = str(directive.get("task") or directive.get("directive_id")
               or directive.get("key") or directive.get("id") or "")
    return name.startswith(EDIT_TASK_PREFIXES)


def failed_quarantined(directive_id, *dirs) -> bool:
    """True if a <directive_id>.failed.json quarantine sentinel exists in ANY of
    the given directories. goose_runner.is_goose_eligible uses this to honor BOTH
    the legacy in-repo directives/ path AND a DURABLE store outside the git tree --
    so `git clean` on a daemon respawn/refresh can no longer un-quarantine a parked
    directive (the re-flush treadmill). Never raises."""
    name = f"{directive_id}.failed.json"
    for d in dirs:
        try:
            if (Path(d) / name).exists():
                return True
        except Exception:
            continue
    return False


def output_file_is_sane(output_file) -> Tuple[bool, str]:
    """Reject an obviously-malformed declared output filename that can never be
    produced and would ghost-loop forever -- specifically a DOUBLED LEADING
    PREFIX like 'admin_admin_ui_suite.py' (the build_admin_ui_suite poison
    observed 2026-06). Conservative: flags ONLY a consecutively-repeated leading
    token in the stem, so it never false-rejects a legitimate file. An empty or
    None output_file (edit-class directives) is sane. Returns (ok, reason).

    Keep in sync with the inlined copy in
    promoters/proposed_to_pending_promoter._validate (which is stdlib-only and
    deliberately does not import this module)."""
    if not output_file or not isinstance(output_file, str):
        return True, "ok"
    stem = Path(output_file).stem
    parts = [p for p in stem.split("_") if p]
    if len(parts) >= 2 and parts[0] == parts[1]:
        return False, f"malformed output_file (doubled leading prefix): {stem!r}"
    return True, "ok"


def declared_output(directive: dict, home: str = DEFAULT_HOME) -> Optional[Path]:
    """The file a directive claims it will produce, resolved under `home`.

    None when the directive declares no single output (goal-based / wire /
    investigate directives that modify existing files) -- those can't be verified
    by file existence, so the caller trusts process success for them.

    Edit-class tasks (wire/rewire/integrate/...) likewise return None even when a
    bogus output_file was stamped on them -- they modify existing files, so there
    is no new file to confirm."""
    if is_edit_task(directive):
        return None
    out = directive.get("output_file") or directive.get("target_file")
    if not out or not isinstance(out, str):
        return None
    p = Path(out)
    return p if p.is_absolute() else Path(home) / out


def output_present(path: Path, min_bytes: int = MIN_OUTPUT_BYTES) -> bool:
    """True if `path` is a non-trivial file (exists and >= min_bytes)."""
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def output_confirmed(directive: dict, home: str = DEFAULT_HOME,
                     min_bytes: int = MIN_OUTPUT_BYTES) -> bool:
    """True if the directive produced its declared output (or declares none).

    False ONLY when it declares an output_file that is missing or empty -- the
    ghost-completion signature."""
    out = declared_output(directive, home)
    if out is None:
        return True
    return output_present(out, min_bytes)


# --- ghost-attempt tracking -------------------------------------------------
# A sidecar next to the .done/.failed sentinels, so a directive goose keeps
# "succeeding" on without producing its file is retried a bounded number of
# times and then failed (surfaced) rather than retried forever.

def _ghost_path(directives_dir, directive_id: str) -> Path:
    return Path(directives_dir) / f"{directive_id}.ghost.json"


def ghost_attempts(directives_dir, directive_id: str) -> int:
    try:
        data = json.loads(_ghost_path(directives_dir, directive_id).read_text(encoding="utf-8"))
        return int(data.get("attempts", 0))
    except (OSError, ValueError, TypeError):
        return 0


def bump_ghost(directives_dir, directive_id: str, when: str) -> int:
    """Record one ghost attempt; return the new running count."""
    n = ghost_attempts(directives_dir, directive_id) + 1
    try:
        _ghost_path(directives_dir, directive_id).write_text(
            json.dumps({"attempts": n, "last": when}), encoding="utf-8")
    except OSError:
        pass
    return n


def clear_ghost(directives_dir, directive_id: str) -> None:
    """Drop the ghost counter -- called on a real (file-confirmed) completion."""
    try:
        _ghost_path(directives_dir, directive_id).unlink()
    except OSError:
        pass
