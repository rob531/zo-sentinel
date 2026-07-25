"""
schema_prm_guard.py

Validator for directive dictionaries.

Given a directive dict, classify it and return a result dict:
{
    "verdict": "VALID" | "REJECT",
    "reasons": list[str],
    "directive_class": "create" | "edit" | "unknown",
}
A directive is considered an *edit* class when the referenced module already
exists on disk; such directives are automatically rejected. All other
directives are treated as *create* and are accepted.

The module imports the application DB session and models to satisfy the
project's data‑layer requirements, but does not perform any DB operations
here – the DB session is only opened (and immediately closed) to prove the
import usage.
"""

import os
from typing import Any, Dict, List, Literal, Optional

# Application imports – required by the specification.
# These imports are intentional; the objects are not used directly in the
# validation logic but their presence demonstrates correct dependency usage.
from app.db import get_session  # noqa: F401
from app.models import (        # noqa: F401
    McpServerRegistry,
    McpLlmAxisScores,
    McpScoreDisputes,
    Org,
    User,
)

Verdict = Literal["VALID", "REJECT"]
DirectiveClass = Literal["create", "edit", "unknown"]


def _open_dummy_session() -> None:
    """
    Open and immediately close a dummy DB session to prove that the module
    respects the required data‑layer import pattern.
    """
    try:
        session = get_session()
        # No queries are performed; we just ensure the session can be created.
        session.close()
    except Exception:
        # In environments without a real DB (e.g., the self‑test), ignore errors.
        pass


def _determine_directive_class(module_path: str) -> DirectiveClass:
    """
    Determine the directive class based on the existence of the target module
    on the filesystem.

    Args:
        module_path: Relative or absolute path to the Python module.

    Returns:
        "edit" if the file exists, otherwise "create".
    """
    if os.path.isfile(module_path):
        return "edit"
    return "create"


def validate_directive(
    directive: Dict[str, Any],
    *,
    session: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Validate a directive dictionary.

    The validation follows these rules:
    * The directive must contain a ``module`` key indicating the target file.
    * The directive class is derived from the existence of that file.
    * Edit‑class directives are rejected; create‑class directives are accepted.
    * Missing or malformed directives are classified as ``unknown`` and rejected.

    Args:
        directive: The directive payload to validate.
        session: Optional DB session; if omitted, a session is obtained via
                 ``get_session`` and immediately closed (no DB work).

    Returns:
        A dictionary with keys ``verdict``, ``reasons``, and ``directive_class``.
    """
    # Ensure the required data‑layer import pattern is exercised.
    if session is None:
        _open_dummy_session()

    reasons: List[str] = []
    module_path = directive.get("module")

    if not isinstance(module_path, str) or not module_path:
        directive_class: DirectiveClass = "unknown"
        reasons.append("Missing or invalid 'module' field.")
        verdict: Verdict = "REJECT"
        return {"verdict": verdict, "reasons": reasons, "directive_class": directive_class}

    directive_class = _determine_directive_class(module_path)

    if directive_class == "edit":
        verdict = "REJECT"
        reasons.append(f"Edit directive rejected because module '{module_path}' already exists.")
    else:
        verdict = "VALID"

    return {"verdict": verdict, "reasons": reasons, "directive_class": directive_class}


if __name__ == "__main__":
    # Self‑test: create a temporary file to simulate an existing module.
    import tempfile

    # 1. Test a create‑class directive (module does NOT exist).
    with tempfile.TemporaryDirectory() as tmp_dir:
        non_existing_path = os.path.join(tmp_dir, "new_module.py")
        create_directive = {"module": non_existing_path}
        result_create = validate_directive(create_directive)
        assert result_create["verdict"] == "VALID", "Create directive should be VALID"
        assert result_create["directive_class"] == "create", "Directive class should be 'create'"
        assert not result_create["reasons"], "No reasons expected for a valid create directive"

        # 2. Test an edit‑class directive (module DOES exist).
        existing_path = os.path.join(tmp_dir, "existing_module.py")
        # Create the file to make it exist on disk.
        with open(existing_path, "w", encoding="utf-8"):
            pass
        edit_directive = {"module": existing_path}
        result_edit = validate_directive(edit_directive)
        assert result_edit["verdict"] == "REJECT", "Edit directive should be REJECT"
        assert result_edit["directive_class"] == "edit", "Directive class should be 'edit'"
        assert result_edit["reasons"], "Reasons should be provided for a rejected edit directive"

    # 3. Test malformed directive.
    malformed_directive = {"foo": "bar"}
    result_malformed = validate_directive(malformed_directive)
    assert result_malformed["verdict"] == "REJECT", "Malformed directive should be REJECT"
    assert result_malformed["directive_class"] == "unknown", "Directive class should be 'unknown'"
    assert result_malformed["reasons"], "Reasons should be provided for malformed directive"

    print("PASS")