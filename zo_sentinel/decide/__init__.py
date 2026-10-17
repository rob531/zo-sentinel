"""Typed decisions: ask a closed question, get back only a value from the set you defined.

The pattern evaluated in docs/eval/JEV_TYPED_DECISIONS_EVAL.md. A caller declares
questions (choice / score / noul) over a state object; a backend answers with
probabilities; core.decide() validates the answer against the declared schema and
ABSTAINS (answer=None) below the caller's confidence floor. An answer outside the
declared options is impossible by construction: it is rejected, never passed on.
"""
from .core import Choice, Score, Noul, Answer, DecisionError, decide  # noqa: F401
