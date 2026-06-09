#!/usr/bin/env python3
"""
minimax_utils.py -- canonical LLM-payload sanitizers.

Consolidates the four scattered copies of the same cleanup logic
(escalation.py, mcp_servers/builder_mcp.py, sentinel_directive_generator.py, and
the retired zo_sentinel_builder.py): strip reasoning preambles
(<think>/<reasoning>/<thinking>/<analysis>, closed or dangling-open) and code
fences from model output, and validate the payload.

Pure / stateless / stdlib-only -> safe to import from any daemon (no heavy deps,
no circular imports). The strip logic is byte-identical to escalation.py's
_strip_reasoning_preamble / _strip_code_fences / _normalize_response (the live
ladder versions), so callers delegating here see ZERO behavior change.
content_is_valid + its constants come from zo_sentinel_builder.py.
"""
import re

_REASONING_CLOSED_RX = re.compile(
    r"<(think|reasoning|thinking|analysis)\b[^>]*>.*?</\1>",
    flags=re.DOTALL | re.IGNORECASE,
)
_REASONING_OPEN_RX = re.compile(
    r"^\s*<(think|reasoning|thinking|analysis)\b[^>]*>",
    flags=re.IGNORECASE,
)
_CODE_START_MARKERS = (
    "<!DOCTYPE", "#!/", "import ", "from ", "def ", "class ",
    "```", "<html", "<!--", "function ", "const ", "let ", "var ",
)

MIN_VALID_BYTES = 300
ERROR_SIGNATURES = [
    "InferenceRouter error", "credit balance is too low",
    "Error code: 400", "Error code: 402", "Error code: 503",
    "invalid_request_error", "[generation failed]",
    "connection refused", "timed out",
]


def strip_reasoning(text: str) -> str:
    """Remove reasoning-mode preambles. Conservative: returns the original if
    stripping would leave nothing useful (never destroys a payload)."""
    if not text:
        return text
    original = text
    text = _REASONING_CLOSED_RX.sub("", text).strip()
    if _REASONING_OPEN_RX.match(text):
        candidates = [text.find(m) for m in _CODE_START_MARKERS]
        candidates = [c for c in candidates if c != -1]
        if candidates:
            text = text[min(candidates):]
    stripped = text.strip()
    return stripped if stripped else original


def strip_reasoning_json(text: str):
    """Like strip_reasoning, but for JSON payloads (e.g. the directive
    generator's lists): on a dangling-open reasoning tag, skip to the first
    '[' or '{' instead of a code marker. Returns (cleaned, did_strip) -- the
    generator's contract."""
    original = text
    text = _REASONING_CLOSED_RX.sub("", text).strip()
    if _REASONING_OPEN_RX.match(text):
        json_starts = [i for i in (text.find("["), text.find("{")) if i != -1]
        if json_starts:
            text = text[min(json_starts):]
    return text.strip(), (text != original)


def strip_code_fences(text: str) -> str:
    """Remove a leading ```lang fence + trailing ``` some models add despite
    'output only the file'."""
    if not text:
        return text
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.split("\n")
    end = len(lines) - 1
    while end > 0 and lines[end].strip() in ("", "```"):
        end -= 1
    result = "\n".join(lines[1:end + 1])
    cleaned = result.strip()
    return cleaned if cleaned else text


def normalize(text: str) -> str:
    """Full sanitize: strip reasoning, then code fences
    (== escalation.py's _normalize_response)."""
    return strip_code_fences(strip_reasoning(text))


def content_is_valid(content: str, task: str = "") -> tuple:
    """Reject truncated stubs (< MIN_VALID_BYTES) and API/quota errors that came
    back as if they were generated content (ERROR_SIGNATURES). Returns
    (ok: bool, reason: str)."""
    if len(content) < MIN_VALID_BYTES:
        return False, "too short (" + str(len(content)) + " bytes)"
    low = content.lower()
    for sig in ERROR_SIGNATURES:
        if sig.lower() in low:
            return False, "error sig: '" + sig + "'"
    return True, "ok"
