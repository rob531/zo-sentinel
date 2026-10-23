"""The lost-update race, reproduced and then closed.

test_lost_update_reproduced_without_guard is the negative control: it
demonstrates the bug the rest of this module prevents. If it ever stops
failing-without-the-guard, the guard is no longer being tested.
"""
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fu_lock  # noqa: E402


@pytest.fixture
def ledger(tmp_path):
    p = tmp_path / "FOLLOWUPS.md"
    p.write_text("line0\nline1\nline2\n", encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------
# Negative control: the naive pattern loses data.
# --------------------------------------------------------------------------
def test_lost_update_reproduced_without_guard(ledger):
    """Naive read -> mutate -> write. A's edit is silently destroyed."""
    a = open(ledger, encoding="utf-8").read()          # A reads
    b = open(ledger, encoding="utf-8").read()          # B reads
    open(ledger, "w", encoding="utf-8").write(a + "A-was-here\n")
    open(ledger, "w", encoding="utf-8").write(b + "B-was-here\n")
    final = open(ledger, encoding="utf-8").read()
    assert "B-was-here" in final
    assert "A-was-here" not in final                   # <- the data loss


# --------------------------------------------------------------------------
# The guard refuses instead of clobbering.
# --------------------------------------------------------------------------
def test_txn_refuses_to_clobber_a_concurrent_edit(ledger):
    with pytest.raises(fu_lock.LedgerChanged):
        with fu_lock.ledger_txn(ledger) as txn:
            txn.lines.append("mine")
            # someone else (e.g. the Syncthing bridge) writes mid-transaction
            with open(ledger, "a", encoding="utf-8") as fh:
                fh.write("theirs\n")
    assert "theirs" in open(ledger, encoding="utf-8").read()
    assert "mine" not in open(ledger, encoding="utf-8").read()


def test_txn_commits_when_uncontended(ledger):
    with fu_lock.ledger_txn(ledger) as txn:
        txn.lines.append("appended")
    assert "appended" in open(ledger, encoding="utf-8").read()


def test_no_op_transaction_is_a_success(ledger):
    before = open(ledger, encoding="utf-8").read()
    # No `as txn`: this test asserts that entering and leaving the transaction
    # WITHOUT touching it leaves the ledger byte-identical.
    with fu_lock.ledger_txn(ledger):
        pass
    assert open(ledger, encoding="utf-8").read() == before


# --------------------------------------------------------------------------
# Mutual exclusion and liveness.
# --------------------------------------------------------------------------
def test_second_writer_waits_then_gets_the_lock(ledger):
    order = []

    def slow():
        with fu_lock.ledger_txn(ledger) as txn:
            order.append("A-in")
            time.sleep(1.0)
            txn.lines.append("A")
            order.append("A-out")

    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.2)
    with fu_lock.ledger_txn(ledger, timeout=10) as txn:
        order.append("B-in")
        txn.lines.append("B")
    t.join()
    assert order == ["A-in", "A-out", "B-in"], order


def test_busy_raises_rather_than_waiting_forever(ledger):
    lp = fu_lock.acquire(ledger)
    try:
        with pytest.raises(fu_lock.LedgerBusy):
            with fu_lock.ledger_txn(ledger, timeout=1):
                pass
    finally:
        fu_lock.release(lp)


def test_stale_lock_is_reclaimed(ledger):
    """A crashed holder must not wedge the ledger forever."""
    lp = ledger + ".lock"
    with open(lp, "w", encoding="utf-8") as fh:
        fh.write("999999,%f" % (time.time() - fu_lock.LOCK_TTL_S - 10))
    with fu_lock.ledger_txn(ledger, timeout=5) as txn:
        txn.lines.append("after-stale-reclaim")
    assert "after-stale-reclaim" in open(ledger, encoding="utf-8").read()


def test_lock_released_on_exception(ledger):
    with pytest.raises(ValueError):
        with fu_lock.ledger_txn(ledger):
            raise ValueError("boom")
    assert not os.path.exists(ledger + ".lock")
    with fu_lock.ledger_txn(ledger, timeout=2) as txn:
        txn.lines.append("still-usable")


# --------------------------------------------------------------------------
# Durability: a crash mid-write must not truncate a 740KB ledger.
# --------------------------------------------------------------------------
def test_write_is_atomic_no_partial_file(ledger, tmp_path):
    big = "\n".join("row%d" % i for i in range(50000))
    with fu_lock.ledger_txn(ledger) as txn:
        txn.lines[:] = big.split("\n")
    assert open(ledger, encoding="utf-8").read().count("\n") == 49999
    assert not [p for p in os.listdir(tmp_path) if ".tmp." in p]


def test_real_multiprocess_contention(ledger):
    """Two OS processes, both appending. Neither may vanish."""
    script = textwrap.dedent("""
        import sys, time
        sys.path.insert(0, %r)
        import fu_lock
        tag = sys.argv[2]
        for attempt in range(20):
            try:
                with fu_lock.ledger_txn(sys.argv[1], timeout=30) as txn:
                    time.sleep(0.05)
                    txn.lines.append(tag)
                break
            except fu_lock.LedgerChanged:
                time.sleep(0.1)
    """ % os.path.dirname(os.path.abspath(__file__)))
    sp = os.path.join(os.path.dirname(ledger), "w.py")
    open(sp, "w", encoding="utf-8").write(script)
    procs = [subprocess.Popen([sys.executable, sp, ledger, "P%d" % i])
             for i in range(4)]
    for p in procs:
        p.wait(timeout=90)
    final = open(ledger, encoding="utf-8").read()
    for i in range(4):
        assert "P%d" % i in final, "process %d's write was lost: %r" % (i, final)
