"""FU-052: the gaps-map extractor must NOT harvest filenames off spec lines that
explicitly say DO NOT / NOT IN SCOPE / dormant.

`dormant` was the only flag word in `_spec_candidate_files` that marked a NEGATIVE,
so the NOT-IN-SCOPE section's own lines --
    "GraphQL surface (`graphql_schema_builder.py` is dormant; do not wire it)"
    "Outbound webhooks (`incident_webhook_dispatcher.py` dormant)"
-- were read as an ask list: the spec said DO NOT and the parser heard DO. It was
harmless only because both modules exist on disk and the gaps map subtracts existing
files; the day either is deleted/renamed, the starvation floor becomes free to seed
work the spec explicitly forbids. Fix: drop `dormant` from the flag words and skip any
flagged line that also carries a NOT-IN-SCOPE / "do not" marker.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from directive_knowledge_sources import _spec_candidate_files


def test_dormant_out_of_scope_line_is_not_harvested():
    line = "GraphQL surface (`graphql_schema_builder.py` is dormant; do not wire it)"
    assert _spec_candidate_files(line) == []


def test_second_dormant_negative_line_is_not_harvested():
    line = "Outbound webhooks to third parties (`incident_webhook_dispatcher.py` dormant)"
    # no positive flag word AND dormant is no longer one -> nothing to harvest
    assert _spec_candidate_files(line) == []


def test_positive_flag_on_a_do_not_line_is_skipped():
    # the backstop: a real flag word ("directive candidate") on a forbidding line
    line = "- directive candidate: `forbidden_thing.py` -- NOT IN SCOPE, do not build this"
    assert "forbidden_thing.py" not in _spec_candidate_files(line)


def test_legitimate_candidate_still_harvested():
    line = "- directive candidate: `real_target.py` -- builds a real reporting surface"
    assert _spec_candidate_files(line) == ["real_target.py"]


def test_not_yet_built_block_still_scanned():
    spec = "\n".join([
        "**Retention / lifecycle daemons (NOT YET BUILT):**",
        "- `retention_sweeper.py` -- age-based expiry",
        "- `exemption_expirer.py` -- nightly check",
    ])
    got = _spec_candidate_files(spec)
    assert "retention_sweeper.py" in got and "exemption_expirer.py" in got


def test_exemplar_exclusion_unbroken_by_dormant_removal():
    # FU-040 property must still hold after this change
    line = "- directive candidate: `edit_class_directive_validator.py` -- validator. Exemplar: `schema_prm_guard.py`."
    got = _spec_candidate_files(line)
    assert got == ["edit_class_directive_validator.py"], got
