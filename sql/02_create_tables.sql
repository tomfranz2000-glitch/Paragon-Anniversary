-- ============================================================================
-- Paragon Anniversary - Complete Table Schema
-- ============================================================================
-- This is the single authoritative table-creation file for the Paragon system.
-- Run 01_create_database.sql first, then run this file before triggers, defaults,
-- or Lua scripts. Every statement is idempotent for existing installations.
-- ============================================================================

-- --------------------------------------------------------------------------
-- Configuration
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_config_category` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(50) NOT NULL,

    PRIMARY KEY (`id`)
);

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_config_statistic` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `category` INT NOT NULL DEFAULT 1,
    `type` ENUM('AURA','COMBAT_RATING','UNIT_MODS') NOT NULL DEFAULT 'AURA',
    -- Symbolic keys are resolved through paragon_constant.lua. VARCHAR keeps
    -- upgrades non-destructive for legacy/custom rows while the triggers in
    -- 03_create_triggers.sql enforce every supported type/value pairing.
    `type_value` VARCHAR(32) NOT NULL DEFAULT 'LOOT',
    `icon` VARCHAR(50) NOT NULL DEFAULT '0',
    `factor` INT NOT NULL DEFAULT 1,
    `limit` INT(3) NOT NULL DEFAULT 255,
    `application` INT NOT NULL DEFAULT 0,

    PRIMARY KEY (`id`),
    CONSTRAINT `fk_category`
        FOREIGN KEY (`category`)
        REFERENCES `acore_ale`.`paragon_config_category` (`id`)
            ON UPDATE CASCADE
            ON DELETE NO ACTION
);

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_config` (
    `field` VARCHAR(255) NOT NULL,
    `value` VARCHAR(255) NOT NULL,

    PRIMARY KEY (`field`)
);

-- --------------------------------------------------------------------------
-- Experience-source configuration
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_config_experience_creature` (
    `id` INT(11) NOT NULL,
    `experience` INT(11) NOT NULL DEFAULT 50,

    PRIMARY KEY (`id`)
);

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_config_experience_achievement` (
    `id` INT(11) NOT NULL,
    `experience` INT(11) NOT NULL DEFAULT 100,

    PRIMARY KEY (`id`)
);

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_config_experience_skill` (
    `id` INT(11) NOT NULL,
    `experience` INT(11) NOT NULL DEFAULT 5000,

    PRIMARY KEY (`id`)
);

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_config_experience_quest` (
    `id` INT(11) NOT NULL,
    `experience` INT(11) NOT NULL DEFAULT 75,

    PRIMARY KEY (`id`)
);

-- --------------------------------------------------------------------------
-- Progression
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `acore_ale`.`character_paragon` (
    `guid` INT(11) NOT NULL,
    `level` INT(11) NOT NULL DEFAULT 1,
    `experience` INT(11) NOT NULL DEFAULT 0,

    PRIMARY KEY (`guid`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`account_paragon` (
    `account_id` INT(11) NOT NULL,
    `level` INT(11) NOT NULL DEFAULT 1,
    `experience` INT(11) NOT NULL DEFAULT 0,

    PRIMARY KEY (`account_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`character_paragon_stats` (
    `guid` INT(11) NOT NULL,
    `stat_id` INT(11) NOT NULL,
    `stat_value` INT(11) NOT NULL,

    PRIMARY KEY (`guid`, `stat_id`)
);

