--[[
    Paragon Configuration Manager

    Singleton service that loads and manages all paragon configuration data
    from the database. Provides convenient accessors for categories, statistics,
    and experience rewards across all sources.

    Data Structure:
    - Categories: Organized groups of related statistics
    - Statistics: Individual paragon stats (UNIT_MODS, COMBAT_RATING, AURA)
    - Experience: Source-specific rewards (creatures, achievements, skills, quests)
    - General Config: Key-value pairs for system settings

    Initialization:
    - Loads from Repository on first instantiation
    - Caches all data in memory
    - Acts as Singleton (single global instance)

    @class Config
    @author iThorgrim
    @license AGL v3
]]

local Repository = require("paragon_repository")

local Config = Object:extend()
local Instance = nil

-- ============================================================================
-- CONSTRUCTOR
-- ============================================================================

---
--- Initializes the configuration service by loading all paragon data.
---
--- This is called once when the singleton instance is first created.
--- Loads from database:
--- 1. Categories and their statistics
--- 2. Experience rewards for all sources
--- 3. General configuration key-value pairs
---
function Config:new()
    -- Build category / stat data
    self:BuildParagonData()

    -- Build experience data
    self:BuildParagonExperience()

    -- Load general configuration
    local config_data = Repository:GetConfig()
    self.config = config_data or {}
end

-- ============================================================================
-- GENERAL CONFIGURATION
-- ============================================================================

---
--- Retrieves a configuration value by field name.
---
--- Configuration values are system settings stored in the database.
--- Examples: POINTS_PER_LEVEL, BASE_MAX_EXPERIENCE, etc.
---
--- @param field The configuration field name to retrieve
--- @return The configuration value (string), or nil if not found
---
function Config:GetByField(field)
    if not field or not self.config then
        return nil
    end

    return self.config[field] or nil
end

-- ============================================================================
-- CATEGORIES & STATISTICS ACCESSORS
-- ============================================================================

---
--- Retrieves all paragon categories with their statistics.
---
--- @return Table mapping category_id to {name, statistics}
---
function Config:GetCategories()
    return self.categories
end

---
--- Retrieves a specific category by its ID.
---
--- @param id The category ID to retrieve
--- @return Table with category data {name, statistics}, or nil if not found
---
function Config:GetByCategoryId(id)
    if not id or not self.categories then
        return nil
    end

    return self.categories[id] or nil
end

---
--- Retrieves a specific statistic by its ID across all categories.
---
--- This performs a linear search through all categories to find the stat.
--- For performance-critical code, prefer caching the result.
---
--- @param stat_id The statistic ID to search for
--- @return Table with stat data, or nil if not found
---
function Config:GetByStatId(stat_id)
    if not stat_id or not self.categories then
        return nil
    end

    for _, cat_data in pairs(self.categories) do
        if cat_data.statistics then
            local stat_data = cat_data.statistics[stat_id]
            if stat_data then
                return stat_data
            end
        end
    end

    return nil
end

---
--- Retrieves the category ID that contains a specific statistic.
---
--- @param stat_id The statistic ID to search for
--- @return The category ID containing the stat, or nil if not found
---
function Config:GetCategoryByStatId(stat_id)
    if not stat_id or not self.categories then
        return nil
    end

    for cat_id, cat_data in pairs(self.categories) do
        if cat_data.statistics then
            local stat_data = cat_data.statistics[stat_id]
            if stat_data then
                return cat_id
            end
        end
    end

    return nil
end

-- ============================================================================
-- DATA BUILDING
-- ============================================================================

---
--- Builds the categories and statistics data structure from database.
---
--- Combines category metadata with their associated statistics into a
--- unified in-memory structure for fast access.
---
function Config:BuildParagonData()
    local statistics_data = Repository:GetConfigStatistics()
    local categories_data = Repository:GetConfigCategories()

    if not categories_data then
        self.categories = {}
        return
    end

    local data = {}
    for cat_id, cat_name in pairs(categories_data) do
        data[cat_id] = {
            name = cat_name,
            statistics = statistics_data and statistics_data[cat_id] or {}
        }
    end

    self.categories = data
end

---
--- Builds the experience rewards data structure from database.
---
--- Loads experience reward tables for all sources (creatures, achievements,
--- skills, quests) into a unified structure for quick lookups.
---
function Config:BuildParagonExperience()
    self.experience = {
        creature = Repository:GetConfigCreatureExperience() or {},
        achievement = Repository:GetConfigAchievementExperience() or {},
        skill = Repository:GetConfigSkillExperience() or {},
        quest = Repository:GetConfigQuestExperience() or {}
    }
end

-- ============================================================================
-- EXPERIENCE REWARDS ACCESSORS
-- ============================================================================

---
--- Internal: Retrieves experience reward for a specific source type and ID.
---
--- @param source_type The source type ('creature', 'achievement', 'skill', 'quest')
--- @param entry_id The entry/ID to look up
--- @return The experience reward value, or nil if not configured
---
local function GetExperienceForSource(self, source_type, entry_id)
    if not source_type or not entry_id then
        return nil
    end

    local source_data = self.experience[source_type]
    if not source_data then
        return nil
    end

    return source_data[entry_id] or nil
end

---
--- Retrieves the paragon experience reward for a creature by entry ID.
---
--- @param creature_entry The creature entry ID
--- @return The experience reward value, or nil if not configured
---
function Config:GetCreatureExperience(creature_entry)
    return GetExperienceForSource(self, "creature", creature_entry)
end

---
--- Retrieves the paragon experience reward for an achievement by ID.
---
--- @param achievement_id The achievement ID
--- @return The experience reward value, or nil if not configured
---
function Config:GetAchievementExperience(achievement_id)
    return GetExperienceForSource(self, "achievement", achievement_id)
end

---
--- Retrieves the paragon experience reward for a skill by skill ID.
---
--- @param skill_id The skill ID
--- @return The experience reward value, or nil if not configured
---
function Config:GetSkillExperience(skill_id)
    return GetExperienceForSource(self, "skill", skill_id)
end

---
--- Retrieves the paragon experience reward for a quest by quest ID.
---
--- @param quest_id The quest ID
--- @return The experience reward value, or nil if not configured
---
function Config:GetQuestExperience(quest_id)
    return GetExperienceForSource(self, "quest", quest_id)
end

-- ============================================================================
-- SINGLETON MANAGEMENT
-- ============================================================================

---
--- Gets the singleton instance of the Config service.
---
--- Creates the instance on first call. All subsequent calls return the same
--- cached instance.
---
--- @return The Config singleton instance
---
function Config:GetInstance()
    if not Instance then
        Instance = Config()
    end

    return Instance
end

return Config:GetInstance()