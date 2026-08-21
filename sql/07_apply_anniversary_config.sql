-- ============================================================================
-- Paragon Anniversary - Realm Configuration
-- ============================================================================
-- Applies the canonical Anniversary configuration to both new and existing
-- installations. Unlike 06_insert_default_config.sql, this file intentionally
-- updates existing rows. Run it once when upgrading an older installation.
-- ============================================================================

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
('UNIVERSAL_SKILL_EXPERIENCE', '25'),
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
