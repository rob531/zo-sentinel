# zo-sentinel

ZO-SENTINEL MCP trust intelligence UI + directive system.

The recovered + upgraded UI server that powers the ZO-SENTINEL dashboard.
Talks to the write/query service on `127.0.0.1:8772` for all DB access.

## Services in this repo

| Component              | Port | Purpose                                       |
|------------------------|------|-----------------------------------------------|
| `ui_server.py`         | 8790 | Rich MCP trust intelligence dashboard         |
| `approval_workflow.py` | 8780 | InfoSec MCP submission/decision API           |

The `templates/` folder ships a few legacy HTML templates that the older
`approval_workflow` and a status dashboard reference. They are kept
verbatim from the previous build for parity; `ui_server.py` itself
inlines its own CSS/JS rather than rendering Jinja templates.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Foreground:
python ui_server.py                # http://127.0.0.1:8790
python approval_workflow.py        # http://127.0.0.1:8780
```

Both services expect a write/query service on `127.0.0.1:8772`. With it
absent, the UI still boots — endpoints will return empty result sets
rather than crashing (see the catch-all middleware in `ui_server.py`).

## Health checks

* `GET /healthz` (ui_server)
* `GET /health`  (approval_workflow)

## Documentation

* `docs/ARCHITECTURE.md` — system topology, port registry, signal
  contract, append-only immutability rules, SLA windows, scope
  boundary.
* `docs/INCIDENT_2026-05-09.md` — postmortem of the directive-builder
  incident that overwrote the rich UI with a stub. Read this before
  ever wiring the directive builder back up to write production files.
