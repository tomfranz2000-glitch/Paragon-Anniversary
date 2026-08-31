-- ============================================================================
-- Account-wide one-time achievement reward claims
-- ============================================================================
-- This migration deliberately seeds every achievement already earned by any
-- character on an account.  Seeding writes entitlement markers only: it does
-- not award, bank, reconcile, or top up Paragon XP.
--
-- Alliance/Horde counterparts share the smaller ID as their canonical key,
-- matching the runtime guard.  The migration is rerunnable.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_rewarded_achievement` (
    `account_id` INT UNSIGNED NOT NULL,
    `achievement_id` INT UNSIGNED NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`account_id`, `achievement_id`)
) ENGINE=InnoDB;

-- Upgrade an installation which already received the earlier two-column
-- version of this migration. Existing claims become settled markers; adding
-- the zero default deliberately does not create retroactive XP.
SET @paragon_achievement_pending_exists := (
    SELECT COUNT(*)
    FROM `information_schema`.`COLUMNS`
    WHERE `TABLE_SCHEMA` = 'acore_ale'
      AND `TABLE_NAME` = 'paragon_rewarded_achievement'
      AND `COLUMN_NAME` = 'pending_xp'
);
SET @paragon_achievement_pending_sql := IF(
    @paragon_achievement_pending_exists = 0,
    'ALTER TABLE `acore_ale`.`paragon_rewarded_achievement` '
        'ADD COLUMN `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0 '
        'AFTER `achievement_id`',
    'SELECT 1'
);
PREPARE paragon_achievement_pending_stmt
    FROM @paragon_achievement_pending_sql;
EXECUTE paragon_achievement_pending_stmt;
DEALLOCATE PREPARE paragon_achievement_pending_stmt;

INSERT IGNORE INTO `acore_ale`.`paragon_rewarded_achievement`
    (`account_id`, `achievement_id`, `pending_xp`)
SELECT characters.`account`,
       CASE
           WHEN faction_pair.`alliance_id` IS NULL THEN earned.`achievement`
           ELSE LEAST(faction_pair.`alliance_id`, faction_pair.`horde_id`)
       END,
       0
FROM `acore_characters`.`character_achievement` earned
JOIN `acore_characters`.`characters` characters
  ON characters.`guid` = earned.`guid`
LEFT JOIN `acore_world`.`player_factionchange_achievement` faction_pair
  ON faction_pair.`alliance_id` = earned.`achievement`
  OR faction_pair.`horde_id` = earned.`achievement`;
