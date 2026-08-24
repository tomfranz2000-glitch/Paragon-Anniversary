-- ============================================================================
-- Paragon Anniversary - Realm Configuration
-- ============================================================================
-- Applies the canonical Anniversary configuration to both new and existing
-- installations. Unlike 04_insert_default_config.sql, this file intentionally
-- updates existing rows. The canonical sql/install.sql bootstrap invokes this
-- rerunnable migration for both fresh installations and upgrades.
-- ============================================================================

ALTER TABLE `acore_ale`.`paragon_config_experience_skill`
    MODIFY COLUMN `experience` INT(11) NOT NULL DEFAULT 1000;

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

ALTER TABLE `acore_ale`.`paragon_profession_progress` ENGINE=InnoDB;

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
('UNIVERSAL_SKILL_EXPERIENCE', '1000'),
('UNIVERSAL_QUEST_EXPERIENCE', '1'),
('PARAGON_ACHIEVEMENT_POINT_XP', '1000'),
('PARAGON_GROUP_XP_DISTANCE', '74'),

-- Experience Multipliers
('EXPERIENCE_MULTIPLIER_LOW_LEVEL', '1'),
('EXPERIENCE_MULTIPLIER_HIGH_LEVEL', '1'),
('LOW_LEVEL_THRESHOLD', '5'),
('HIGH_LEVEL_THRESHOLD', '100'),

-- Point Customization
('DEFAULT_STAT_LIMIT', '255')
ON DUPLICATE KEY UPDATE value = VALUES(value);
