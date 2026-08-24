-- ============================================================================
-- Paragon System - Validation Triggers
-- ============================================================================
-- Migrates legacy numeric statistic keys to their symbolic runtime names, then
-- validates type/value pairs on INSERT and UPDATE. The VARCHAR conversion
-- intentionally preserves unknown custom values instead of truncating them.
-- ============================================================================

DROP TRIGGER IF EXISTS `acore_ale`.`paragon_config_statistics_before_insert`;
DROP TRIGGER IF EXISTS `acore_ale`.`paragon_config_statistics_before_update`;

ALTER TABLE `acore_ale`.`paragon_config_statistic`
    MODIFY COLUMN `type_value` VARCHAR(32) NOT NULL DEFAULT 'LOOT';

-- The historical schema stored the numeric values from paragon_constant.lua.
-- Convert every known value after widening to text. Any unknown custom numeric
-- key remains byte-for-byte intact so an operator can map it deliberately.
UPDATE `acore_ale`.`paragon_config_statistic`
SET `type_value` = CASE `type`
    WHEN 'COMBAT_RATING' THEN CASE CAST(`type_value` AS UNSIGNED)
        WHEN 0 THEN 'WEAPON_SKILL'
        WHEN 1 THEN 'DEFENSE_SKILL'
        WHEN 2 THEN 'DODGE'
        WHEN 3 THEN 'PARRY'
        WHEN 4 THEN 'BLOCK'
        WHEN 5 THEN 'HIT_MELEE'
        WHEN 6 THEN 'HIT_RANGED'
        WHEN 7 THEN 'HIT_SPELL'
        WHEN 8 THEN 'CRIT_MELEE'
        WHEN 9 THEN 'CRIT_RANGED'
        WHEN 10 THEN 'CRIT_SPELL'
        WHEN 11 THEN 'HIT_TAKEN_MELEE'
        WHEN 12 THEN 'HIT_TAKEN_RANGED'
        WHEN 13 THEN 'HIT_TAKEN_SPELL'
        WHEN 14 THEN 'CRIT_TAKEN_MELEE'
        WHEN 15 THEN 'CRIT_TAKEN_RANGED'
        WHEN 16 THEN 'CRIT_TAKEN_SPELL'
        WHEN 17 THEN 'HASTE_MELEE'
        WHEN 18 THEN 'HASTE_RANGED'
        WHEN 19 THEN 'HASTE_SPELL'
        WHEN 20 THEN 'WEAPON_SKILL_MAINHAND'
        WHEN 21 THEN 'WEAPON_SKILL_OFFHAND'
        WHEN 22 THEN 'WEAPON_SKILL_RANGED'
        WHEN 23 THEN 'EXPERTISE'
        WHEN 24 THEN 'ARMOR_PENETRATION'
        ELSE `type_value`
    END
    WHEN 'UNIT_MODS' THEN CASE CAST(`type_value` AS UNSIGNED)
        WHEN 0 THEN 'STAT_STRENGTH'
        WHEN 1 THEN 'STAT_AGILITY'
        WHEN 2 THEN 'STAT_STAMINA'
        WHEN 3 THEN 'STAT_INTELLECT'
        WHEN 4 THEN 'STAT_SPIRIT'
        WHEN 5 THEN 'HEALTH'
        WHEN 6 THEN 'MANA'
        WHEN 7 THEN 'RAGE'
        WHEN 8 THEN 'FOCUS'
        WHEN 9 THEN 'ENERGY'
        WHEN 10 THEN 'HAPPINESS'
        WHEN 11 THEN 'RUNE'
        WHEN 12 THEN 'RUNIC_POWER'
        WHEN 13 THEN 'ARMOR'
        WHEN 14 THEN 'RESISTANCE_HOLY'
        WHEN 15 THEN 'RESISTANCE_FIRE'
        WHEN 16 THEN 'RESISTANCE_NATURE'
        WHEN 17 THEN 'RESISTANCE_FROST'
        WHEN 18 THEN 'RESISTANCE_SHADOW'
        WHEN 19 THEN 'RESISTANCE_ARCANE'
        WHEN 20 THEN 'ATTACK_POWER'
        WHEN 21 THEN 'ATTACK_POWER_RANGED'
        WHEN 22 THEN 'DAMAGE_MAINHAND'
        WHEN 23 THEN 'DAMAGE_OFFHAND'
        WHEN 24 THEN 'DAMAGE_RANGED'
        ELSE `type_value`
    END
    WHEN 'AURA' THEN CASE CAST(`type_value` AS UNSIGNED)
        WHEN 1900000 THEN 'LOOT'
        WHEN 1900001 THEN 'REPUTATION'
        WHEN 1900002 THEN 'EXPERIENCE'
        ELSE `type_value`
    END
    ELSE `type_value`
END
WHERE `type_value` REGEXP '^[0-9]+$';

DELIMITER //

