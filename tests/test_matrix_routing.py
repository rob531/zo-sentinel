"""Matrix-driven rung selection: route to the model that ACTUALLY builds this
directive_type x complexity best (failure_matrix), escalate toward proven-good
alternatives (never the 14% rung), and never route to an empty model."""
import zo_sentinel.build_routing as BR

ROWS = [
    {"directive_type": "utility", "complexity": "medium", "model": "zo-ladder-medium", "attempts": 304, "success_pct": 72.4},
    {"directive_type": "utility", "complexity": "medium", "model": "zo-ladder-high",   "attempts": 125, "success_pct": 14.4},
    {"directive_type": "utility", "complexity": "medium", "model": "MiniMax-Text-01",  "attempts": 15,  "success_pct": 80.0},   # small sample
    {"directive_type": "code",    "complexity": "medium", "model": "",                 "attempts": 38,  "success_pct": 0.0},    # empty -> 0% bug
    {"directive_type": "code",    "complexity": "medium", "model": "MiniMax-M2.7",     "attempts": 10,  "success_pct": 90.0},
    {"directive_type": "code",    "complexity": "high",   "model": "MiniMax-M2.7",     "attempts": 39,  "success_pct": 76.9},
    {"directive_type": "code",    "complexity": "high",   "model": "zo-ladder-medium", "attempts": 50,  "success_pct": 60.0},
]


def test_best_picks_highest_above_min_attempts():
    assert BR.best_model_from_matrix(ROWS, "utility", "medium", min_attempts=20) == "zo-ladder-medium"


def test_skips_empty_model_and_small_sample():
    assert BR.best_model_from_matrix(ROWS, "code", "medium", min_attempts=20) is None       # only ""(skip) + 10-attempt
    assert BR.best_model_from_matrix(ROWS, "code", "medium", min_attempts=5) == "MiniMax-M2.7"


def test_min_success_floor_and_exclude():
    assert BR.best_model_from_matrix(ROWS, "utility", "medium", min_attempts=20, min_success=50) == "zo-ladder-medium"
    assert BR.best_model_from_matrix(ROWS, "utility", "medium", min_attempts=20, min_success=80) is None
    assert BR.best_model_from_matrix(ROWS, "utility", "medium", min_attempts=20, exclude="zo-ladder-medium") == "zo-ladder-high"


def test_build_env_initial_uses_matrix_winner(monkeypatch):
    monkeypatch.delenv("ZO_ESCALATE", raising=False)
    env = BR.build_env_for({"complexity": "medium", "interface": "utility"}, attempt=0, matrix_rows=ROWS)
    assert env["GOOSE_MODEL"] == "zo-ladder-medium"


def test_build_env_falls_back_to_static_when_no_matrix(monkeypatch):
    monkeypatch.delenv("ZO_ESCALATE", raising=False)
    env = BR.build_env_for({"complexity": "medium", "interface": "utility"}, attempt=0, matrix_rows=[])
    assert env["GOOSE_MODEL"] == "zo-ladder-medium"     # static medium route


def test_escalation_retries_winner_when_no_good_alt(monkeypatch):
    monkeypatch.setenv("ZO_ESCALATE", "1")
    # utility/medium: base=zo-ladder-medium(72); only alt is zo-ladder-high(14)<floor -> retry winner, NOT the 14% rung
    env = BR.build_env_for({"complexity": "medium", "interface": "utility"}, attempt=1, matrix_rows=ROWS)
    assert env["GOOSE_MODEL"] == "zo-ladder-medium"


def test_escalation_picks_proven_good_alternative(monkeypatch):
    monkeypatch.setenv("ZO_ESCALATE", "1")
    # code/high: base=MiniMax-M2.7(76.9 winner); alt zo-ladder-medium(60>=floor) -> escalate to it
    env = BR.build_env_for({"complexity": "high", "interface": "code"}, attempt=1, matrix_rows=ROWS)
    assert env["GOOSE_MODEL"] == "zo-ladder-medium"


def test_never_empty(monkeypatch):
    monkeypatch.delenv("ZO_ESCALATE", raising=False)
    env = BR.build_env_for({"complexity": "low", "interface": "doc"}, attempt=0, matrix_rows=ROWS)
    assert env["GOOSE_MODEL"]            # no matrix row for doc/low -> static DEFAULT_ALIAS, never ""
