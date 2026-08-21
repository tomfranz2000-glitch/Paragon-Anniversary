-- ============================================================================
-- Paragon Anniversary - Runtime Support Tables
-- ============================================================================
-- Creates the tables used by the collection-reward and pre-80 banking modules.
-- The collectible XP classifier populates the first two tables and can seed the
-- rewarded-item mirrors; an empty catalogue is valid and must not prevent the
-- worldserver from starting.
-- ============================================================================

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

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_rewarded_collectible_spell` (
    `account_id` INT UNSIGNED NOT NULL,
    `spell_id` INT UNSIGNED NOT NULL,

    PRIMARY KEY (`account_id`, `spell_id`)
);

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_rewarded_appearance` (
    `account_id` INT UNSIGNED NOT NULL,
    `item_id` INT UNSIGNED NOT NULL,

    PRIMARY KEY (`account_id`, `item_id`)
);

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_banked_experience` (
    `guid` INT UNSIGNED NOT NULL,
    `amount` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`guid`)
);
