# SOC2 Ticket Integrity Inspector — Product Specification
## Version 0.1 | 2026-04-23

---

## 1. The Problem

Organisations under SOC2 Type 2 with continuous monitoring generate a
constant stream of evidence collection tickets. Over months these accumulate:

- **Duplicates** — same control mapped to multiple live tickets
- **Orphans** — ticket references a control that was retired, renamed, or
  moved to a different framework section
- **Gaps** — controls that have no evidence ticket at all (invisible to auditor)
- **Stale** — tickets open past their evidence collection window with no activity
- **Misclassified** — ticket text doesn't match the control it's assigned to
- **Undated** — evidence collected but no collection date, unusable at audit
- **Scope drift** — control scope changed but ticket description wasn't updated

At audit time the InfoSec team spends days manually reconciling this before
they can present clean evidence. That is the problem this tool eliminates.

---

## 2. What It Is

An autonomous agent that connects to your ticketing system, understands your
SOC2 control framework, and produces a clean reconciliation report showing
exactly what needs fixing before the auditor arrives. Optionally: executes
the fixes automatically with human approval gates.

Target user: InfoSec/GRC teams running continuous monitoring programs.
Target moment: 30-60 days before SOC2 Type 2 audit period closes.

---

## 3. Core Concepts

### Control Catalogue
The canonical list of SOC2 controls in scope. Source of truth.
Can be loaded from: CSV, JSON, direct SOC2 CC/A/C/P/PI section mapping,
or connected to a GRC platform (Vanta, Drata, Secureframe).

### Evidence Ticket
A ticket in Jira/ServiceNow/Linear/GitHub Issues that represents a unit
of control evidence collection. Has: control ID, collection period,
assigned owner, status, evidence attachments.

### Reconciliation
The mapping of every ticket → control, and every control → tickets.
A clean reconciliation has: 1 active ticket per control, no orphans,
no gaps, all tickets dated within the audit window.

---

## 4. Integrity Checks

### Check 1: Coverage (Gaps)
For every control in scope: is there at least one active, non-stale ticket?
Output: list of controls with no coverage.

### Check 2: Duplication
For every control: are there multiple active tickets?
Output: duplicate sets with recommendation (keep newest, close others).

### Check 3: Orphan Detection
For every ticket: does its control ID exist in the current catalogue?
Output: orphaned tickets with suggested re-mapping or closure.

### Check 4: Staleness
For every active ticket: was it updated within the evidence collection
window (configurable, default 30 days)?
Output: stale tickets with last-activity date and owner.

### Check 5: Semantic Alignment
Using LLM: does the ticket description actually match the control it's
assigned to? Catches manual entry errors.
Output: misaligned tickets with confidence score and suggested correction.

### Check 6: Evidence Completeness
For every closed ticket marked as "evidence collected": is there an
attachment, link, or description that would satisfy an auditor?
Output: tickets claiming completion but lacking substantive evidence.

### Check 7: Period Validity
Are evidence collection dates within the SOC2 audit period?
Evidence from outside the period window is inadmissible.
Output: tickets with out-of-window evidence dates.

### Check 8: Owner Assignment
Are all active tickets assigned to a live team member?
Output: unassigned or assigned-to-departed-employee tickets.

---

## 5. Output

### Reconciliation Report (Markdown + JSON)

```
SOC2 TICKET INTEGRITY REPORT
Generated: 2026-04-23  Audit Window: 2025-10-01 to 2026-03-31
Control Framework: SOC2 CC Series (Trust Services Criteria)

SUMMARY
  Controls in scope:        47
  Controls with coverage:   41  (87%)
  GAPS (no ticket):          6  -- ACTION REQUIRED
  Duplicates:                8  -- review recommended
  Orphaned tickets:          3  -- close or remap
  Stale tickets:            12  -- needs owner action
  Misaligned (LLM check):    4  -- verify manually
  Out-of-window evidence:    2  -- may be inadmissible

ACTION PRIORITY
  P1 (before audit):  Gaps, Orphans, Out-of-window
  P2 (this sprint):   Duplicates, Stale
  P3 (housekeeping):  Misaligned, Missing evidence

[detailed sections follow...]
```

### Machine-readable output (JSON)
For integration with GRC platforms, Slack alerts, or dashboard display.

### Proposed Actions File
Optional: auto-generated ticket updates ready for bulk import.
Human reviews before any writes back to ticketing system.

---

## 6. Integrations

### Ticketing (Phase 1 targets)
- Jira Cloud (REST API, OAuth2)
- ServiceNow (REST API, basic auth or OAuth)
- Linear (GraphQL API)
- GitHub Issues (REST API)

### GRC Platforms (Phase 2 targets)
- Vanta (control catalogue sync)
- Drata (control catalogue sync)
- Secureframe (control catalogue sync)
- Tugboat Logic

### Framework Support
- SOC2 Type 2 (CC, A, C, P, PI series) — primary
- ISO 27001 Annex A — secondary
- NIST CSF — tertiary
- Custom framework via CSV upload

---

## 7. Architecture

```
ticketing_connector.py   -- adapter per ticketing platform
control_catalogue.py     -- loads and normalises control framework
reconciliation_engine.py -- runs all 8 checks, produces findings
semantic_checker.py      -- LLM-powered alignment check (uses escalation.py)
report_generator.py      -- Markdown + JSON output
action_proposer.py       -- generates proposed ticket updates (gated)
approval_gate.py         -- human review before writes
```

Stateless by design: reads from ticketing system, writes a report, exits.
Optional persistent mode: runs on a schedule and diffs findings over time
("3 new gaps appeared since last run on Monday").

---

## 8. Key Design Decisions

**No direct ticket writes without approval gate.**
The tool is an inspector first. Write-back is an optional mode that requires
explicit confirmation. Auditors need to trust your evidence provenance.

**LLM for semantic checks only.**
The structural checks (gaps, duplicates, orphans, staleness) are deterministic
SQL/API queries. LLM is only used for the semantic alignment check where
judgment is genuinely needed. This keeps the report auditable and reproducible.

**Control catalogue is the source of truth.**
The tool doesn't infer what controls should exist. It maps against a defined
catalogue. If the catalogue is wrong, fix the catalogue first.

**Framework-agnostic core.**
The reconciliation engine knows nothing about SOC2 specifically. It operates
on (control_id, ticket_id, dates, status) tuples. The framework mapping is
a configuration layer on top.

---

## 9. Differentiation

Existing tools (Vanta, Drata) do continuous monitoring but focus on
evidence *collection* (automated tests, integrations). They don't solve
the *ticket hygiene* problem that accumulates when teams manage evidence
through tickets manually or semi-manually alongside the automated layer.

This tool is complementary, not competitive: it cleans up the manual
evidence ticket layer that exists in every real program regardless of
how much automation is in place.

The CISO perspective is the differentiator: built by someone who knows
what an auditor actually scrutinises, not by engineers guessing.

---

## 10. Build Order

1. `control_catalogue.py` — load SOC2 CC controls from CSV/JSON
2. `ticketing_connector.py` — Jira adapter first (most common)
3. `reconciliation_engine.py` — checks 1-4 (structural, no LLM)
4. `report_generator.py` — Markdown output
5. `semantic_checker.py` — checks 5-6 (LLM via escalation.py)
6. `action_proposer.py` + `approval_gate.py` — optional write-back
7. ServiceNow adapter
8. GRC platform sync

Builder-builder anchor: `soc2_agent/` — first non-Sentinel project
to validate the anchor system works end-to-end.