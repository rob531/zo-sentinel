#!/usr/bin/env python3
"""Tests for the staged->active promotion gate (tools/promote_staged_to_active.py).

Focus on the HOLD paths -- the correctness branches that make the gate more than
a folder move: a missing contract, a route collision, and (the FU-031-defeating
one) a contract that RUNS AND FAILS must all keep the service in staged/. A good
service promotes. Creates throwaway services under services/staged/zztest_*/ and
removes them; runs in OBSERVE semantics (evaluate() never moves anything).
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import promote_staged_to_active as P  # noqa: E402

STAGED = os.path.join(REPO_ROOT, "services", "staged")

_GOOD_TOML = ('[service]\nname = "%s"\n'
              'import_path = "services.active.%s.router"\n'
              'prefix = "%s"\ntag = "t"\norigin = "service"\nauth = "public"\n'
              'needs_data_layer = false\n')

_ROUTER = ('from fastapi import APIRouter\n'
           'router = APIRouter(prefix="%s", tags=["t"])\n'
           '@router.get("/ping")\ndef ping():\n    return {"ok": True}\n')

_CONTRACT_PASS = ('import sys\n'
                  'if __name__ == "__main__":\n'
                  '    print("PASS"); sys.exit(0)\n')
_CONTRACT_FAIL = ('import sys\n'
                  'if __name__ == "__main__":\n'
                  '    print("FAIL: boom"); sys.exit(1)\n')


def _mk(name, *, toml=True, router=True, prefix="/api/zztest", contract=_CONTRACT_PASS):
    d = os.path.join(STAGED, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "__init__.py"), "w").write("\n")
    if toml:
        open(os.path.join(d, "service.toml"), "w").write(_GOOD_TOML % (name, name, prefix))
    if router:
        open(os.path.join(d, "router.py"), "w").write(_ROUTER % prefix)
    if contract is not None:
        open(os.path.join(d, "contract.py"), "w").write(contract)
    return d


class PromotionGateTests(unittest.TestCase):
    created: list[str] = []

    def tearDown(self):
        for name in self.created:
            shutil.rmtree(os.path.join(STAGED, name), ignore_errors=True)
        self.created = []

    def _eval(self, name, **kw):
        self.created.append(name)
        _mk(name, **kw)
        return P.evaluate(name, P._active_taken_routes())

    def test_missing_toml_holds(self):
        v = self._eval("zztest_notoml", toml=False)
        self.assertEqual(v["verdict"], "HOLD")
        self.assertTrue(any("service.toml" in r for r in v["reasons"]))

    def test_no_router_holds(self):
        v = self._eval("zztest_norouter", router=False)
        self.assertEqual(v["verdict"], "HOLD")
        self.assertTrue(any("no router" in r for r in v["reasons"]))

    def test_route_collision_holds(self):
        # /api + /verdict/{server_id} collides with the live verdict_breakdown_api
        d = os.path.join(STAGED, "zztest_collision")
        self.created.append("zztest_collision")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "__init__.py"), "w").write("\n")
        open(os.path.join(d, "service.toml"), "w").write(
            _GOOD_TOML % ("zztest_collision", "zztest_collision", "/api"))
        open(os.path.join(d, "router.py"), "w").write(
            'from fastapi import APIRouter\nrouter = APIRouter(prefix="/api", tags=["t"])\n'
            '@router.get("/verdict/{server_id}")\ndef v(server_id: str):\n    return {}\n')
        open(os.path.join(d, "contract.py"), "w").write(_CONTRACT_PASS)
        v = P.evaluate("zztest_collision", P._active_taken_routes())
        self.assertEqual(v["verdict"], "HOLD")
        self.assertTrue(any("collision" in r for r in v["reasons"]), v["reasons"])

    def test_failing_contract_holds(self):
        # the FU-031-defeating case: static gates pass, but the contract RUNS AND FAILS
        v = self._eval("zztest_broken", contract=_CONTRACT_FAIL)
        self.assertEqual(v["verdict"], "HOLD")
        self.assertTrue(any("contract FAILED" in r for r in v["reasons"]), v["reasons"])
        self.assertFalse(v["contract_ok"])

    def test_good_service_promotes(self):
        v = self._eval("zztest_good")
        self.assertEqual(v["verdict"], "PROMOTE", v["reasons"])
        self.assertTrue(v["contract_ok"])


if __name__ == "__main__":
    unittest.main()
