-- ============================================================================
-- Durable collection reward settlement
-- ============================================================================
-- Existing mount/companion/appearance mirrors are authoritative no-backpay
-- claims. Adding pending_xp with a zero default preserves every old row as an
-- already-settled entitlement. New runtime claims write their exact value here
-- before advancing Paragon progression.
-- ============================================================================

SET @paragon_collection_ddl := IF(
    (SELECT COUNT(*) FROM `information_schema`.`columns`
     WHERE `table_schema` = 'acore_ale'
       AND `table_name` = 'paragon_rewarded_collectible_spell'
       AND `column_name` = 'pending_xp') = 0,
    'ALTER TABLE `acore_ale`.`paragon_rewarded_collectible_spell` ADD COLUMN `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0',
    'SELECT 1');
PREPARE paragon_collection_stmt FROM @paragon_collection_ddl;
EXECUTE paragon_collection_stmt;
DEALLOCATE PREPARE paragon_collection_stmt;

SET @paragon_collection_ddl := IF(
    (SELECT COUNT(*) FROM `information_schema`.`statistics`
     WHERE `table_schema` = 'acore_ale'
       AND `table_name` = 'paragon_rewarded_collectible_spell'
       AND `index_name` = 'ix_paragon_collectible_spell_pending') = 0,
    'ALTER TABLE `acore_ale`.`paragon_rewarded_collectible_spell` ADD INDEX `ix_paragon_collectible_spell_pending` (`account_id`, `pending_xp`)',
    'SELECT 1');
PREPARE paragon_collection_stmt FROM @paragon_collection_ddl;
EXECUTE paragon_collection_stmt;
DEALLOCATE PREPARE paragon_collection_stmt;

SET @paragon_collection_ddl := IF(
    (SELECT COUNT(*) FROM `information_schema`.`columns`
     WHERE `table_schema` = 'acore_ale'
       AND `table_name` = 'paragon_rewarded_appearance'
       AND `column_name` = 'pending_xp') = 0,
    'ALTER TABLE `acore_ale`.`paragon_rewarded_appearance` ADD COLUMN `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0',
    'SELECT 1');
PREPARE paragon_collection_stmt FROM @paragon_collection_ddl;
EXECUTE paragon_collection_stmt;
DEALLOCATE PREPARE paragon_collection_stmt;

SET @paragon_collection_ddl := IF(
    (SELECT COUNT(*) FROM `information_schema`.`statistics`
     WHERE `table_schema` = 'acore_ale'
       AND `table_name` = 'paragon_rewarded_appearance'
       AND `index_name` = 'ix_paragon_appearance_pending') = 0,
    'ALTER TABLE `acore_ale`.`paragon_rewarded_appearance` ADD INDEX `ix_paragon_appearance_pending` (`account_id`, `pending_xp`)',
    'SELECT 1');
PREPARE paragon_collection_stmt FROM @paragon_collection_ddl;
EXECUTE paragon_collection_stmt;
DEALLOCATE PREPARE paragon_collection_stmt;
