-- enrichment_evidence.sql
-- The Stage 2 gate. Run after enrichment_harness.py has produced data.
--
-- Per enrichment, reports:
--   distinct_vals_total  -- how many unique scores across all (run, server) pairs
--   distinct_fingerprints -- how many unique input fingerprints it saw
--   n_runs               -- how many harness runs contributed
--   stddev_all           -- score spread across all rows
--   min_per_run, max_per_run -- range per run (sensitivity indicator)
--
-- Verdict column:
--   REJECT: flat             -- distinct_vals = 1. Same score everywhere, useless.
--   REJECT: no input variety -- distinct_fingerprints = 1. Harness not varying inputs correctly,
--                               or enrichment memoises against something it shouldn't.
--   REJECT: ignores inputs   -- many fingerprints but few scores. Computing, but not from inputs.
--   WEAK: low discrimination -- some variety but scores < servers/3
--   CANDIDATE for integration -- genuine discrimination, input-sensitive
--
-- Usage via psql-like:
--   python3 -c "import duckdb,os; p='/home/workspace/zo_mesh/zomesh.db';
--               print(duckdb.connect(p,read_only=True).execute(
--                 open('/home/workspace/zo_sentinel/enrichment_evidence.sql').read()
--               ).fetchdf())"
--
-- Or via Claude's zo_db_query (paste the SELECT below).

SELECT
    enrichment_name,
    COUNT(DISTINCT server_id)         AS n_servers,
    COUNT(DISTINCT run_id)            AS n_runs,
    COUNT(*)                          AS n_rows,
    COUNT(DISTINCT score)             AS distinct_vals,
    COUNT(DISTINCT input_fingerprint) AS distinct_fingerprints,
    ROUND(MIN(score), 1)              AS lo,
    ROUND(MAX(score), 1)              AS hi,
    ROUND(STDDEV(score), 2)           AS stddev_all,
    CASE
        WHEN COUNT(DISTINCT score) = 1
            THEN 'REJECT: flat score across everything'
        WHEN COUNT(DISTINCT input_fingerprint) = 1
            THEN 'REJECT: no input variety (harness or enrichment bug)'
        WHEN COUNT(DISTINCT score) < COUNT(DISTINCT input_fingerprint) / 3
            THEN 'REJECT: ignores most inputs'
        WHEN COUNT(DISTINCT score) < COUNT(DISTINCT server_id) / 3
            THEN 'WEAK: low discrimination'
        ELSE 'CANDIDATE for integration'
    END AS verdict
FROM mcp_signal_enrichments
GROUP BY enrichment_name
ORDER BY distinct_vals DESC, enrichment_name;