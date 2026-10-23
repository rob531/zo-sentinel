# Chairman checkpoint — context capture

**Read this FIRST (layer L0 of `LOCO_CHAIRMAN.md` §9).** If the section below
the rule is empty, no work is in flight — proceed to `chairman/QUEUE.md`.
If it is non-empty, a prior session ran low on context mid-gap: re-run its
recorded commands to re-establish the referent (do NOT trust the numbers as
still true — GC-8 applies to your own past self), continue from "next
command", and EMPTY this section in the same change that moves the register
row.

Write a capture per `LOCO_CHAIRMAN.md` §10 when: a context-summarisation
notice appears, the token budget drops below ~10%, the item in hand outsizes
the remaining budget, or a remote session nears its end with unpushed state.
The capture is not real until committed AND pushed.

Template:

```
## CAPTURE <utc-iso>
- register row / queue entry:
- grade in hand -> grade targeted:
- interrogatives answered (number: answer):
- commands run verbatim + key numbers:
- next command (exact):
- UNVERIFIED: <beliefs not yet evidenced -- C0 material, do not launder>
```

---

<!-- live capture goes below this line; empty = nothing in flight -->

## CAPTURE 2026-10-02T03:20Z
- register row / queue entry: GR-11..GR-15 (RCA 2026-10 follow-ups + alerting, handoff `ops/HANDOFF_followups_and_alerting.md`, zo-fleet-tools#18)
- grade in hand -> grade targeted: GR-11/12/13 C2 -> C4 (arming is Robin's, on the tower/host); GR-14 C1 -> C3; GR-15 C0 (decision)
- interrogatives answered: what runs -- nothing new runs until armed (ZoAlertWatch not registered; supervise/restore not applied on the host; zo-fleet-tools#21 held); who decides -- Robin: (a) GR-15 hand-close attribution, (b) whether `daemon_restart_reload` covers unattended go.sh restore (`host_patches.apply` opt-in)
- commands run verbatim + key numbers: `python zo_alert_watch.py --self-test` (zo-fleet-tools) PASS; `python3 tools/supervise_go_sh.py --self-test` 16/16; `python3 tools/restore_host_patches.py --self-test` 17/17; `python host_verdict.py --self-test` 60/60; `python host_patches_tick.py --self-test` 14/14
- next command (exact): on the tower, `python "D:\zo\Zocomputer Agents\_tools\zo_alert_watch.py" --dry-run`, then register ZoAlertWatch (PowerShell in zo-fleet-tools#20 body); decide zo-fleet-tools#21 (merge / split)
- UNVERIFIED: the GraphQL ClosedEvent query and `gh api -X GET search/issues` forms were never executed from the cloud (`host_verdict.py --live-smoke` proves them on the tower); ZoChainTick's real Last Result codes (alert_registry `ok_results` assumes 0/10); `zmgo_globs` match the real zm-go outputs

