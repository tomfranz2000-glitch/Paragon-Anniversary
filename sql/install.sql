-- ============================================================================
-- Paragon Anniversary - Canonical Database Bootstrap
-- ============================================================================
-- Run this file from the repository root with the mysql or mariadb command-line
-- client. It is the single supported entrypoint for both fresh installations
-- and in-place upgrades:
--
--   mysql [connection options] < sql/install.sql
--
-- SOURCE paths are resolved from the client's working directory, hence the
-- repository-root requirement. The component migrations remain separate so
-- their responsibilities are reviewable, while this file fixes their order.
-- Every component is rerunnable. The final migration intentionally reapplies
-- the canonical Anniversary configuration and advances profession high-water
-- marks without granting retroactive XP.
-- ============================================================================

SOURCE sql/01_create_database.sql;
SOURCE sql/02_create_tables.sql;
SOURCE sql/03_create_triggers.sql;
SOURCE sql/04_insert_default_config.sql;
SOURCE sql/05_apply_anniversary_config.sql;
