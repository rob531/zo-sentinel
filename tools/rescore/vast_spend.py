#!/usr/bin/env python3
r"""vast_spend.py -- authoritative Vast.ai spend, read from Vast itself.

FU-035 (confirmed on three consecutive days). The daily ops audit computed
month-to-date spend as a DELTA against a local state file,
D:/zo/runs/ops_audit_state.json. The earliest July entry in that file is
2026-07-20 and the balance recorded there has not moved since, so the delta
evaluated to $0.00 EVERY DAY and the ">= $20" alarm was structurally
incapable of firing for the whole of July. A budget guard whose only failure
mode is a silent all-clear is worse than no guard at all: it launders "I do
not know" into "everything is fine". Same shape as FU-093 (row counts as a
proxy for valid scores) and FU-107 (a green status over a skipped backup) --
a gate reading a PROXY instead of the thing it claims to assert.

THE AUTHORITATIVE METHOD
------------------------
Ask Vast. GET /api/v0/users/current/invoices/ returns the account's invoice
list plus a `current` block:

    {"invoices": [{"type": "payment", "amount": -25.0, ...}],
     "current":  {"charges": 0, "service_fee": 0, "total": 0,
                  "credit": 18.784030982420944}}

Spend since the last top-up = |last payment amount| - current.credit.
Verified live 2026-07-26: one -25.00 payment, credit 18.7840 => $6.22 spent.
The delta method reported $0.00 for the same period.

Properties this module holds on to, because their absence is the defect:
  * NO local state. It cannot be poisoned by, and does not depend on, a stale
    or lost file. Restore the tower from bare metal and it still works.
  * FAIL LOUD. Network failure, a missing key, a malformed payload, or no
    payment invoice all raise VastSpendError. It NEVER returns 0.00 or None
    as a stand-in for "could not tell" -- returning zero on failure is
    precisely the bug being fixed.
  * The key comes from the AgentVault convention (`fetch_secret.py vast`,
    which itself resolves env var -> Windows keyring -> keys.env). No
    hardcoded secret and no raw os.environ read of the key.

USAGE
-----
    python tools/rescore/vast_spend.py            # self-test, then live figure
    python tools/rescore/vast_spend.py selftest   # offline only (CI-safe)
    python tools/rescore/vast_spend.py live       # live figure only

    import sys, os
    sys.path.insert(0, os.path.join(REPO, "tools", "rescore"))
    from vast_spend import spend_since_topup, remaining_credit

(The same import convention weekly_rescore.py already uses for spend_guard.)

NOTE -- WIRING IS DELIBERATELY NOT IN THIS PR
---------------------------------------------
This lands the correct, tested MEASUREMENT only. The daily ops-audit task and
zo_sentinel/vast_jobs.audit() still derive MTD from
D:/zo/runs/ops_audit_state.json and must be switched to spend_since_topup()
in a follow-up, so that swap is reviewable on its own and this change cannot
take the audit down with it. Until then, treat the audit's MTD figure as
KNOWN-BROKEN (always $0.00) and read this module's output instead.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

INVOICES_URL = os.environ.get(
    "VAST_INVOICES_URL", "https://console.vast.ai/api/v0/users/current/invoices/")
ACCOUNT_INVOICES_URL = os.environ.get(
    "VAST_ACCOUNT_INVOICES_URL", "https://console.vast.ai/api/v0/invoices/")
INSTANCES_URL = os.environ.get(
    "VAST_INSTANCES_URL", "https://console.vast.ai/api/v0/instances/")
FETCH_SECRET = os.environ.get("AGENTVAULT_FETCH_SECRET",
                              r"D:\agentvault\fetch_secret.py")
SECRET_NAME = "vast"
TIMEOUT_S = int(os.environ.get("VAST_SPEND_TIMEOUT", "60"))

# memory: vast_budget_25_monthly -- $25/month, alert at $20.
MONTHLY_BUDGET_USD = 25.00
ALERT_AT_USD = 20.00


class VastSpendError(RuntimeError):
    """Spend could not be established.

    Raised rather than degraded to 0.00. The whole point of FU-035 is that a
    guard which cannot measure must SAY SO -- an unmeasurable budget is an
    alarm condition, not an all-clear.
    """


def api_key(fetch_secret: str = FETCH_SECRET, name: str = SECRET_NAME) -> str:
    """Vast API key via the AgentVault convention. Never a raw env read."""
    try:
        out = subprocess.run([sys.executable, fetch_secret, name],
                             capture_output=True, text=True, timeout=60)
    except Exception as exc:                     # noqa: BLE001 -- fail loud
        raise VastSpendError(
            "AgentVault fetch_secret({!r}) could not run: {}: {}".format(
                name, exc.__class__.__name__, exc)) from exc
    val = out.stdout.strip().splitlines()[-1].strip() if out.stdout.strip() else ""
    if not val:
        raise VastSpendError(
            "AgentVault secret {!r} is empty (rc={}): {}".format(
                name, out.returncode, (out.stderr or "")[:200]))
    return val


def fetch_invoices(key: Optional[str] = None, url: str = INVOICES_URL,
                   timeout: int = TIMEOUT_S) -> Dict[str, Any]:
    """Raw invoices payload. Raises VastSpendError on ANY failure."""
    key = key or api_key()
    req = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + key,
                      "Accept": "application/json",
                      "User-Agent": "zo-sentinel-vast-spend/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise VastSpendError(
            "vast invoices API HTTP {}: {} -- refusing to report $0.00 "
            "(FU-035)".format(exc.code, exc.reason)) from exc
    except Exception as exc:                     # noqa: BLE001 -- fail loud
        raise VastSpendError(
            "vast invoices API unreachable ({}: {}) -- refusing to report "
            "$0.00 (FU-035)".format(exc.__class__.__name__, exc)) from exc
    if not isinstance(payload, dict) or "current" not in payload:
        raise VastSpendError(
            "vast invoices payload has no 'current' block; got {}".format(
                sorted(payload)[:12] if isinstance(payload, dict)
                else type(payload).__name__))
    if not [i for i in (payload.get("invoices") or [])
            if isinstance(i, dict) and i.get("type") == "payment"]:
        # API drift observed 2026-08-30: this endpoint now returns an EMPTY
        # invoices list; settled payments moved to /invoices/. Fall back.
        payload = _merge_account_payments(payload, key=key, timeout=timeout)
    return payload


def _map_account_rows(rows: Any) -> List[dict]:
    """Map /invoices/ rows (amount_cents, is_credit, paid_on -- shape observed
    live 2026-08-30) into the legacy payment shape last_topup() consumes.
    Only SETTLED credits qualify: is_credit, paid_on set, negative cents."""
    return [
        {"type": "payment", "id": r.get("id"),
         "amount": (r.get("amount_cents") or 0) / 100.0,
         "timestamp": r.get("when"), "paid_timestamp": r.get("paid_on"),
         "failed": None, "refunded": None}
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and r.get("is_credit") and r.get("paid_on")
        and (r.get("amount_cents") or 0) < 0]


def _merge_account_payments(payload: Dict[str, Any], key: Optional[str] = None,
                            url: str = ACCOUNT_INVOICES_URL,
                            timeout: int = TIMEOUT_S) -> Dict[str, Any]:
    """Fetch the account-level /invoices/ endpoint and merge its settled
    credits into payload["invoices"]. Raises on failure -- a missing anchor
    must stay LOUD (FU-035), never degrade to an empty list."""
    key = key or api_key()
    req = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + key,
                      "Accept": "application/json",
                      "User-Agent": "zo-sentinel-vast-spend/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            rows = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:                     # noqa: BLE001 -- fail loud
        raise VastSpendError(
            "account invoices fallback unreachable ({}: {}) -- refusing to "
            "report $0.00 (FU-035)".format(exc.__class__.__name__, exc)) from exc
    payload = dict(payload)
    payload["invoices"] = list(payload.get("invoices") or []) + \
        _map_account_rows(rows)
    payload["_payments_source"] = url
    return payload


def last_topup(invoices: List[dict]) -> Optional[dict]:
    """The most recent SETTLED payment (credit) invoice, or None.

    Payments carry a negative amount on this API. Failed and refunded rows
    are excluded -- a refunded top-up never funded anything.
    """
    pays = [i for i in (invoices or [])
            if isinstance(i, dict) and i.get("type") == "payment"
            and not i.get("failed") and not i.get("refunded")]
    if not pays:
        return None
    return max(pays, key=lambda i: float(i.get("paid_timestamp")
                                         or i.get("timestamp") or 0.0))


def spend_since_topup(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """{"topup", "credit", "spent", "since_ts", "source", "alarm", ...}.

    spent = |last top-up| - current credit. No local state, and no month
    boundary invented on our side: the top-up IS the natural epoch and the
    only anchor Vast itself gives us.
    """
    payload = fetch_invoices() if payload is None else payload
    credit_raw = (payload.get("current") or {}).get("credit")
    if credit_raw is None:
        raise VastSpendError(
            "vast invoices payload carries no current.credit -- cannot "
            "compute spend, and will not guess (FU-035)")
    credit = float(credit_raw)
    pay = last_topup(payload.get("invoices") or [])
    if pay is None:
        raise VastSpendError(
            "no settled payment invoice on the account -- no anchor for "
            "spend-since-top-up (FU-035)")
    topup = abs(float(pay.get("amount") or 0.0))
    if topup <= 0.0:
        raise VastSpendError(
            "last payment invoice has amount {!r} -- unusable as an "
            "anchor".format(pay.get("amount")))
    spent = round(topup - credit, 2)
    return {"topup": round(topup, 2),
            "credit": round(credit, 2),
            "spent": spent,
            "since_ts": float(pay.get("paid_timestamp")
                              or pay.get("timestamp") or 0.0),
            "invoice_id": pay.get("id"),
            "monthly_budget": MONTHLY_BUDGET_USD,
            "alert_at": ALERT_AT_USD,
            "alarm": spent >= ALERT_AT_USD,
            "source": "invoices_api"}


def remaining_credit(payload: Optional[Dict[str, Any]] = None) -> float:
    """Credit left on the account, straight from Vast. Raises on failure."""
    payload = fetch_invoices() if payload is None else payload
    credit = (payload.get("current") or {}).get("credit")
    if credit is None:
        raise VastSpendError(
            "vast invoices payload carries no current.credit (FU-035)")
    return float(credit)


def format_line(rep: Dict[str, Any]) -> str:
    return ("vast spend since top-up: ${:.2f} of ${:.2f} (credit ${:.2f} "
            "remaining, alert at ${:.2f}){}".format(
                rep["spent"], rep["topup"], rep["credit"], rep["alert_at"],
                "  ** ALARM **" if rep["alarm"] else ""))


# --------------------------------------------------------------------------
# BURN RATE -- the reading that was missing on 2026-09-01
# --------------------------------------------------------------------------
# `spend_since_topup` answers "how much has gone?". Nothing answered "how fast
# is it going?", and the two are not the same guard. Measured 2026-09-01 by
# moat-rescore-weekly: spend read $13.21, which is comfortably under the $20
# alert, while a single live instance was drawing $0.1756/hr -- $4.21/day. The
# alert threshold was ~1.6 days away and no value anywhere in this fleet could
# have said so. A level-only budget guard fires AFTER the money is gone, which
# is the same complaint FU-035 makes about the delta method, one derivative up.
#
# This is a READING, not a gate. It does not halt a wave, destroy an instance
# or veto a launch: R7 prefers recovery over restriction, and an instance that
# looks expensive to this function may be a sibling lane's sanctioned job --
# on 2026-09-01 it was exactly that (`enrichment-ab-v1`, an SFT lane, correctly
# left alone). Deciding to stop someone else's job is never this module's call.
#
# R6 THROUGHOUT. An unreachable instances API raises; it does not report a burn
# of zero. A burn of zero is only ever published when the live API answered and
# the answer was "nothing is running", and the basis says which of the two it is.

def fetch_instances(key: Optional[str] = None, url: str = INSTANCES_URL,
                    timeout: int = TIMEOUT_S) -> List[dict]:
    """Live instances. Raises VastSpendError on ANY failure -- an unreadable
    fleet is UNKNOWN, and unknown is not an empty fleet."""
    key = key or api_key()
    req = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + key,
                      "Accept": "application/json",
                      "User-Agent": "zo-sentinel-vast-spend/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:                     # noqa: BLE001 -- fail loud
        raise VastSpendError(
            "vast instances API unreachable ({}: {}) -- refusing to report a "
            "burn of $0.00/hr (FU-035, R6)".format(
                exc.__class__.__name__, exc)) from exc
    rows = payload.get("instances") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise VastSpendError(
            "vast instances payload is not a list; got {}".format(
                type(rows).__name__))
    return rows


LIVE_STATES = {"running", "loading", "created", "starting"}


def burn_rate(spend: Dict[str, Any], instances: List[dict]) -> Dict[str, Any]:
    """How fast is the account draining, and when does that hit the alert?

    `instances` is the LIVE API's answer -- the ledger is not evidence here
    (the vast ledger split-brain settled that). A stopped or exited instance
    draws nothing but may still appear in the list, so state is filtered rather
    than assumed.
    """
    live = [i for i in instances
            if isinstance(i, dict)
            and str(i.get("actual_status") or i.get("cur_state") or "").lower()
            in LIVE_STATES]
    dph = round(sum(float(i.get("dph_total") or 0.0) for i in live), 6)
    spent = float(spend["spent"])
    alert_at = float(spend["alert_at"])
    credit = float(spend["credit"])

    def hours(headroom: float) -> Optional[float]:
        if dph <= 0:
            return None                      # not "infinity"; nothing is drawing
        return round(max(0.0, headroom) / dph, 2)

    h_alert = hours(alert_at - spent)
    h_credit = hours(credit)
    if spent >= alert_at:
        verdict = "already_over_alert"
    elif dph <= 0:
        verdict = "idle"
    elif h_alert is not None and h_alert <= 24:
        verdict = "alert_within_24h"
    else:
        verdict = "ok"
    return {
        "dph_total": dph,
        "daily_usd": round(dph * 24, 4),
        "live_instances": len(live),
        "instance_ids": sorted(i.get("id") for i in live if i.get("id")),
        "labels": sorted({str(i.get("label") or "unlabelled") for i in live}),
        "hours_to_alert": h_alert,
        "hours_to_credit_exhausted": h_credit,
        "verdict": verdict,
        # R5: the basis travels with the number, and it distinguishes a MEASURED
        # zero from the absence of a measurement.
        "basis": "sum(dph_total) over instances the LIVE api reports in {} "
                 "({} of {} rows); headroom = alert_at - spent".format(
                     sorted(LIVE_STATES), len(live), len(instances)),
    }


def format_burn(b: Dict[str, Any]) -> str:
    if b["verdict"] == "idle":
        return ("vast burn: $0.00/hr -- the live API reports nothing running. "
                "This is a MEASURED zero, not an unread one.")
    tail = ""
    if b["hours_to_alert"] is not None:
        tail = "; alert threshold in {:.1f}h, credit gone in {:.1f}h".format(
            b["hours_to_alert"], b["hours_to_credit_exhausted"])
    return ("vast burn: ${:.4f}/hr = ${:.2f}/day across {} live instance(s) "
            "{}{}{}".format(b["dph_total"], b["daily_usd"], b["live_instances"],
                            b["labels"], tail,
                            "  ** ALERT WITHIN 24h **"
                            if b["verdict"] == "alert_within_24h" else ""))


# --------------------------------------------------------------------------
# self-test: pure, offline, against the REAL payload observed 2026-07-26
# --------------------------------------------------------------------------
LIVE_FIXTURE_20260726: Dict[str, Any] = {
    "invoices": [{"id": 3148330, "type": "payment", "service": "stripe_payments",
                  "is_credit": True, "timestamp": 1784296988.9266248,
                  "paid_timestamp": 1784296989.2385857, "amount": -25.0,
                  "failed": None, "refunded": None}],
    "current": {"charges": 0, "service_fee": 0, "total": 0,
                "credit": 18.784030982420944},
}


def selftest() -> None:
    rep = spend_since_topup(LIVE_FIXTURE_20260726)
    # the exact figure the delta method reported as $0.00 for the same period
    assert rep["spent"] == 6.22, rep
    assert rep["topup"] == 25.00 and rep["credit"] == 18.78, rep
    assert rep["source"] == "invoices_api", rep
    assert rep["alarm"] is False, rep
    assert remaining_credit(LIVE_FIXTURE_20260726) == 18.784030982420944

    # the alarm CAN fire -- FU-035 is that it structurally could not
    hot = spend_since_topup({"invoices": LIVE_FIXTURE_20260726["invoices"],
                             "current": {"credit": 3.10}})
    assert hot["spent"] == 21.90 and hot["alarm"] is True, hot

    # every failure path is LOUD, never a silent zero ------------------------
    for bad, why in [({}, "no current block"),
                     ({"current": {}}, "no credit"),
                     ({"current": {"credit": 5.0}, "invoices": []}, "no payment"),
                     ({"current": {"credit": 5.0},
                       "invoices": [{"type": "payment", "amount": -25.0,
                                     "refunded": True}]}, "refunded top-up"),
                     ({"current": {"credit": 5.0},
                       "invoices": [{"type": "payment", "amount": 0}]},
                      "zero-amount anchor")]:
        raised = False
        try:
            spend_since_topup(bad)
        except VastSpendError:
            raised = True
        assert raised, "returned a figure instead of raising on: " + why

    # network failure must raise, not degrade to 0.00
    raised = False
    try:
        fetch_invoices(key="x", url="http://127.0.0.1:9/invoices", timeout=2)
    except VastSpendError as exc:
        raised = "refusing to report" in str(exc)
    assert raised, "fetch_invoices swallowed a network failure"

    # failed payments are not anchors; the newest settled payment wins
    two = {"current": {"credit": 1.0},
           "invoices": [{"type": "payment", "amount": -25.0, "timestamp": 100},
                        {"type": "payment", "amount": -10.0, "timestamp": 200},
                        {"type": "payment", "amount": -99.0, "timestamp": 300,
                         "failed": True},
                        {"type": "charge", "amount": 4.0, "timestamp": 400}]}
    assert last_topup(two["invoices"])["amount"] == -10.0
    assert spend_since_topup(two)["spent"] == 9.0

    # API drift 2026-08-30: payments live at /invoices/ as amount_cents rows
    acct = [{"id": 2831506, "when": 1778883474.18, "paid_on": 1778883474.57,
             "amount_cents": -2500, "is_credit": True},          # the real top-up
            {"id": 1, "when": 50.0, "paid_on": None,
             "amount_cents": -999, "is_credit": True},           # unsettled
            {"id": 2, "when": 60.0, "paid_on": 61.0,
             "amount_cents": 400, "is_credit": False}]           # a charge
    mapped = _map_account_rows(acct)
    assert len(mapped) == 1 and mapped[0]["amount"] == -25.0, mapped
    rep3 = spend_since_topup({"current": {"credit": 13.504}, "invoices": mapped})
    assert rep3["spent"] == 11.5 and rep3["topup"] == 25.0, rep3
    assert _map_account_rows("not-a-list") == []

    # ---- burn rate -------------------------------------------------------
    # The live 2026-09-01 reading: one sanctioned sibling-lane instance at
    # $0.1756/hr against $13.21 spent. The level guard was quiet; the rate was
    # 1.6 days from the alert.
    live_0901 = [{"id": 49452453, "actual_status": "running",
                  "label": "enrichment-ab-v1", "dph_total": 0.17555555555555558}]
    b = burn_rate({"spent": 13.21, "alert_at": 20.0, "credit": 11.79}, live_0901)
    assert b["live_instances"] == 1 and b["dph_total"] == 0.175556, b
    assert abs(b["daily_usd"] - 4.2133) < 0.001, b
    assert abs(b["hours_to_alert"] - 38.68) < 0.05, b
    assert b["verdict"] == "ok", b

    # NEGATIVE CONTROL. The same call must be able to say the opposite, or the
    # verdict is decoration: at $2/hr the alert is 3.4h away, not 38.
    hot = burn_rate({"spent": 13.21, "alert_at": 20.0, "credit": 11.79},
                    [{"id": 1, "actual_status": "running", "dph_total": 2.0}])
    assert hot["verdict"] == "alert_within_24h", hot
    assert abs(hot["hours_to_alert"] - 3.4) < 0.01, hot

    # A MEASURED zero is allowed and is labelled as one.
    idle = burn_rate({"spent": 13.21, "alert_at": 20.0, "credit": 11.79}, [])
    assert idle["verdict"] == "idle" and idle["dph_total"] == 0.0, idle
    assert idle["hours_to_alert"] is None, "0/0 must not read as 'infinite time'"
    assert "MEASURED zero" in format_burn(idle)

    # Exited instances are still in the list and draw nothing.
    dead = burn_rate({"spent": 1.0, "alert_at": 20.0, "credit": 24.0},
                     [{"id": 2, "actual_status": "exited", "dph_total": 0.9}])
    assert dead["verdict"] == "idle" and dead["live_instances"] == 0, dead

    # Past the alert, the verdict says so regardless of rate.
    over = burn_rate({"spent": 21.0, "alert_at": 20.0, "credit": 4.0},
                     [{"id": 3, "actual_status": "running", "dph_total": 0.5}])
    assert over["verdict"] == "already_over_alert", over
    assert over["hours_to_alert"] == 0.0, over

    # R6: an unreadable API raises rather than reporting an idle fleet.
    raised = False
    try:
        fetch_instances(key="x", url="http://127.0.0.1:9/instances", timeout=2)
    except VastSpendError as exc:
        raised = "refusing to report a burn" in str(exc)
    assert raised, "fetch_instances swallowed a network failure"

    print("PASS vast_spend self-tests (live 2026-07-26 payload => $6.22 spent, "
          "where the delta method reported $0.00; every failure path raises)")


def main(argv: List[str]) -> int:
    mode = argv[0] if argv else "all"
    if mode in ("selftest", "test", "all"):
        selftest()
        if mode != "all":
            return 0
    if mode == "burn":
        try:
            rep = spend_since_topup()
            b = burn_rate(rep, fetch_instances())
        except VastSpendError as exc:
            print("VAST BURN UNKNOWN -- {}".format(exc), file=sys.stderr)
            return 2
        print(json.dumps({"spend": rep, "burn": b}, indent=2))
        print(format_line(rep))
        print(format_burn(b))
        return 0
    try:
        rep = spend_since_topup()
    except VastSpendError as exc:
        print("VAST SPEND UNKNOWN -- {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=2))
    print(format_line(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
