-- Runs once, on an empty data directory.
--
-- The application's migrations create their own schema; this file only
-- installs extensions, which need privileges the application role may not
-- have in a managed deployment.

-- gen_random_uuid() for UUID primary keys. Built in since PostgreSQL 13;
-- pgcrypto is installed as well so the migration's CREATE EXTENSION is a
-- no-op rather than a privilege error.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Case-insensitive, accent-aware collation for names written in Uzbek Latin,
-- Uzbek Cyrillic or Russian, so that searching for a supplier or patient is
-- not defeated by capitalisation.
CREATE COLLATION IF NOT EXISTS case_insensitive (
    provider = icu,
    locale = 'und-u-ks-level2',
    deterministic = false
);

-- Statement statistics, for the Phase 7 performance work.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
