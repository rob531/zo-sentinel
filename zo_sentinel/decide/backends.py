"""Backends for core.decide(). Each is a callable: (state, plan) -> (raw, meta).

plan = {qid: (question, option_order)}; raw = {qid: {key: prob}}.

* LexicalBackend -- deterministic, offline, free. Token overlap between the state's
  text and each option's description, softmaxed. It is the BASELINE every paid
  backend must beat on the eval set, and the fallback when no key is present.
* JevBackend -- TypeSafe Jev through its documented decisions endpoint. Key comes
  from the environment ONLY (AgentVault injects it on the mesh); this module never
  reads .env files and never logs the key. Endpoint defaults to OpenRouter's
  /api/alpha/decisions; JEV_BASE_URL overrides (e.g. the mesh egress router).
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.request
from typing import Dict, Mapping, Tuple

from .core import Choice, DecisionError, Noul, Score

_TOK = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the of to in on for and or is are be with by this that it as at from show me my".split())


def _toks(s: str):
    return [t for t in _TOK.findall(str(s).lower()) if t not in _STOP and len(t) > 1]


def _state_text(state: Mapping) -> str:
    return json.dumps(state, default=str) if not isinstance(state, str) else state


class LexicalBackend:
    name = "lexical"

    def __init__(self, temperature: float = 0.6, prior: float = 0.0):
        self.temperature, self.prior = temperature, prior

    def _probs(self, text_toks, descs: Dict[str, str]) -> Dict[str, float]:
        bag = set(text_toks)
        logits = {}
        for k, d in descs.items():
            dt = set(_toks(d)) | set(_toks(k.replace("_", " ")))
            hit = len(bag & dt)
            logits[k] = (hit / math.sqrt(len(dt) or 1)) / self.temperature + self.prior
        m = max(logits.values())
        ex = {k: math.exp(v - m) for k, v in logits.items()}
        z = sum(ex.values())
        return {k: v / z for k, v in ex.items()}

    def __call__(self, state, plan) -> Tuple[Dict, Dict]:
        tt = _toks(_state_text(state))
        raw = {}
        for qid, (q, order) in plan.items():
            if isinstance(q, Choice):
                raw[qid] = self._probs(tt, {k: q.options[k] for k in order})
            elif isinstance(q, Noul):
                raw[qid] = self._probs(tt, {"true": q.yes, "false": q.no})
            elif isinstance(q, Score):
                raw[qid] = self._probs(tt, {str(i): lv for i, lv in enumerate(q.levels)})
            else:
                raise DecisionError(f"{qid}: unsupported question")
        return raw, {"calls": 1, "cost_usd": 0.0}


class JevBackend:
    """TypeSafe Jev via the documented decisions request shape.

    Request:  {model, state, questions: {qid: {type, instructions, criteria}}}
    Response: answers[qid] -> choice:{probabilities}, score:{probabilities by index},
              noul:{noul: P(yes)}; usage.cost.
    """
    name = "jev"
    DEFAULT_URL = "https://openrouter.ai/api/alpha/decisions"

    def __init__(self, model: str = None, url: str = None, key_env: str = None,
                 timeout: float = 10.0, opener=None):
        self.model = model or os.environ.get("JEV_MODEL", "typesafe/jev-1.13")  # pin: thresholds drift on -latest
        self.url = url or os.environ.get("JEV_BASE_URL", self.DEFAULT_URL)
        self.key_env = key_env or ("TYPESAFE_API_KEY" if "typesafe.ai" in self.url else "OPENROUTER_API_KEY")
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def available(self) -> bool:
        return bool(os.environ.get(self.key_env))

    @staticmethod
    def payload_questions(plan) -> Dict:
        out = {}
        for qid, (q, order) in plan.items():
            out[qid] = {"type": q.type, "instructions": q.instructions, "criteria": q.criteria(order)}
        return out

    def __call__(self, state, plan):
        key = os.environ.get(self.key_env)
        if not key:
            raise DecisionError(f"{self.key_env} not in environment (inject via AgentVault)")
        body = json.dumps({"model": self.model, "state": state,
                           "questions": self.payload_questions(plan)}, default=str).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        t0 = time.monotonic()
        with self._open(req, timeout=self.timeout) as r:
            resp = json.loads(r.read().decode())
        ms = (time.monotonic() - t0) * 1000
        return self.parse(resp, plan), {"calls": 1, "latency_ms": ms,
                                        "cost_usd": self.cost_from(resp),
                                        "model": resp.get("model", self.model)}

    @staticmethod
    def cost_from(resp: Mapping) -> float:
        return float((resp.get("usage") or {}).get("cost") or 0.0)

    @staticmethod
    def parse(resp: Mapping, plan) -> Dict:
        answers = resp.get("answers")
        if not isinstance(answers, Mapping):
            raise DecisionError(f"jev: no answers in response (keys={sorted(resp)[:6]})")
        raw = {}
        for qid, (q, _order) in plan.items():
            a = answers.get(qid)
            if not isinstance(a, Mapping):
                raise DecisionError(f"jev: missing answer for {qid}")
            if isinstance(q, Noul):
                p = float(a["noul"])
                raw[qid] = {"true": p, "false": 1.0 - p}
            else:
                raw[qid] = {str(k): float(v) for k, v in (a.get("probabilities") or {}).items()}
        return raw


class GlideBackend(JevBackend):
    """Fastino GLiDE: same Noul/Choice/Score request and response shape as Jev.

    POST https://api.fastino.ai/v1/systemone, model must be "fastino/GLiDE",
    key from FASTINO_API_KEY (AgentVault). The response carries usage.input_tokens
    but no cost field, so cost is computed at the documented $0.15 / 1M input tokens
    (output free). Per-question limit is 40k tokens; core's 32k state guard is stricter.
    """
    name = "glide"
    DEFAULT_URL = "https://api.fastino.ai/v1/systemone"
    USD_PER_INPUT_TOKEN = 0.15 / 1_000_000

    def __init__(self, url: str = None, timeout: float = 15.0, opener=None):
        super().__init__(model="fastino/GLiDE", url=url or os.environ.get("GLIDE_BASE_URL", self.DEFAULT_URL),
                         key_env="FASTINO_API_KEY", timeout=timeout, opener=opener)

    @staticmethod
    def cost_from(resp) -> float:
        tok = ((resp.get("usage") or {}).get("input_tokens")) or 0
        return float(tok) * GlideBackend.USD_PER_INPUT_TOKEN
