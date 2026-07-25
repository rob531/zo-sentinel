# The Autopoietic Loop — naming doctrine (chairman, 2026-07-24)

The E2E ladder + architect-goose + builder-goose + gates assembly now has a name
that says what it is FOR, not what it is made of: **the Autopoietic Loop**.

## Why this word

Autopoiesis (Maturana & Varela): a system whose product is **itself** — it
continuously produces and maintains its own components, preserving its
organization while the parts turn over. That is precisely the bar for spineful
emission, and precisely what the Potemkin era failed: the old ladder was merely
*allopoietic* — it produced output (559 files) without producing or maintaining
the organization that makes output count (mounts, contracts, reachability). 76%
of the lines, 13% of the app, 2.9% yield: production without self-production.

**The test of autopoiesis at the point of emitting directives:** every directive
must be produced *from the system's own self-description*, and every build must
regenerate that self-description. Concretely, the loop closed today:

```
  self-description  ──►  emission           ──►  construction        ──►  self-renewal
  (what am I?)           (what to become?)       (becoming it)            (I am now this)

  app_surface KL          architect proposes     fan-out → engine         generate_spine
  reachability census     ONE build_service      writes each file;        re-emits the spine;
  services/active/        directive from the     casing-repair heals      KL + census update;
  redirects report card   gaps in ITSELF         emissions pre-test       report card grades
                                                                          the lesson
                └──────────────── the loop feeds itself ◄─────────────────┘
```

Self-maintenance is the other half: the ratchet (orphans may not grow), the
liveness contract (only what proves alive joins the body), the janitor (dead
matter is retired), the redirect-reject (the loop *teaches its own emitter* at
the moment of failure), the casing-repair (the loop *heals its own emissions*).
An organism, not a factory.

## Protean substrate

The model layer beneath the loop is **protean** — it holds no fixed shape and no
hard bindings. Rungs are aliases resolved by the ladder shim; providers come and
go (TOS drift, pricing, quality windows — MiniMax stays, others rotate) and the
loop absorbs that by re-pointing an alias, never by editing organizational code.
The organization persists; the matter flowing through it is interchangeable.
That is the autopoietic property restated at the substrate: **identity lives in
the loop, not in the components.**

## Naming conventions going forward

- The assembly (ladder + architect-goose + builder-goose + gates + promoter +
  spine generator) is referred to as **the Autopoietic Loop** in docs, briefings,
  and directives. Individual daemons/files keep their names — no rename churn,
  never break goose.
- The service unit + its contract remain the loop's **atomic unit of
  self-production**; `services/active/` is the loop's **body plan**.
- New harness tools take loop-relative names (emitter, membrane/gate, repair,
  report card) rather than vendor- or model-relative names.
- "Spineful emission" = an emission the loop can incorporate into itself:
  registered, contract-proven, mounted, censused. Anything else is allopoietic
  waste and the gates exist to refuse it.

*Companions: `SOA_ATOMIC_UNIT_IMPLEMENTATION.md` (mechanics), `ORPHAN_REVIEW_FABLE5.md`
(retiring the allopoietic backlog), `REACHABILITY_POSTMORTEM` (why the bar is
reachability), GOOSE_WATCH.md Harness-Engineering row (the doctrine's industry twin).*
