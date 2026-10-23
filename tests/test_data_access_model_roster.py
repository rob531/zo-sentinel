"""goose_runner._data_access_context must inline the COMPLETE app.models roster.

WHY. That block is folded into EVERY build task (goose recipes AND the engine
fallback), and until 2026-09-20 its only statement about which model classes
exist was a pointer to docs/SCHEMA_TRUTH.md -- a file the engine, a single chat
completion with no filesystem, cannot open -- plus four names given as spelling
examples. Measured on the live runner log (2026-09-16T10:58Z..2026-09-20T06:24Z,
the full window the un-rotated file covers): 145 ghost-guard give-ups, 95 of
them gate=selftest, 77 lines of the form "'X' is not in 'Y'". The app.models
half of those are ServiceHealth, OrgService, McpRiskRegister, MCPSignalScores,
MeshMemory, McpThreatAssociation and McpSignalScore -- every one plausible,
every one absent from a 14-class roster that was never shown to the model.

THE NEGATIVE CONTROL IS THE FIRST TEST IN THIS FILE, and it is not decorative:
it feeds the exact pre-fix string to the same predicate the positive test uses
and requires it to FAIL. An assertion never observed RED is an untested branch,
not evidence (HARNESS_DOCTRINE R4). If someone reverts the grounding, test 1
keeps passing and tests 2-4 go red; if someone weakens the predicate until
anything passes, test 1 goes red. Neither direction is silent.
"""
import goose_runner as g


# The verbatim text this block shipped with before 2026-09-20. Kept as a
# fixture so the negative control tests the REAL prior artifact, not a
# paraphrase of it.
PRE_FIX_SYMBOL_TEXT = (
    "SYMBOL TABLE: docs/SCHEMA_TRUTH.md, generated from app/models.py + app/db.py, lists "
    "every name that EXISTS -- read it before writing an import. If a model is not in it "
    "it does not exist: use the nearest real class or flag that the directive needs a "
    "schema decision, never invent one. Spelling is the commonest miss -- prefix Mcp not "
    "MCP, never plural: McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory. "
)

# A 14-class roster shaped like the live one (schema KL, 2026-09-20). The four
# names the pre-fix text happened to carry are deliberately included, so that
# the negative control fails on the OTHER ten rather than on all fourteen --
# i.e. the predicate is measuring completeness, not mere non-emptiness.
FAKE_KL = {"models": {n: {"table": n.lower(), "columns": ["id"]} for n in (
    "ApiKey", "AskCorpusDoc", "CadenceJobRun", "McpLlmAxisScore",
    "McpScoreDispute", "McpServerRegistry", "Org", "Perspective",
    "PerspectiveEvent", "PerspectiveSnapshot", "ThreatIntelRef", "User",
    "VulnAdvisory", "VulnLink")}}

# Observed in the live log as "'X' is not in 'app.models'". None exists.
PHANTOMS = ("ServiceHealth", "OrgService", "McpRiskRegister", "MCPSignalScores",
            "MeshMemory", "McpThreatAssociation", "McpSignalScore")


def missing_from(block, names):
    """The predicate, shared by the negative control and the positive test."""
    return [n for n in names if n not in block]


def reset_caches():
    """Reset every per-process cache this block reads.

    Deliberately tolerant of a module that lacks the newer names. On the first
    run of the negative control the pre-fix module raised AttributeError here,
    so four of the five RED results were "this symbol is new" rather than "the
    old text fails the predicate" -- a control that measures the wrong thing is
    not a control. With this guard the pre-fix pole fails on the CONTENT
    assertions, which is the claim actually being made.
    """
    g._DATA_ACCESS_CTX = None
    if hasattr(g, "_DATA_ACCESS_KEY"):
        g._DATA_ACCESS_KEY = None
    memo = getattr(g, "_MODEL_ROSTER_MEMO", None)
    if isinstance(memo, dict):
        memo["mtime"] = None
        memo["names"] = None


def render(monkeypatch, kl=FAKE_KL, raises=False):
    def fake(*_a, **_k):
        if raises:
            raise RuntimeError("KL unreadable")
        return kl, "ok"
    monkeypatch.setattr(g, "_schema_kl_cached", fake)
    reset_caches()
    try:
        return g._data_access_context({"directive_id": "build_anything"})
    finally:
        reset_caches()


# ---------------------------------------------------------------- 1. RED
def test_negative_control_the_pre_fix_text_fails_the_roster_predicate():
    """The artifact this change replaces must NOT satisfy the new assertion.

    Ten of the fourteen real class names are absent from the pre-fix text, and
    the unfollowable pointer is present. Both are asserted, so the predicate
    cannot be satisfied by a block that merely mentions a few model names.
    """
    names = sorted(FAKE_KL["models"])
    absent = missing_from(PRE_FIX_SYMBOL_TEXT, names)
    assert len(absent) == 10, absent
    assert "docs/SCHEMA_TRUTH.md" in PRE_FIX_SYMBOL_TEXT


# --------------------------------------------------------------- 2. GREEN
def test_every_real_class_name_is_inlined(monkeypatch):
    block = render(monkeypatch)
    assert missing_from(block, sorted(FAKE_KL["models"])) == []
    assert "COMPLETE roster" in block


def test_the_unfollowable_pointer_is_gone(monkeypatch):
    """A prompt that names an authority the engine cannot reach is worse than
    silence: the model is sent to fetch truth it cannot get, and invents it."""
    assert "docs/SCHEMA_TRUTH.md" not in render(monkeypatch)


def test_no_phantom_class_is_endorsed(monkeypatch):
    block = render(monkeypatch)
    assert [p for p in PHANTOMS if p in block] == []


# ------------------------------------------------- 3. R6: unknown != zero
def test_kl_error_restores_the_shipped_text_and_never_claims_an_empty_roster(monkeypatch):
    """An unreadable KL must not render "the COMPLETE roster is: <nothing>".

    That would assert a falsehood with more authority than the pointer it
    replaced -- the failure mode R6 exists for. It falls back to exactly the
    text that shipped before, pointer and all.
    """
    block = render(monkeypatch, raises=True)
    assert "docs/SCHEMA_TRUTH.md" in block
    assert "COMPLETE roster" not in block
    assert "roster is: ." not in block
    assert "roster is: ," not in block


def test_the_block_is_idempotent_across_calls(monkeypatch):
    """Re-rendering must be a no-op, not an accumulation: this string is
    concatenated into every build task and a cache keyed on the roster must
    return the identical block rather than appending to it."""
    def fake(*_a, **_k):
        return FAKE_KL, "ok"
    monkeypatch.setattr(g, "_schema_kl_cached", fake)
    reset_caches()
    try:
        first = g._data_access_context({"directive_id": "d"})
        second = g._data_access_context({"directive_id": "d"})
        assert first == second
        assert first.count("COMPLETE roster") == 1
    finally:
        reset_caches()
