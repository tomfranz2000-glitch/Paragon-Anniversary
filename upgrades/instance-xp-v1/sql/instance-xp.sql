-- Paragon Anniversary: instance creature XP v1
-- Feature-scoped, rerunnable migration. The installer verifies that
-- acore_ale.paragon_config is InnoDB before executing this transaction and
-- verifies all five values before accepting the cutover.

START TRANSACTION;

INSERT INTO `acore_ale`.`paragon_config` (`field`, `value`) VALUES
('PARAGON_CREATURE_XP_TBC_HEROIC_DUNGEON_MULTIPLIER', '1.25'),
('PARAGON_CREATURE_XP_WOTLK_HEROIC_DUNGEON_MULTIPLIER', '1.5'),
('PARAGON_CREATURE_XP_TBC_RAID_MULTIPLIER', '2'),
('PARAGON_CREATURE_XP_WOTLK_NORMAL_RAID_MULTIPLIER', '2.5'),
('PARAGON_CREATURE_XP_WOTLK_HEROIC_RAID_MULTIPLIER', '4')
ON DUPLICATE KEY UPDATE `value` = VALUES(`value`);

COMMIT;
