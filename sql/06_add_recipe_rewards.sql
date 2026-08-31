-- ============================================================================
-- Paragon Anniversary - account-wide one-time profession recipe rewards
-- ============================================================================
-- Idempotent fresh-install/in-place migration.  Existing known recipes are
-- seeded by the Lua runtime on each character's first login for the generated
-- catalogue version; this migration intentionally performs no reconciliation
-- and grants no XP.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_recipe_reward_claim` (
    `account_id` INT UNSIGNED NOT NULL,
    `spell_id` INT UNSIGNED NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `claimed_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`account_id`, `spell_id`),
    KEY `ix_paragon_recipe_pending` (`account_id`, `pending_xp`)
) ENGINE=InnoDB;

-- Per-character seeding matters because profession recipes are not shared by
-- mod-collections: every alt must establish its pre-existing spellbook once.
-- catalog_version makes a future generated-catalog change seed-before-reward
-- again without retroactive XP.
CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_recipe_reward_seed` (
    `guid` INT UNSIGNED NOT NULL,
    `account_id` INT UNSIGNED NOT NULL,
    `catalog_version` INT UNSIGNED NOT NULL,
    `seeded_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (`guid`),
    KEY `ix_paragon_recipe_seed_account` (`account_id`)
) ENGINE=InnoDB;
