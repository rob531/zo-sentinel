# referent-verify arming probe (a) -- NO-OP

Temporary. Opened 2026-08-27 to prove probe (a) of the three-probe arming
pattern for #4080: a docs-only pull request must report the `referent-verify`
context in SECONDS and must NOT hang.

That is the probe everybody omits, because "nothing changed" sounds like
nothing to test. It is the one that catches the trap #4089 documented: a
workflow skipped by a `paths:` filter reports NO context at all, and branch
protection waits on it forever. The docs-only PR is precisely the class that
would hang, and precisely the class that started #4032.

This file is deleted with its branch as soon as the probe has reported.
