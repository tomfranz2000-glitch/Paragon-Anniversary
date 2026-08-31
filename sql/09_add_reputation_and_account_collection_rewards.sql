-- ============================================================================
-- Reputation, toy, and heirloom one-time rewards
-- ============================================================================
-- One feature migration upgrades an existing installation. The authoritative
-- fresh-install definitions remain in 02_create_tables.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_collectible_account_item_xp` (
    `kind` VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `item_id` INT UNSIGNED NOT NULL,
    `name` VARCHAR(120) NOT NULL,
    `xp` INT UNSIGNED NOT NULL,

    PRIMARY KEY (`kind`, `item_id`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_rewarded_account_item` (
    `account_id` INT UNSIGNED NOT NULL,
    `kind` VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    `item_id` INT UNSIGNED NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`account_id`, `kind`, `item_id`),
    KEY `ix_paragon_account_item_pending` (`account_id`, `pending_xp`)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `acore_ale`.`paragon_reputation_progress` (
    `account_id` INT UNSIGNED NOT NULL,
    `faction_id` INT UNSIGNED NOT NULL,
    `high_water` INT NOT NULL,
    `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0,

    PRIMARY KEY (`account_id`, `faction_id`),
    KEY `ix_paragon_reputation_pending` (`account_id`, `pending_xp`)
) ENGINE=InnoDB;
