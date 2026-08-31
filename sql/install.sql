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
-- Every component is rerunnable. The canonical configuration and final mastery
-- expansion advance profession, weapon, and lockpicking high-water marks
-- without granting retroactive XP. Fist Weapons shares the Unarmed track.
-- ============================================================================

SOURCE sql/01_create_database.sql;
SOURCE sql/02_create_tables.sql;
SOURCE sql/03_create_triggers.sql;
SOURCE sql/04_insert_default_config.sql;
SOURCE sql/05_apply_anniversary_config.sql;
SOURCE sql/06_add_recipe_rewards.sql;
SOURCE sql/07_add_achievement_reward_claims.sql;
SOURCE sql/08_add_collection_pending_claims.sql;
SOURCE sql/09_add_reputation_and_account_collection_rewards.sql;
SOURCE sql/10_expand_skill_mastery_rewards.sql;