-- BEFORE INSERT Trigger
-- Validates that type_value matches the selected type
CREATE TRIGGER IF NOT EXISTS `acore_ale`.`paragon_config_statistics_before_insert`
BEFORE INSERT ON `acore_ale`.`paragon_config_statistic`
FOR EACH ROW
BEGIN
    DECLARE v_type VARCHAR(50);
    DECLARE v_value VARCHAR(50);

    SET v_type = NEW.type;
    SET v_value = NEW.type_value;

    IF v_type = 'COMBAT_RATING' THEN
        IF v_value NOT IN (
            'WEAPON_SKILL', 'DEFENSE_SKILL', 'DODGE', 'PARRY', 'BLOCK',
            'HIT_MELEE', 'HIT_RANGED', 'HIT_SPELL',
            'CRIT_MELEE', 'CRIT_RANGED', 'CRIT_SPELL',
            'HIT_TAKEN_MELEE', 'HIT_TAKEN_RANGED', 'HIT_TAKEN_SPELL',
            'CRIT_TAKEN_MELEE', 'CRIT_TAKEN_RANGED', 'CRIT_TAKEN_SPELL',
            'HASTE_MELEE', 'HASTE_RANGED', 'HASTE_SPELL',
            'WEAPON_SKILL_MAINHAND', 'WEAPON_SKILL_OFFHAND', 'WEAPON_SKILL_RANGED',
            'EXPERTISE', 'ARMOR_PENETRATION'
        ) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid COMBAT_RATING value for this type.';
        END IF;
    END IF;

    IF v_type = 'UNIT_MODS' THEN
        IF v_value NOT IN (
            'STAT_STRENGTH', 'STAT_AGILITY', 'STAT_STAMINA', 'STAT_INTELLECT', 'STAT_SPIRIT',
            'HEALTH', 'MANA', 'RAGE', 'FOCUS', 'ENERGY', 'HAPPINESS',
            'RUNE', 'RUNIC_POWER', 'ARMOR',
            'RESISTANCE_HOLY', 'RESISTANCE_FIRE', 'RESISTANCE_NATURE', 'RESISTANCE_FROST', 'RESISTANCE_SHADOW', 'RESISTANCE_ARCANE',
            'ATTACK_POWER', 'ATTACK_POWER_RANGED',
            'DAMAGE_MAINHAND', 'DAMAGE_OFFHAND', 'DAMAGE_RANGED'
        ) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid UNIT_MODS value for this type.';
        END IF;
    END IF;

    IF v_type = 'AURA' THEN
        IF v_value NOT IN ('LOOT', 'REPUTATION', 'EXPERIENCE') THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid AURA value for this type.';
        END IF;
    END IF;
END//

-- BEFORE UPDATE Trigger
-- Validates that type_value matches the selected type
CREATE TRIGGER IF NOT EXISTS `acore_ale`.`paragon_config_statistics_before_update`
BEFORE UPDATE ON `acore_ale`.`paragon_config_statistic`
FOR EACH ROW
BEGIN
    DECLARE v_type VARCHAR(50);
    DECLARE v_value VARCHAR(50);

    SET v_type = NEW.type;
    SET v_value = NEW.type_value;

    IF v_type = 'COMBAT_RATING' THEN
        IF v_value NOT IN (
            'WEAPON_SKILL', 'DEFENSE_SKILL', 'DODGE', 'PARRY', 'BLOCK',
            'HIT_MELEE', 'HIT_RANGED', 'HIT_SPELL',
            'CRIT_MELEE', 'CRIT_RANGED', 'CRIT_SPELL',
            'HIT_TAKEN_MELEE', 'HIT_TAKEN_RANGED', 'HIT_TAKEN_SPELL',
            'CRIT_TAKEN_MELEE', 'CRIT_TAKEN_RANGED', 'CRIT_TAKEN_SPELL',
            'HASTE_MELEE', 'HASTE_RANGED', 'HASTE_SPELL',
            'WEAPON_SKILL_MAINHAND', 'WEAPON_SKILL_OFFHAND', 'WEAPON_SKILL_RANGED',
            'EXPERTISE', 'ARMOR_PENETRATION'
        ) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid COMBAT_RATING value for this type.';
        END IF;
    END IF;

    IF v_type = 'UNIT_MODS' THEN
        IF v_value NOT IN (
            'STAT_STRENGTH', 'STAT_AGILITY', 'STAT_STAMINA', 'STAT_INTELLECT', 'STAT_SPIRIT',
            'HEALTH', 'MANA', 'RAGE', 'FOCUS', 'ENERGY', 'HAPPINESS',
            'RUNE', 'RUNIC_POWER', 'ARMOR',
            'RESISTANCE_HOLY', 'RESISTANCE_FIRE', 'RESISTANCE_NATURE', 'RESISTANCE_FROST', 'RESISTANCE_SHADOW', 'RESISTANCE_ARCANE',
            'ATTACK_POWER', 'ATTACK_POWER_RANGED',
            'DAMAGE_MAINHAND', 'DAMAGE_OFFHAND', 'DAMAGE_RANGED'
        ) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid UNIT_MODS value for this type.';
        END IF;
    END IF;

    IF v_type = 'AURA' THEN
        IF v_value NOT IN ('LOOT', 'REPUTATION', 'EXPERIENCE') THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid AURA value for this type.';
        END IF;
    END IF;
END//

DELIMITER ;
