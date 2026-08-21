-- ============================================================================
-- Paragon System - Validation Triggers
-- ============================================================================
-- Validates statistic type values on INSERT and UPDATE
-- Ensures type_value matches the selected type (COMBAT_RATING, UNIT_MODS, or AURA)
-- ============================================================================

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
