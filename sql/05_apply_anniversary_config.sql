-- ============================================================================
-- Paragon Anniversary - Realm Configuration
-- ============================================================================
-- Applies the canonical Anniversary configuration to both new and existing
-- installations. Unlike 04_insert_default_config.sql, this file intentionally
-- updates existing rows. The canonical sql/install.sql bootstrap invokes this
-- rerunnable migration for both fresh installations and upgrades.
-- ============================================================================

ALTER TABLE `acore_ale`.`paragon_config_experience_skill`
    MODIFY COLUMN `experience` INT(11) NOT NULL DEFAULT 2000;

-- The profession payout acknowledgement updates its progression row and
-- pending ledger in one statement, so both tables must be transactional.
ALTER TABLE `acore_ale`.`character_paragon` ENGINE=InnoDB;
ALTER TABLE `acore_ale`.`account_paragon` ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_profession_progress` (
    `owner_type` TINYINT UNSIGNED NOT NULL,
    `owner_id` INT UNSIGNED NOT NULL,
    `skill_id` SMALLINT UNSIGNED NOT NULL,
    `high_water` SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`owner_type`, `owner_id`, `skill_id`)
) ENGINE=InnoDB;

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

ALTER TABLE `acore_ale`.`paragon_pvp_reward_claim` ENGINE=InnoDB;

ALTER TABLE `acore_ale`.`paragon_profession_progress` ENGINE=InnoDB;
ALTER TABLE `acore_ale`.`paragon_banked_experience` ENGINE=InnoDB;
ALTER TABLE `acore_ale`.`paragon_config` ENGINE=InnoDB;

START TRANSACTION;

-- Retire the temporary runtime-scaling policy if an intermediate installation
-- created it. Every one-time source now stores and awards its final value.
DELETE FROM `acore_ale`.`paragon_config`
WHERE `field` = 'PARAGON_ONE_TIME_XP_MULTIPLIER';

-- Upgrade unpaid one-time claims from the former 1000-point authorities to
-- the new final 2000-point values. The joins make this exactly-once: this file
-- upserts both config rows to 2000 below, so every rerun skips these updates.
UPDATE `acore_ale`.`paragon_profession_progress` profession
JOIN `acore_ale`.`paragon_config` config
  ON config.`field` = 'UNIVERSAL_SKILL_EXPERIENCE'
 AND config.`value` = '1000'
SET profession.`pending_xp` = profession.`pending_xp` * 2
WHERE profession.`pending_xp` > 0;

UPDATE `acore_ale`.`paragon_banked_experience` banked
JOIN `acore_ale`.`paragon_config` config
  ON config.`field` = 'PARAGON_ACHIEVEMENT_POINT_XP'
 AND config.`value` = '1000'
SET banked.`amount` = banked.`amount` * 2
WHERE banked.`amount` > 0;

-- Seed both supported progression scopes from existing character skills.
-- Re-running this migration is safe: it can only raise high-water and never
-- creates pending XP, so historical points do not pay retroactively.
INSERT INTO `acore_ale`.`paragon_profession_progress`
    (`owner_type`, `owner_id`, `skill_id`, `high_water`, `pending_xp`)
SELECT 1, c.`account`, cs.`skill`, MAX(cs.`value`), 0
FROM `acore_characters`.`character_skills` cs
JOIN `acore_characters`.`characters` c ON c.`guid` = cs.`guid`
WHERE cs.`skill` IN (129,164,165,171,182,185,186,197,202,333,356,393,755,773)
GROUP BY c.`account`, cs.`skill`
ON DUPLICATE KEY UPDATE
    `high_water` = GREATEST(`high_water`, VALUES(`high_water`));

INSERT INTO `acore_ale`.`paragon_profession_progress`
    (`owner_type`, `owner_id`, `skill_id`, `high_water`, `pending_xp`)
SELECT 0, cs.`guid`, cs.`skill`, cs.`value`, 0
FROM `acore_characters`.`character_skills` cs
WHERE cs.`skill` IN (129,164,165,171,182,185,186,197,202,333,356,393,755,773)
ON DUPLICATE KEY UPDATE
    `high_water` = GREATEST(`high_water`, VALUES(`high_water`));

-- Canonical point-spend categories and runtime-supported statistics. INSERT
-- IGNORE fills a clean/partial install without replacing operator-owned rows at
-- any existing ID. GOLD and MOVE_SPEED from the dated example dump are deliberately
-- not seeded: no server runtime implementation exists for those AURA keys.
INSERT IGNORE INTO `acore_ale`.`paragon_config_category` (`id`, `name`) VALUES
(1, 'Defense'),
(2, 'Attack'),
(3, 'Magic'),
(4, 'Other');

INSERT IGNORE INTO `acore_ale`.`paragon_config_statistic`
    (`id`, `category`, `type`, `type_value`, `icon`, `factor`, `limit`, `application`)
VALUES
(1,  1, 'UNIT_MODS',     'ARMOR',             'Interface/Icons/INV_Chest_Plate01',          1, 0,   0),
(2,  1, 'COMBAT_RATING', 'PARRY',              'Interface/Icons/Ability_Parry',              1, 0,   0),
(3,  1, 'COMBAT_RATING', 'BLOCK',              'Interface/Icons/Ability_Defend',             1, 0,   0),
(4,  1, 'COMBAT_RATING', 'DEFENSE_SKILL',      'Interface/Icons/Spell_Holy_MindSooth',       1, 0,   0),
(5,  1, 'COMBAT_RATING', 'DODGE',              'Interface/Icons/spell_arcane_blink',         1, 0,   0),
(6,  2, 'UNIT_MODS',     'STAT_STRENGTH',      'Interface/Icons/Ability_Warrior_InnerRage',  1, 0,   0),
(7,  2, 'UNIT_MODS',     'STAT_AGILITY',       'Interface/Icons/Ability_Rogue_Sprint',       1, 0,   0),
(8,  2, 'COMBAT_RATING', 'CRIT_MELEE',         'Interface/Icons/Ability_CriticalStrike',     1, 0,   0),
(9,  2, 'COMBAT_RATING', 'HASTE_MELEE',        'Interface/Icons/Spell_Nature_Bloodlust',     1, 0,   0),
(10, 2, 'COMBAT_RATING', 'ARMOR_PENETRATION',  'Interface/Icons/Ability_Warrior_Riposte',    1, 0,   0),
(11, 3, 'UNIT_MODS',     'STAT_INTELLECT',     'Interface/Icons/Spell_Holy_MagicalSentry',   1, 0,   0),
(12, 3, 'UNIT_MODS',     'STAT_SPIRIT',        'Interface/Icons/spell_holy_spiritualguidence', 1, 0, 0),
(13, 3, 'COMBAT_RATING', 'HIT_SPELL',          'Interface/Icons/Spell_Arcane_Blast',         1, 0,   0),
(14, 3, 'COMBAT_RATING', 'HASTE_SPELL',        'Interface/Icons/Spell_Frost_ManaBurn',       1, 0,   0),
(15, 4, 'AURA',          'EXPERIENCE',         'Interface/Icons/INV_Misc_Book_11',           1, 255, 0),
(17, 4, 'AURA',          'LOOT',               'Interface/Icons/INV_Misc_Bag_10_Blue',       1, 255, 0),
(19, 4, 'AURA',          'REPUTATION',         'Interface/Icons/Achievement_Reputation_01',  1, 255, 0);

INSERT INTO `acore_ale`.`paragon_config` (field, value) VALUES
-- System Control
('ENABLE_PARAGON_SYSTEM', '1'),
('MINIMUM_LEVEL_FOR_PARAGON_XP', '80'),
('PARAGON_LEVEL_CAP', '10000'),
('LEVEL_LINKED_TO_ACCOUNT', '1'),
('LEVEL_UP_ANIMATION', '64785'),

-- Progression Settings
('BASE_MAX_EXPERIENCE', '30000'),
('POINTS_PER_LEVEL', '1'),
('PARAGON_STARTING_LEVEL', '1'),
('PARAGON_STARTING_EXPERIENCE', '0'),
('PARAGON_CURVE_R0', '0.0429'),
('PARAGON_CURVE_K', '20'),

-- Experience Rewards
('UNIVERSAL_CREATURE_EXPERIENCE', '50'),
('UNIVERSAL_ACHIEVEVEMENT_EXPERIENCE', '100'),
('UNIVERSAL_SKILL_EXPERIENCE', '2000'),
('UNIVERSAL_QUEST_EXPERIENCE', '1'),
('PARAGON_ACHIEVEMENT_POINT_XP', '2000'),
('PARAGON_GROUP_XP_DISTANCE', '74'),

-- PvP Merit (stored base values; there is no hidden global PvP multiplier)
('PARAGON_PVP_ENABLED', '1'),
('PARAGON_PVP_HONOR_XP_PER_POINT', '8'),
('PARAGON_PVP_HONOR_DR_WINDOW_MINUTES', '30'),
('PARAGON_PVP_HONOR_DR_FULL_CREDITS', '1'),
('PARAGON_PVP_HONOR_DR_HALF_CREDITS', '2'),
('PARAGON_PVP_HONOR_DR_TENTH_CREDITS', '3'),
('PARAGON_PVP_HONOR_DR_FULL_PERCENT', '100'),
('PARAGON_PVP_HONOR_DR_HALF_PERCENT', '50'),
('PARAGON_PVP_HONOR_DR_TENTH_PERCENT', '10'),
('PARAGON_PVP_HONOR_DR_LATER_PERCENT', '0'),
('PARAGON_PVP_MATCH_MIN_SECONDS', '60'),
('PARAGON_PVP_MATCH_MIN_ACTIVE_BUCKETS', '2'),
('PARAGON_PVP_MATCH_MIN_ACTIVE_PERCENT', '30'),
('PARAGON_PVP_BG_XP_PER_ACTIVE_MINUTE', '4000'),
('PARAGON_PVP_BG_WIN_XP_PER_ACTIVE_MINUTE', '1000'),
('PARAGON_PVP_BG_DRAW_XP_PER_ACTIVE_MINUTE', '500'),
('PARAGON_PVP_BG_OBJECTIVE_MAJOR_XP', '8000'),
('PARAGON_PVP_BG_OBJECTIVE_STANDARD_XP', '4000'),
('PARAGON_PVP_BG_OBJECTIVE_ASSIST_XP', '2000'),
('PARAGON_PVP_BG_OBJECTIVE_CAP_PERCENT', '20'),
('PARAGON_PVP_BG_CAP_WSG_MINUTES', '25'),
('PARAGON_PVP_BG_CAP_AB_MINUTES', '30'),
('PARAGON_PVP_BG_CAP_EOTS_MINUTES', '25'),
('PARAGON_PVP_BG_CAP_AV_MINUTES', '45'),
('PARAGON_PVP_BG_CAP_SOTA_MINUTES', '25'),
('PARAGON_PVP_BG_CAP_IOC_MINUTES', '40'),
('PARAGON_PVP_BG_CAP_GENERIC_MINUTES', '30'),
('PARAGON_PVP_WINTERGRASP_CAP_MINUTES', '40'),
('PARAGON_PVP_ARENA_MIN_SECONDS', '15'),
('PARAGON_PVP_ARENA_MIN_CONTRIBUTION', '10000'),
('PARAGON_PVP_ARENA_2V2_WIN_XP', '37500'),
('PARAGON_PVP_ARENA_2V2_LOSS_XP', '26250'),
('PARAGON_PVP_ARENA_3V3_WIN_XP', '45000'),
('PARAGON_PVP_ARENA_3V3_LOSS_XP', '31500'),
('PARAGON_PVP_ARENA_5V5_WIN_XP', '56250'),
('PARAGON_PVP_ARENA_5V5_LOSS_XP', '39000'),
('PARAGON_PVP_SKIRMISH_WIN_XP', '11250'),
('PARAGON_PVP_SKIRMISH_LOSS_XP', '7500'),
('PARAGON_PVP_SKIRMISH_DAILY_CAP_XP', '56250'),
('PARAGON_PVP_ARENA_ROSTER_DR_WINDOW_MINUTES', '60'),
('PARAGON_PVP_ARENA_ROSTER_DR_FULL_SETTLEMENTS', '3'),
('PARAGON_PVP_ARENA_ROSTER_DR_HALF_SETTLEMENTS', '5'),
('PARAGON_PVP_ARENA_ROSTER_DR_TENTH_SETTLEMENTS', '6'),
('PARAGON_PVP_ARENA_ROSTER_DR_FULL_PERCENT', '100'),
('PARAGON_PVP_ARENA_ROSTER_DR_HALF_PERCENT', '50'),
('PARAGON_PVP_ARENA_ROSTER_DR_TENTH_PERCENT', '10'),
('PARAGON_PVP_ARENA_ROSTER_DR_LATER_PERCENT', '0'),
('PARAGON_PVP_OUTDOOR_STANDARD_XP', '15000'),
('PARAGON_PVP_OUTDOOR_MAJOR_XP', '30000'),
('PARAGON_PVP_DUEL_WIN_XP', '5000'),
('PARAGON_PVP_DUEL_LOSS_XP', '2000'),
('PARAGON_PVP_DUEL_DISTINCT_OPPONENTS_PER_DAY', '3'),
('PARAGON_PVP_WEEKLY_BREADTH_XP', '20000'),
('PARAGON_PVP_DAILY_RESET_WORLDSTATE', '20005'),
('PARAGON_PVP_WEEKLY_RESET_WORLDSTATE', '20002'),
('PARAGON_PVP_DAILY_RESET_INTERVAL_SECONDS', '86400'),
('PARAGON_PVP_WEEKLY_RESET_INTERVAL_SECONDS', '604800'),
('PARAGON_PVP_RESET_FALLBACK_ANCHOR_UNIX', '0'),
('PARAGON_PVP_LEDGER_RETENTION_DAYS', '90'),
('PARAGON_PVP_PENDING_RETENTION_DAYS', '365'),
('PARAGON_PVP_CLEANUP_INTERVAL_SECONDS', '3600'),

-- Experience Multipliers
('EXPERIENCE_MULTIPLIER_LOW_LEVEL', '1'),
('EXPERIENCE_MULTIPLIER_HIGH_LEVEL', '1'),
('LOW_LEVEL_THRESHOLD', '5'),
('HIGH_LEVEL_THRESHOLD', '100'),

-- Point Customization
('DEFAULT_STAT_LIMIT', '255')
ON DUPLICATE KEY UPDATE value = VALUES(value);

COMMIT;
