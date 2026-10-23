"""Question schema, validation, abstention and order-debiasing for typed decisions.

Contract (mirrors TypeSafe Jev's documented request shape so the Jev backend is a
thin adapter, but nothing here depends on Jev):

    Choice(instructions, options={name: description})   up to 255 options
    Score(instructions, levels=[lowest, ..., highest])  2..10 levels
    Noul(instructions, yes=..., no=...)                 yes/no

A backend returns, per question, a probability vector over the declared options
(levels / {true,false}). core.decide():
  * rejects any vector whose keys differ from the declared set (DecisionError) --
    an out-of-schema answer never reaches the caller;
  * renormalises, picks argmax, and returns Answer(value=None, abstained=True)
    when p_top < floor. Abstention is a first-class outcome, not a default value;
  * optionally answers twice with the option order reversed and averages, because
    TypeSafe's own docs say Jev favours the first-listed option.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Union

MAX_CHOICE_OPTIONS = 255
MIN_LEVELS, MAX_LEVELS = 2, 10
# Jev documents 32k tokens for state; ~4 chars/token is a conservative guard so
# an oversized state fails HERE, loudly, rather than being truncated upstream.
MAX_STATE_CHARS = 32_000 * 4


class DecisionError(ValueError):
    """The question or the backend's answer violates the declared schema."""


@dataclass(frozen=True)
class Choice:
    instructions: str
    options: Mapping[str, str]
    floor: float = 0.5
    type: str = "choice"

    def keys(self) -> List[str]:
        return list(self.options)

    def criteria(self, order: Optional[List[str]] = None):
        order = order or self.keys()
        return {k: self.options[k] for k in order}


@dataclass(frozen=True)
class Score:
    instructions: str
    levels: List[str]
    floor: float = 0.5
    type: str = "score"

    def keys(self) -> List[str]:
        return [str(i) for i in range(len(self.levels))]

    def criteria(self, order: Optional[List[str]] = None):
        # Score order is semantic (low -> high); it is never permuted.
        return list(self.levels)


@dataclass(frozen=True)
class Noul:
    instructions: str
    yes: str = "yes"
    no: str = "no"
    floor: float = 0.5
    type: str = "noul"

    def keys(self) -> List[str]:
        return ["true", "false"]

    def criteria(self, order: Optional[List[str]] = None):
        return {"true": self.yes, "false": self.no}


Question = Union[Choice, Score, Noul]


@dataclass
class Answer:
    value: Optional[str]            # option key / level index (str) / "true"|"false"; None = abstained
    p_top: float
    probabilities: Dict[str, float]
    abstained: bool
    score: Optional[float] = None   # Score only: probability-weighted level position
    backend: str = ""
    meta: Dict = field(default_factory=dict)

    @property
    def yes(self) -> Optional[bool]:
        return None if self.value is None else self.value == "true"


# A backend: (state, {qid: (question, option_order)}) -> {qid: {key: prob}}, meta
Backend = Callable[[Mapping, Mapping[str, tuple]], tuple]


def validate_question(qid: str, q: Question) -> None:
    if not q.instructions or not q.instructions.strip():
        raise DecisionError(f"{qid}: empty instructions")
    if isinstance(q, Choice):
        if not 2 <= len(q.options) <= MAX_CHOICE_OPTIONS:
            raise DecisionError(f"{qid}: choice needs 2..{MAX_CHOICE_OPTIONS} options, got {len(q.options)}")
        if any(not str(v).strip() for v in q.options.values()):
            raise DecisionError(f"{qid}: every option needs a description")
    elif isinstance(q, Score):
        if not MIN_LEVELS <= len(q.levels) <= MAX_LEVELS:
            raise DecisionError(f"{qid}: score needs {MIN_LEVELS}..{MAX_LEVELS} levels")
    elif not isinstance(q, Noul):
        raise DecisionError(f"{qid}: unknown question type {type(q).__name__}")
    if not 0.0 <= q.floor <= 1.0:
        raise DecisionError(f"{qid}: floor must be in [0,1]")


def _normalise(qid: str, q: Question, probs: Mapping[str, float]) -> Dict[str, float]:
    if not isinstance(probs, Mapping):
        raise DecisionError(f"{qid}: backend returned {type(probs).__name__}, not a probability map")
    got, want = set(map(str, probs)), set(q.keys())
    if got != want:
        raise DecisionError(f"{qid}: backend keys {sorted(got)} != declared {sorted(want)}")
    vals = {}
    for k in q.keys():
        v = float(probs[k])
        if v != v or v < 0:  # NaN or negative
            raise DecisionError(f"{qid}: invalid probability {v!r} for {k}")
        vals[k] = v
    total = sum(vals.values())
    if total <= 0:
        raise DecisionError(f"{qid}: probabilities sum to zero")
    return {k: v / total for k, v in vals.items()}


def decide(state: Mapping, questions: Mapping[str, Question], backend: Backend,
           *, debias: bool = False, name: str = "") -> Dict[str, Answer]:
    """Answer every question against one state. Raises DecisionError on any schema breach."""
    if not questions:
        raise DecisionError("no questions")
    for qid, q in questions.items():
        validate_question(qid, q)
    if len(json.dumps(state, default=str)) > MAX_STATE_CHARS:
        raise DecisionError("state exceeds the documented 32k-token budget; summarise it first")

    passes = [{qid: (q, q.keys()) for qid, q in questions.items()}]
    if debias:
        passes.append({qid: (q, list(reversed(q.keys())) if isinstance(q, Choice) else q.keys())
                       for qid, q in questions.items()})

    acc: Dict[str, Dict[str, float]] = {qid: {k: 0.0 for k in q.keys()} for qid, q in questions.items()}
    meta: Dict = {"passes": len(passes)}
    for p in passes:
        raw, m = backend(state, p)
        for k, v in (m or {}).items():
            meta[k] = meta.get(k, 0) + v if isinstance(v, (int, float)) else v
        for qid, q in questions.items():
            if qid not in raw:
                raise DecisionError(f"{qid}: backend returned no answer")
            for k, v in _normalise(qid, q, raw[qid]).items():
                acc[qid][k] += v / len(passes)

    out: Dict[str, Answer] = {}
    for qid, q in questions.items():
        probs = acc[qid]
        top = max(probs, key=probs.get)
        p_top = probs[top]
        abst = p_top < q.floor
        score = None
        if isinstance(q, Score):
            score = sum(int(k) * v for k, v in probs.items())
        out[qid] = Answer(value=None if abst else top, p_top=round(p_top, 4),
                          probabilities={k: round(v, 4) for k, v in probs.items()},
                          abstained=abst, score=score, backend=name or getattr(backend, "name", ""),
                          meta=meta)
    return out