-- owner_type: 0 = character-linked GUID, 1 = account-linked account ID.
-- high_water makes skill-point rewards one-time; pending_xp banks genuine
-- future gains earned before MINIMUM_LEVEL_FOR_PARAGON_XP.
CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_profession_progress` (
    `owner_type` TINYINT UNSIGNED NOT NULL,
    `owner_id` INT UNSIGNED NOT NULL,
    `skill_id` SMALLINT UNSIGNED NOT NULL,
    `high_water` SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`owner_type`, `owner_id`, `skill_id`)
) ENGINE=InnoDB;

-- Final profession-recipe spells are account-wide one-time entitlements.
-- Existing spellbooks are seeded without XP by the Lua runtime once per
-- character and generated catalogue version.
CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_recipe_reward_claim` (
    `account_id` INT UNSIGNED NOT NULL,
    `spell_id` INT UNSIGNED NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `claimed_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`account_id`, `spell_id`),
    KEY `ix_paragon_recipe_pending` (`account_id`, `pending_xp`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_recipe_reward_seed` (
    `guid` INT UNSIGNED NOT NULL,
    `account_id` INT UNSIGNED NOT NULL,
    `catalog_version` INT UNSIGNED NOT NULL,
    `seeded_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (`guid`),
    KEY `ix_paragon_recipe_seed_account` (`account_id`)
) ENGINE=InnoDB;

-- --------------------------------------------------------------------------
-- Collection rewards and pre-minimum-level banking
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_collectible_spell_xp` (
    `spell_id` INT NOT NULL,
    `kind` VARCHAR(10) NOT NULL,
    `name` VARCHAR(120) NOT NULL,
    `xp` INT NOT NULL,

    PRIMARY KEY (`spell_id`)
);

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_collectible_item_xp` (
    `item_id` INT NOT NULL,
    `name` VARCHAR(120) NOT NULL,
    `xp` INT NOT NULL,

    PRIMARY KEY (`item_id`)
);

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_collectible_account_item_xp` (
    `kind` VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `item_id` INT UNSIGNED NOT NULL,
    `name` VARCHAR(120) NOT NULL,
    `xp` INT UNSIGNED NOT NULL,

    PRIMARY KEY (`kind`, `item_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_rewarded_collectible_spell` (
    `account_id` INT UNSIGNED NOT NULL,
    `spell_id` INT UNSIGNED NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`account_id`, `spell_id`),
    KEY `ix_paragon_collectible_spell_pending` (`account_id`, `pending_xp`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_rewarded_appearance` (
    `account_id` INT UNSIGNED NOT NULL,
    `item_id` INT UNSIGNED NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`account_id`, `item_id`),
    KEY `ix_paragon_appearance_pending` (`account_id`, `pending_xp`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_rewarded_account_item` (
    `account_id` INT UNSIGNED NOT NULL,
    `kind` VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `item_id` INT UNSIGNED NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`account_id`, `kind`, `item_id`),
    KEY `ix_paragon_account_item_pending` (`account_id`, `pending_xp`)
) ENGINE=InnoDB;

-- Account-wide reputation high-water makes the fixed reward genuinely
-- one-time. Faction-change counterparts are stored under one canonical ID.
CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_reputation_progress` (
    `account_id` INT UNSIGNED NOT NULL,
    `faction_id` INT UNSIGNED NOT NULL,
    `high_water` INT NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`account_id`, `faction_id`),
    KEY `ix_paragon_reputation_pending` (`account_id`, `pending_xp`)
) ENGINE=InnoDB;

-- Achievement points pay once per account. Faction-equivalent IDs are
-- canonicalized by the migration/runtime before they reach this table.
CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_rewarded_achievement` (
    `account_id` INT UNSIGNED NOT NULL,
    `achievement_id` INT UNSIGNED NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`account_id`, `achievement_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_banked_experience` (
    `guid` INT UNSIGNED NOT NULL,
    `amount` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`guid`)
);

-- --------------------------------------------------------------------------
-- Anniversary feature state
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_codex_alloc` (
    `guid` INT UNSIGNED NOT NULL,
    `node_id` INT UNSIGNED NOT NULL,
    `node_rank` INT UNSIGNED NOT NULL,

    PRIMARY KEY (`guid`, `node_id`)
) ENGINE=InnoDB;

-- --------------------------------------------------------------------------
-- Account-wide PvP reward settlement
-- --------------------------------------------------------------------------

-- One row is both the durable idempotency claim and the audit/history record
-- for one independently payable component of a bridge settlement. Pending
-- rows are a write-ahead queue; paid rows supply honor-pair and arena-roster
-- diminishing returns, reset-window caps, and weekly breadth uniqueness.
CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_pvp_reward_claim` (
    `account_id` INT UNSIGNED NOT NULL,
    `recipient_guid` INT UNSIGNED NOT NULL,
    `event_token` VARCHAR(191) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `component` VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `source_type` TINYINT UNSIGNED NOT NULL,
    `source_entry` INT UNSIGNED NOT NULL DEFAULT 0,
    `base_xp` BIGINT UNSIGNED NOT NULL,
    `awarded_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `counterpart_account_id` INT UNSIGNED NOT NULL DEFAULT 0,
    `opponent_key` VARCHAR(191) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT '',
    `period_key` VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT '',
    `entitlement_key` VARCHAR(191) CHARACTER SET ascii COLLATE ascii_bin NULL,
    `same_ip_risk` TINYINT UNSIGNED NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL,
    `paid_at` DATETIME NULL,

    PRIMARY KEY (`account_id`, `event_token`, `component`),
    UNIQUE KEY `uq_paragon_pvp_entitlement` (`account_id`, `entitlement_key`),
    KEY `ix_paragon_pvp_honor_pair`
        (`account_id`, `component`, `counterpart_account_id`, `paid_at`),
    KEY `ix_paragon_pvp_arena_roster`
        (`account_id`, `opponent_key`, `created_at`),
    KEY `ix_paragon_pvp_period`
        (`account_id`, `component`, `period_key`),
    KEY `ix_paragon_pvp_pending_owner`
        (`account_id`, `recipient_guid`, `paid_at`, `created_at`),
    KEY `ix_paragon_pvp_cleanup_paid` (`paid_at`),
    KEY `ix_paragon_pvp_cleanup_pending` (`created_at`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_custom_glyph` (
    `guid` INT UNSIGNED NOT NULL,
    `item` INT UNSIGNED NOT NULL,
    `property` INT UNSIGNED NOT NULL,
    `aura` INT UNSIGNED NOT NULL,

    PRIMARY KEY (`guid`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_racial_pick` (
    `guid` INT UNSIGNED NOT NULL,
    `pick_key` VARCHAR(32) NOT NULL,

    PRIMARY KEY (`guid`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_rare_kills` (
    `guid` INT UNSIGNED NOT NULL,
    `entry` INT UNSIGNED NOT NULL,

    PRIMARY KEY (`guid`, `entry`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_solo_clears` (
    `guid` INT UNSIGNED NOT NULL,
    `dungeon` SMALLINT UNSIGNED NOT NULL,

    PRIMARY KEY (`guid`, `dungeon`)
) ENGINE=InnoDB;
