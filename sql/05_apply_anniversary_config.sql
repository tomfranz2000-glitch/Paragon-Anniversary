-- ============================================================================
-- Paragon Anniversary - Realm Configuration
-- ============================================================================
-- Applies the canonical Anniversary configuration to both new and existing
-- installations. Unlike 04_insert_default_config.sql, this file intentionally
-- updates existing rows. Run it once when upgrading an older installation.
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
