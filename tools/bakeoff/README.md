# UI bake-off (rung quality via interactive treewalk)

Same directive -> every ladder rung builds a UI -> a real headless browser walks
the accessibility tree, **clicks every interactive node**, and records console
errors / JS exceptions / failed requests / DOM churn -> a deterministic 0-100
score. No model-as-judge: a model only authors the build; the verdict is the
treewalk's.

## Files
- `treewalk.py` -- the scorer. Drives chromium over one HTML file/URL, treewalks
  it, emits a JSON report + score. (Selenium-class interaction; deterministic verdict.)
- `build_via_rung.py` -- asks one rung (OpenAI-compat / Anthropic) to build the
  HTML from a directive. Self-sources `/root/.zo_secrets` (browser UA, like the shim).
- `run_bakeoff.py` -- loops rungs: build -> treewalk -> ranked comparison table.
- `directive.example.txt` -- representative Sentinel admin-UI directive (swap in any real one).
- `test_score.py` -- pure-function scorer tests (no browser; runs in CI).

## Run it

Multi-rung bake-off (tower or GH runner, where keys live):
```
python3 run_bakeoff.py --directive directive.example.txt \
    --rungs cerebras,groq,mistral,nvidia,gemini,anthropic
```

Score a single pre-built file (no keys):
```
python3 run_bakeoff.py --local path/to/app.html
```

As a goose recipe (per rung): set `GOOSE_MODEL=zo-ladder-<rung>` and run
`ui_bakeoff.recipe.yaml` with `directive_file` + `rung`.

In CI: `.github/workflows/ui-bakeoff.yml` installs `playwright --with-deps` and
runs the full bake-off with keys from GH secrets (workflow_dispatch).

## Score (deterministic)
Starts at 100. Penalties: doesn't load (->0), no interactive elements (-35),
clicks don't mutate the DOM i.e. dead UI (-20), unclean-click ratio (up to -25),
console errors (up to -20), JS exceptions (up to -24), 4xx/5xx (up to -20),
unnamed controls (up to -10).
