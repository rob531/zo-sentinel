-- =====================================================================
-- builder.duckdb.sql  --  ZO SENTINEL BUILDER SCHEMA  (OWNER: the builder)
-- =====================================================================
-- State for the self-build feedback loop -- NOT product data. No API
-- endpoint and no UI reads these rows; only the directive generator,
-- goose_runner, the build-routing/escalation ladder, and the key-hydration
-- ops tooling touch them. Workload is OLAP: append-mostly, single-process,
-- read via analytical aggregation.
--
-- TARGET ENGINE: DuckDB -- and it STAYS DuckDB. This is the right engine
-- for an append-heavy, single-writer, aggregate-read workload. Do NOT port
-- these to Postgres in the app migration (Phase C); the whole point of the
-- split is that the builder keeps DuckDB while the app moves off it.
--
-- Access via the write_service bus (/write, /execute, /query) -- never a
-- direct duckdb import (CLAUDE.md). Source of truth extracted from
-- full_schema_bootstrap.py (Phase A split).
-- =====================================================================

-- ---------------------------------------------------------------------
-- build_provenance -- one row per build ATTEMPT (success + ghost both
-- recorded). The Phase 4 instrumentation feeds this from goose_runner;
-- the failure_matrix view and the build_success_stats MCP tool read it so
-- the architect/router can see which rung succeeds for which kind of work.
-- Idempotent writes: deterministic build_id + INSERT OR IGNORE.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS build_provenance (
    build_id       VARCHAR PRIMARY KEY DEFAULT gen_random_uuid()::VARCHAR,
    directive_id   VARCHAR,
    directive_type VARCHAR,
    complexity     VARCHAR,
    engine         VARCHAR,
    model          VARCHAR,
    backend        VARCHAR,
    smoke_result   VARCHAR,
    rescue_count   INTEGER DEFAULT 0,
    success        BOOLEAN,
    output_path    VARCHAR,
    output_bytes   INTEGER,
    error          TEXT,
    built_at       TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------
-- key_topology -- the secret-HYDRATION dependency map. NAMES + topology
-- ONLY; secret VALUES are NEVER stored here. One row per (key, consumer)
-- so a single key with two delivery paths (e.g. ANTHROPIC via secretless-ai
-- [works] vs via key_hydrator [broken]) is explicit. Turns "which rung is
-- down and why" from a multi-log trace into one query. Hand-maintained:
-- graphify can't derive subprocess/cross-host/config edges, so update this
-- when the hydration path changes.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS key_topology (
    key_name    VARCHAR,      -- env var NAME only -- NEVER the secret value
    consumer    VARCHAR,      -- daemon/service that needs the key
    ladder_rung VARCHAR,      -- escalation alias/rung this key enables (or '')
    source      VARCHAR,      -- origin: tower_vault | zo_secrets | zo_env | modal
    delivery    VARCHAR,      -- mechanism: secretless_ai | boot_zo_env |
                              --            key_hydrator_ondemand | key_dispatch
    status      VARCHAR,      -- working | degraded | broken
    note        VARCHAR,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (key_name, consumer)
);

-- ---------------------------------------------------------------------
-- VIEWS (read models). DuckDB-specific aggregates -- another reason these
-- stay on DuckDB: `arg_max(...) FILTER (...)` has no direct Postgres
-- equivalent (PG would need DISTINCT ON or a lateral subquery).
-- ---------------------------------------------------------------------

-- failure_matrix -- Phase 4 read model over build_provenance, aggregated by
-- (directive_type, complexity, model) so the router can see which rung
-- succeeds for which kind of work. Always live (it's a VIEW).
CREATE OR REPLACE VIEW failure_matrix AS
SELECT
    directive_type,
    complexity,
    model,
    COUNT(*)                                                   AS attempts,
    SUM(CASE WHEN success THEN 1 ELSE 0 END)                   AS successes,
    ROUND(100.0 * AVG(CASE WHEN success THEN 1 ELSE 0 END), 1) AS success_pct,
    ROUND(AVG(rescue_count), 2)                                AS avg_rescues,
    MAX(built_at)                                              AS last_seen,
    arg_max(error, built_at) FILTER (WHERE NOT success)        AS last_error
FROM build_provenance
GROUP BY directive_type, complexity, model;

-- key_chain_status -- surfaces BROKEN/degraded hydration chains first. Pairs
-- each (key, consumer) with whether a WORKING delivery exists for that key on
-- any consumer (key_has_working_path) -- "wrong path" vs "key unavailable".
CREATE OR REPLACE VIEW key_chain_status AS
SELECT t.key_name, t.consumer, t.ladder_rung, t.source, t.delivery, t.status,
       EXISTS (SELECT 1 FROM key_topology w
               WHERE w.key_name = t.key_name AND w.status = 'working') AS key_has_working_path,
       t.note
FROM key_topology t
ORDER BY CASE t.status WHEN 'broken' THEN 0 WHEN 'degraded' THEN 1 ELSE 2 END, t.key_name;

-- ---------------------------------------------------------------------
-- build_churn_daily -- the CONVERGENCE LEADING INDICATOR. Per day, what
-- share of produced files were CHURN (re-deriving / re-wiring something
-- that already exists) vs NET-NEW capability. A build counts as churn if:
--   * its output_path was first produced on an EARLIER day (path rework), OR
--   * the filename is a version bump   (_v2/_v3...)                       OR
--   * the filename is glue/rework      (wiring|integration|fix|verify|_test...)
-- Falling churn_pct  => the loop is CONVERGING (closing capabilities).
-- Rising  churn_pct  => PLATEAU risk (re-attacking, not finishing).
-- Ghost builds (no output_path) are excluded from the ratio; success_pct
-- and avg_rescues are over produced files only.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW build_churn_daily AS
WITH produced AS (
    SELECT
        build_id,
        built_at::DATE AS day,
        success,
        rescue_count,
        output_path,
        regexp_matches(output_path, '_v[0-9]')                                                          AS is_versioned,
        regexp_matches(output_path, '(wiring|integration|completion|complete|fix|patch|diagnose|verify|_test)') AS is_glue
    FROM build_provenance
    WHERE COALESCE(output_path, '') <> ''
),
first_day AS (
    SELECT output_path, MIN(day) AS first_day FROM produced GROUP BY output_path
),
churned AS (
    SELECT p.*,
           (fd.first_day < p.day) AS is_path_rework,
           ((fd.first_day < p.day) OR p.is_versioned OR p.is_glue) AS is_churn
    FROM produced p
    JOIN first_day fd ON fd.output_path = p.output_path
)
SELECT
    day,
    COUNT(*)                                       AS produced_files,
    SUM(is_churn::INT)                             AS churn_files,
    SUM((NOT is_churn)::INT)                       AS net_new_files,
    ROUND(100.0 * AVG(is_churn::INT), 1)           AS churn_pct,
    ROUND(100.0 * AVG(success::INT), 1)            AS success_pct,
    ROUND(AVG(rescue_count), 2)                    AS avg_rescues,
    SUM(is_path_rework::INT)                       AS path_rework,
    SUM(is_versioned::INT)                         AS versioned,
    SUM(is_glue::INT)                              AS glue
FROM churned
GROUP BY day
ORDER BY day DESC;

-- build_churn_trend -- the single-row VERDICT over build_churn_daily: this
-- week's churn vs last week's, and a regime label the daemon/dashboard can
-- read directly. This is the row to watch: regime='CONVERGING' means MVP is
-- approaching in cycles; 'PLATEAU-RISK' means more cycles will NOT close it
-- (add a capability-close gate instead).
CREATE OR REPLACE VIEW build_churn_trend AS
SELECT
    cur.churn_pct_7d,
    prev.churn_pct_7d AS churn_pct_prev_7d,
    ROUND(cur.churn_pct_7d - prev.churn_pct_7d, 1) AS delta_pts,
    CASE
        WHEN cur.churn_pct_7d IS NULL                                        THEN 'NO-DATA'
        WHEN prev.churn_pct_7d IS NULL                                       THEN 'BASELINE'
        WHEN cur.churn_pct_7d - prev.churn_pct_7d <= -3                      THEN 'CONVERGING'
        WHEN cur.churn_pct_7d - prev.churn_pct_7d >=  3                      THEN 'PLATEAU-RISK'
        ELSE 'FLAT'
    END AS regime
FROM
    (SELECT ROUND(100.0 * SUM(churn_files) / NULLIF(SUM(produced_files), 0), 1) AS churn_pct_7d
     FROM build_churn_daily WHERE day >  CURRENT_DATE - INTERVAL 7 DAY) cur,
    (SELECT ROUND(100.0 * SUM(churn_files) / NULLIF(SUM(produced_files), 0), 1) AS churn_pct_7d
     FROM build_churn_daily WHERE day >  CURRENT_DATE - INTERVAL 14 DAY
                              AND day <= CURRENT_DATE - INTERVAL 7 DAY) prev;
