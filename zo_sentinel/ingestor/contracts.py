"""
contracts.py -- the per-artifact validation contracts.

Faithfully modelled on tests/gates/gate_8_new_module.py (the canonical host-side
spec) so the host gate and this ingestor apply the SAME rules:

  * static safety scan BEFORE import: DROP TABLE / DELETE FROM / TRUNCATE on a
    protected core table is an automatic FAIL -- the import IS the test, so a
    hallucinated module-level mutation must never be allowed to run just to
    test it.
  * *_enrichment.py  -> compute_score({}) returns (float in [0,100], dict)
  * admin_*.html     -> interactive (form OR input/button controls)
  * *.html           -> parses + body not empty
  * *.py             -> imports in isolation with no side-effect explosion
  * *.md             -> non-empty

Pure + hermetic: every function takes a path/source and returns a verdict; no
network, no DB, no host assumptions.
"""
from __future__ import annotations

import importlib.util
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from zo_sentinel.ingestor.model import ArtifactType

# KEEP IN SYNC with gate_8_new_module.PROTECTED_CORE_TABLES.
PROTECTED_CORE_TABLES = {
    "mcp_server_registry",
    "mcp_signal_scores",
    "mcp_threat_associations",
    "mcp_risk_register",
    "mcp_attestations",
    "mcp_signal_enrichments",
    "service_health",
    "mesh_memory",
    "agent_outputs",
}

FORBIDDEN_STATIC_PATTERNS = [
    re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE),
    re.compile(r"DELETE\s+FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE),
    re.compile(r"TRUNCATE\s+(?:TABLE\s+)?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(file_path: str) -> ArtifactType:
    name = Path(file_path).name.lower()
    if name.endswith("_enrichment.py"):
        return ArtifactType.ENRICHMENT_PY
    if name.startswith("admin_") and name.endswith(".html"):
        return ArtifactType.ADMIN_HTML
    if name.endswith(".html"):
        return ArtifactType.HTML
    if name.endswith(".py"):
        return ArtifactType.PYTHON
    if name.endswith(".md"):
        return ArtifactType.MARKDOWN
    return ArtifactType.UNKNOWN


# ---------------------------------------------------------------------------
# Static safety scan (runs BEFORE any import)
# ---------------------------------------------------------------------------

def static_safety_scan(source: str) -> Optional[str]:
    """Return a failure reason if source contains a forbidden mutation on a
    protected core table, else None."""
    for pat in FORBIDDEN_STATIC_PATTERNS:
        for m in pat.finditer(source):
            table = m.group(1) if m.groups() else "?"
            if table.lower() in PROTECTED_CORE_TABLES:
                return f"forbidden {m.group(0)[:40]!r} on protected table {table!r}"
    return None


# ---------------------------------------------------------------------------
# Python import (isolated, unique module name, no __main__)
# ---------------------------------------------------------------------------

def import_isolated(py_path: Path):
    """Import a .py without polluting sys.modules cache. Returns (mod, err)."""
    try:
        spec = importlib.util.spec_from_file_location(
            f"ingestor_probe_{py_path.stem}_{int(time.time() * 1000)}", py_path)
        if spec is None or spec.loader is None:
            return None, "spec_from_file_location returned None"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Enrichment contract: compute_score(dict) -> (float in [0,100], dict)
# ---------------------------------------------------------------------------

def check_enrichment_contract(mod) -> tuple[bool, str]:
    fn = getattr(mod, "compute_score", None)
    if fn is None or not callable(fn):
        return False, "missing compute_score() function"
    try:
        result = fn({})
    except Exception as e:
        return False, f"compute_score({{}}) raised: {type(e).__name__}: {e}"
    if not (isinstance(result, tuple) and len(result) == 2):
        return False, f"compute_score returned {type(result).__name__}, expected 2-tuple"
    score, meta = result
    if not isinstance(score, (int, float)):
        return False, f"score is {type(score).__name__}, expected float"
    if not (0 <= float(score) <= 100):
        return False, f"score {score} outside [0, 100]"
    if not isinstance(meta, dict):
        return False, f"second element is {type(meta).__name__}, expected dict"
    return True, ""


# ---------------------------------------------------------------------------
# HTML contract
# ---------------------------------------------------------------------------

class _HTMLChecker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.has_form = False
        self.has_input = False
        self.tags = 0
        self.body_chars = 0

    def handle_starttag(self, tag, attrs):
        self.tags += 1
        if tag == "form":
            self.has_form = True
        if tag in ("input", "select", "button", "textarea"):
            self.has_input = True

    def handle_data(self, data):
        self.body_chars += len(data.strip())


def check_html_contract(path: Path, admin_required: bool) -> tuple[bool, str]:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"read failed: {e}"
    p = _HTMLChecker()
    try:
        p.feed(src)
    except Exception as e:
        return False, f"html parse failed: {e}"
    if p.tags == 0:
        return False, "no HTML tags / empty document"
    if admin_required:
        # SPA pattern accepted: a <form> OR interactive input/button controls.
        if not (p.has_form or p.has_input):
            return False, "admin page has neither <form> nor input controls"
    elif p.body_chars == 0 and p.tags < 2:
        return False, "empty / no renderable content"
    return True, ""


# ---------------------------------------------------------------------------
# Top-level contract dispatch for a single file
# ---------------------------------------------------------------------------

def validate_file(path: Path, artifact_type: ArtifactType) -> tuple[bool, str, str, bool]:
    """Validate one artifact file.

    Returns (ok, contract_name, detail, safety_block). safety_block is True iff
    the failure was the static safety scan (a security stop, not a quality miss).
    """
    if not path.exists():
        return False, "exists", f"artifact file not found: {path}", False

    # .py / enrichment: safety scan FIRST, before any import.
    if artifact_type in (ArtifactType.PYTHON, ArtifactType.ENRICHMENT_PY):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return False, "read", f"read failed: {e}", False
        reason = static_safety_scan(source)
        if reason:
            return False, "static_safety_scan", reason, True

        mod, err = import_isolated(path)
        if err:
            return False, "import", err, False
        if artifact_type is ArtifactType.ENRICHMENT_PY:
            ok, detail = check_enrichment_contract(mod)
            return ok, "enrichment_compute_score", detail, False
        return True, "python_import", "", False

    if artifact_type is ArtifactType.ADMIN_HTML:
        ok, detail = check_html_contract(path, admin_required=True)
        return ok, "admin_html_interactive", detail, False

    if artifact_type is ArtifactType.HTML:
        ok, detail = check_html_contract(path, admin_required=False)
        return ok, "html_parse_nonempty", detail, False

    if artifact_type is ArtifactType.MARKDOWN:
        try:
            ok = path.stat().st_size > 0 and bool(path.read_text(encoding="utf-8", errors="replace").strip())
        except Exception as e:
            return False, "markdown_nonempty", f"read failed: {e}", False
        return ok, "markdown_nonempty", "" if ok else "markdown file is empty", False

    # UNKNOWN: we don't gate types we don't understand -- record + pass through.
    return True, "unknown_passthrough", f"no contract for {path.name}", False
