--- Paragon System Constants
-- This module defines all constants used by the paragon system including
-- database configuration, SQL queries, and statistic type enumerations
-- @module paragon_constant

return {
    --- Database name used for paragon system tables
    DB_NAME = "acore_ale",

    --- Runtime SQL queries. Schema/configuration DDL lives exclusively under
    --- sql/ and is installed through sql/install.sql.
    QUERY = {
        -- Select all categories
        SEL_CONFIG_CAT = "SELECT `id`, `name` FROM `%s`.`paragon_config_category`;",

        -- Select all statistic configurations
        SEL_CONFIG_STAT = "SELECT `id`, `category`, `type`, `type_value`, `icon`, `factor`, `limit`, `application` FROM `%s`.`paragon_config_statistic`;",

        -- Select all configuration settings
        SEL_CONFIG = "SELECT `field`, `value` FROM `%s`.`paragon_config`;",

        -- Select paragon level and experience for a character (character-linked)
        SEL_PARA_CHARACTER = "SELECT level, experience FROM `%s`.`character_paragon` WHERE guid = %d;",

        -- Select paragon level and experience for an account (account-linked)
        SEL_PARA_ACCOUNT = "SELECT level, experience FROM `%s`.`account_paragon` WHERE account_id = %d;",

        -- Insert new character paragon record (character-linked)
        INS_PARA_CHARACTER = "INSERT INTO `%s`.`character_paragon` (guid, level, experience) VALUES (%d, %d, %d) ON DUPLICATE KEY UPDATE level = VALUES(level), experience = VALUES(experience);",

        -- Insert new account paragon record (account-linked)
        INS_PARA_ACCOUNT = "INSERT INTO `%s`.`account_paragon` (account_id, level, experience) VALUES (%d, %d, %d) ON DUPLICATE KEY UPDATE level = VALUES(level), experience = VALUES(experience);",

        -- Delete character paragon record
        DEL_PARA_CHARACTER = "DELETE FROM `%s`.`character_paragon` WHERE guid = %d;",

        -- Delete account paragon record
        DEL_PARA_ACCOUNT = "DELETE FROM `%s`.`account_paragon` WHERE account_id = %d;",

        -- Delete only character-scoped profession mastery and pending XP.
        -- Account-scoped rows belong to the account's remaining characters.
        DEL_PROFESSION_PROGRESS_CHARACTER = "DELETE FROM `%s`.`paragon_profession_progress` WHERE owner_type = 0 AND owner_id = %d;",

        -- Select all statistics for a character
        SEL_PARA_STAT = "SELECT stat_id, stat_value FROM `%s`.`character_paragon_stats` WHERE guid = %d;",

        INS_PARA_STAT = "INSERT INTO `%s`.`character_paragon_stats` (guid, stat_id, stat_value) VALUES (%d, %d, %d) ON DUPLICATE KEY UPDATE stat_value = VALUES(stat_value);",

        -- Delete all statistics for a character
        DEL_PARA_STAT = "DELETE FROM `%s`.`character_paragon_stats` WHERE guid = %d;",

        -- Select all creature experience overrides
        SEL_CONFIG_EXP_CREATURE = "SELECT id, experience FROM `%s`.`paragon_config_experience_creature`;",

        -- Select all achievement experience overrides
        SEL_CONFIG_EXP_ACHIEVEMENT = "SELECT id, experience FROM `%s`.`paragon_config_experience_achievement`;",

        -- Select all skill experience overrides
        SEL_CONFIG_EXP_SKILL = "SELECT id, experience FROM `%s`.`paragon_config_experience_skill`;",

        -- Select all quest experience overrides
        SEL_CONFIG_EXP_QUEST = "SELECT id, experience FROM `%s`.`paragon_config_experience_quest`;"
    },

    --- Statistic Type Enumerations
    -- Defines the available statistic types that can be enhanced through the paragon system
    STATISTICS = {
        --- Combat Rating Statistics
        -- These affect combat performance metrics like hit chance, crit, haste, etc.
        COMBAT_RATING = {
            WEAPON_SKILL            = 0,
            DEFENSE_SKILL           = 1,
            DODGE                   = 2,
            PARRY                   = 3,
            BLOCK                   = 4,
            HIT_MELEE               = 5,
            HIT_RANGED              = 6,
            HIT_SPELL               = 7,
            CRIT_MELEE              = 8,
            CRIT_RANGED             = 9,
            CRIT_SPELL              = 10,
            HIT_TAKEN_MELEE         = 11,
            HIT_TAKEN_RANGED        = 12,
            HIT_TAKEN_SPELL         = 13,
            CRIT_TAKEN_MELEE        = 14,
            CRIT_TAKEN_RANGED       = 15,
            CRIT_TAKEN_SPELL        = 16,
            HASTE_MELEE             = 17,
            HASTE_RANGED            = 18,
            HASTE_SPELL             = 19,
            WEAPON_SKILL_MAINHAND   = 20,
            WEAPON_SKILL_OFFHAND    = 21,
            WEAPON_SKILL_RANGED     = 22,
            EXPERTISE               = 23,
            ARMOR_PENETRATION       = 24
        },

        --- Unit Modifier Statistics
        -- These affect base character attributes and resources
        UNIT_MODS = {
            STAT_STRENGTH           = 0,
            STAT_AGILITY            = 1,
            STAT_STAMINA            = 2,
            STAT_INTELLECT          = 3,
            STAT_SPIRIT             = 4,
            HEALTH                  = 5,
            MANA                    = 6,
            RAGE                    = 7,
            FOCUS                   = 8,
            ENERGY                  = 9,
            HAPPINESS               = 10,
            RUNE                    = 11,
            RUNIC_POWER             = 12,
            ARMOR                   = 13,
            RESISTANCE_HOLY         = 14,
            RESISTANCE_FIRE         = 15,
            RESISTANCE_NATURE       = 16,
            RESISTANCE_FROST        = 17,
            RESISTANCE_SHADOW       = 18,
            RESISTANCE_ARCANE       = 19,
            ATTACK_POWER            = 20,
            ATTACK_POWER_RANGED     = 21,
            DAMAGE_MAINHAND         = 22,
            DAMAGE_OFFHAND          = 23,
            DAMAGE_RANGED           = 24,
        },

        --- Aura-based Bonuses
        -- Custom aura IDs for special bonuses like loot, reputation, and experience
        AURA = {
            LOOT                    = 1900000,
            REPUTATION              = 1900001,
            EXPERIENCE              = 1900002
        }
    }
}
