--[[
    Paragon Class

    Represents a character's paragon progression system.
    Manages level, experience, statistics, and available points.

    Design Philosophy:
    ==================
    This class manages the core paragon data and logic. It triggers Mediator
    events when state changes occur, allowing other modules to react without
    tight coupling.

    Key Features:
    - Automatic recalculation of experience thresholds on level change
    - Automatic recalculation of available points when stats change
    - Input validation on all setters
    - Mediator events for state change tracking
    - Method chaining for fluent API

    Mediator Events:
    ================
    - OnParagonLevelChanged: (paragon, old_level, new_level)
    - OnParagonExperienceChanged: (paragon, old_exp, new_exp)
    - OnParagonPointsChanged: (paragon, old_points, new_points)
    - OnParagonStatChanged: (paragon, old_stat_value, new_stat_value)

    @class Paragon
    @author iThorgrim
    @license AGL v3
]]

local Repository = require("paragon_repository")
local Config = require("paragon_config")

local Paragon = Object:extend()

-- Global Mediator is available (initialized before this module loads)
-- Used for triggering paragon state change events

-- ============================================================================
-- PRIVATE FUNCTIONS
-- ============================================================================

---
--- Calculates the maximum experience required for a given level.
---
--- Uses BASE_MAX_EXPERIENCE config with fallback to 1000 if not available.
---
--- @param level The paragon level
--- @return The maximum experience required for that level
---
local function CalculateMaxExperienceForLevel(level)
    level = level or tonumber(Config:GetByField("PARAGON_STARTING_LEVEL"))
    local base_max_exp = tonumber(Config:GetByField("BASE_MAX_EXPERIENCE")) or 1000
    return base_max_exp * level
end

---
--- Recalculates available points based on current level and invested statistics.
---
--- Available points = (level × POINTS_PER_LEVEL) - invested_points
---
--- @param paragon The paragon instance to update
---
local function RecalculateAvailablePoints(paragon)
    if not paragon or not paragon.level then
        return
    end

    local used_points = 0
    for _, stat_value in pairs(paragon.statistics) do
        used_points = used_points + stat_value
    end

    -- Get points per level with fallback to 1 if config not available
    local points_per_level = tonumber(Config:GetByField("POINTS_PER_LEVEL")) or 1
    paragon.points = (paragon.level * points_per_level) - used_points
end

-- ============================================================================
-- CONSTRUCTOR
-- ============================================================================

---
--- Initializes a new Paragon instance with default values.
---
--- Initial state:
--- - Level: 1
--- - Experience: 0 / max_experience_for_level_1
--- - Points: 0
--- - Statistics: empty
---
--- @param player_guid The character's GUID to associate with this paragon instance
---
function Paragon:new(player_guid, account_id)
    self.guid = player_guid

    if (Config:GetByField("LEVEL_LINKED_TO_ACCOUNT") == "1") then
        self.account = account_id
    end

    self.level = tonumber(Config:GetByField("PARAGON_STARTING_LEVEL")) or 1
    self.exp = {
        current = tonumber(Config:GetByField("PARAGON_STARTING_EXPERIENCE")) or 0,
        max = CalculateMaxExperienceForLevel(self.level)
    }
    self.points = 0
    self.statistics = {}
end

-- ============================================================================
-- DATABASE OPERATIONS
-- ============================================================================

---
--- Asynchronously loads both level/experience and statistics from the database.
---
--- Routes to correct database table based on LEVEL_LINKED_TO_ACCOUNT configuration:
--- - If enabled (1): Loads from account_paragon table using account_id
--- - If disabled (0): Loads from character_paragon table using character guid
---
--- The callback is invoked after both queries complete.
---
--- @param callback Function to invoke after loading (receives guid, self)
---
function Paragon:Load(callback)
    if (Config:GetByField("LEVEL_LINKED_TO_ACCOUNT") == "1") then
        -- Account-linked mode: load from account_paragon
        Repository:GetParagonByAccountId(self.account, function(data)
            if data and data.level then
                self.level = data.level
                self.exp.current = data.current_experience or 0
                self.exp.max = CalculateMaxExperienceForLevel(self.level)
            end
            self:LoadStats(callback)
        end)
    else
        -- Character-linked mode: load from character_paragon
        Repository:GetParagonByCharacter(self.guid, function(data)
            if data and data.level then
                self.level = data.level
                self.exp.current = data.current_experience or 0
                self.exp.max = CalculateMaxExperienceForLevel(self.level)
            end
            self:LoadStats(callback)
        end)
    end
end

---
--- Saves all paragon data to the database including level, experience, and statistics.
---
--- Persists character paragon progression and all invested statistic points.
--- Routes to correct database table based on LEVEL_LINKED_TO_ACCOUNT configuration:
--- - If enabled (1): Saves to account_paragon table using account_id
--- - If disabled (0): Saves to character_paragon table using character guid
---
function Paragon:Save()
    if (Config:GetByField("LEVEL_LINKED_TO_ACCOUNT") == "1") then
        -- Account-linked mode: save to account_paragon
        Repository:SaveParagonByAccount(self.account, self.level, self.exp.current)
    else
        -- Character-linked mode: save to character_paragon
        Repository:SaveParagonByCharacter(self.guid, self.level, self.exp.current)
    end
    -- Character statistics are always saved using character GUID regardless of mode
    Repository:SaveParagonCharacterStat(self.guid, self.statistics)
end

---
--- Asynchronously loads paragon statistics from database.
---
--- After loading, recalculates available points and invokes callback.
---
--- @param callback Function to invoke after loading (receives guid, self)
---
--- @private
---
function Paragon:LoadStats(callback)
    Repository:GetParagonStatByCharacter(self.guid, function(data)
        if data then
            self.statistics = data
        end

        RecalculateAvailablePoints(self)

        if callback then
            callback(self.guid, self)
        end
    end)
end

-- ============================================================================
-- LEVEL ACCESSORS
-- ============================================================================

---
--- Gets the current paragon level.
---
--- @return The paragon level (≥ 1)
---
function Paragon:GetLevel()
    return self.level
end

---
--- Sets the paragon level to a specific value.
---
--- Automatically recalculates:
--- - Maximum experience for the new level
--- - Available points
---
--- @param level The new level to set
--- @return self For method chaining
---
function Paragon:SetLevel(level)
    if not level or level < 1 then
        return self
    end

    -- Enforce level cap if configured (0 = unlimited)
    local level_cap = tonumber(Config:GetByField("PARAGON_LEVEL_CAP")) or 0
    if level_cap > 0 and level > level_cap then
        level = level_cap
    end

    local previous_level = self.level
    if previous_level ~= level then
        self.level = level
        self.exp.max = CalculateMaxExperienceForLevel(level)
        RecalculateAvailablePoints(self)

        -- Trigger Mediator event after level changes
        if Mediator then
            Mediator.On("OnParagonLevelChanged", {
                arguments = { GetPlayerByGUID(GetPlayerGUID(self.guid)), self, previous_level, level }
            })
        end
    end

    return self
end

---
--- Adds one or more levels to the current paragon level.
---
--- @param levels The number of levels to add (default: 1)
--- @return self For method chaining
---
function Paragon:AddLevel(levels)
    levels = levels or 1
    if levels <= 0 then
        return self
    end

    return self:SetLevel(self.level + levels)
end

-- ============================================================================
-- POINTS ACCESSORS
-- ============================================================================

---
--- Gets the number of available paragon points to invest.
---
--- @return The available points (≥ 0)
---
function Paragon:GetPoints()
    return self.points
end

---
--- Sets the available paragon points.
---
--- @param points The new number of points (should be ≥ 0)
--- @return self For method chaining
---
function Paragon:SetPoints(points)
    if not points or points < 0 then
        return self
    end

    local previous_points = self.points
    if previous_points ~= points then
        self.points = points

        -- Trigger Mediator event after points change
        if Mediator then
            Mediator.On("OnParagonPointsChanged", {
                arguments = { self, previous_points, points }
            })
        end
    end

    return self
end

---
--- Adds points to the available pool.
---
--- @param amount The number of points to add
--- @return self For method chaining
---
function Paragon:AddPoints(amount)
    if not amount or amount <= 0 then
        return self
    end

    return self:SetPoints(self.points + amount)
end

---
--- Subtracts points from the available pool.
---
--- Prevents points from going below zero.
---
--- @param amount The number of points to subtract
--- @return self For method chaining
---
function Paragon:SubtractPoints(amount)
    if not amount or amount <= 0 then
        return self
    end

    local new_points = self.points - amount
    return self:SetPoints(math.max(0, new_points))
end

---
--- Checks if there are available points to invest.
---
--- @return Boolean indicating if points > 0
---
function Paragon:HasAvailablePoints()
    return self.points > 0
end

-- ============================================================================
-- EXPERIENCE ACCESSORS
-- ============================================================================

---
--- Gets the current experience points.
---
--- @return The current experience (0 to exp.max)
---
function Paragon:GetExperience()
    return self.exp.current
end

---
--- Sets the current experience to a specific value.
---
--- Clamps value to prevent going below 0 or above max.
---
--- @param experience The new experience value
--- @return self For method chaining
---
function Paragon:SetExperience(experience)
    if not experience or experience < 0 then
        experience = 0
    end

    -- Prevent experience from exceeding max (should be handled by caller)
    experience = math.min(experience, self.exp.max)

    local previous_exp = self.exp.current
    if previous_exp ~= experience then
        self.exp.current = experience

        -- Trigger Mediator event after experience changes
        if Mediator then
            Mediator.On("OnParagonExperienceChanged", {
                arguments = { self, previous_exp, experience }
            })
        end
    end

    return self
end

---
--- Adds experience points.
---
--- Note: Does NOT handle level-ups. Use the Anniversary module for that.
---
--- @param amount The amount of experience to add
--- @return self For method chaining
---
function Paragon:AddExperience(amount)
    if not amount or amount <= 0 then
        return self
    end

    local new_exp = self.exp.current + amount
    return self:SetExperience(new_exp)
end

---
--- Gets the experience required for the next level.
---
--- @return The maximum experience threshold for current level
---
function Paragon:GetExperienceForNextLevel()
    return self.exp.max
end

---
--- Gets the current progress toward next level as a percentage.
---
--- @return Percentage value (0-100)
---
function Paragon:GetExperienceProgress()
    if self.exp.max == 0 then
        return 0
    end

    return (self.exp.current / self.exp.max) * 100
end

---
--- Sets the experience threshold for current level.
---
--- @param max_experience The new maximum experience value
--- @return self For method chaining
---
function Paragon:SetExperienceForNextLevel(max_experience)
    if not max_experience or max_experience <= 0 then
        return self
    end

    self.exp.max = max_experience
    return self
end

-- ============================================================================
-- STATISTICS ACCESSORS
-- ============================================================================

---
--- Gets all paragon statistics.
---
--- @return Table mapping stat_id to invested value
---
function Paragon:GetStatistics()
    return self.statistics
end

---
--- Gets the invested value for a specific statistic.
---
--- @param stat_id The statistic ID to retrieve
--- @return The invested value (0 if not initialized)
---
function Paragon:GetStatValue(stat_id)
    return self.statistics[stat_id] or 0
end

---
--- Sets the invested value for a specific statistic.
---
--- Initializes the statistic if it doesn't exist.
--- Recalculates available points after change.
---
--- @param stat_id The statistic ID to update
--- @param value The new invested value (≥ 0)
--- @return self For method chaining
---
function Paragon:SetStatValue(stat_id, value)
    if not stat_id or not value or value < 0 then
        return self
    end

    local previous_value = self.statistics[stat_id] or 0
    if previous_value ~= value then
        self.statistics[stat_id] = value
        RecalculateAvailablePoints(self)

        -- Trigger Mediator event after stat changes
        if Mediator then
            Mediator.On("OnParagonStatChanged", {
                arguments = { self, stat_id, previous_value, value }
            })
        end
    end

    return self
end

---
--- Adds to the invested value of a specific statistic.
---
--- @param stat_id The statistic ID to increment
--- @param amount The amount to add
--- @return self For method chaining
---
function Paragon:AddStatValue(stat_id, amount)
    if not stat_id or not amount or amount <= 0 then
        return self
    end

    local current = self:GetStatValue(stat_id)
    return self:SetStatValue(stat_id, current + amount)
end

---
--- Subtracts from the invested value of a specific statistic.
---
--- Prevents value from going below zero.
---
--- @param stat_id The statistic ID to decrement
--- @param amount The amount to subtract
--- @return self For method chaining
---
function Paragon:SubtractStatValue(stat_id, amount)
    if not stat_id or not amount or amount <= 0 then
        return self
    end

    local current = self:GetStatValue(stat_id)
    local new_value = math.max(0, current - amount)

    return self:SetStatValue(stat_id, new_value)
end

---
--- Initializes a statistic (sets to 0 if not already set).
---
--- Used to prepare a statistic for investment.
---
--- @param stat_id The statistic ID to initialize
--- @return self For method chaining
---
function Paragon:InitializeStatistic(stat_id)
    if not stat_id then
        return self
    end

    if self.statistics[stat_id] == nil then
        self.statistics[stat_id] = 0
    end

    return self
end

---
--- Resets all statistics to zero.
---
--- @return self For method chaining
---
function Paragon:ResetStatistics()
    self.statistics = {}
    RecalculateAvailablePoints(self)
    return self
end

---
--- Gets the total number of points invested across all statistics.
---
--- @return The sum of all invested points
---
function Paragon:GetUsedPoints()
    local used = 0
    for _, value in pairs(self.statistics) do
        used = used + value
    end

    return used
end

---
--- Gets the total points available at current level.
---
--- Total = level × POINTS_PER_LEVEL
---
--- @return The total points available for this level
---
function Paragon:GetTotalPointsAvailable()
    local points_per_level = tonumber(Config:GetByField("POINTS_PER_LEVEL"))
    return self.level * points_per_level
end

-- ============================================================================
-- UTILITY METHODS
-- ============================================================================

---
--- Gets the character's GUID associated with this paragon instance.
---
--- @return The character's GUID
---
function Paragon:GetGUID()
    return self.guid
end

---
--- Gets a summary of the current paragon state.
---
--- Useful for debugging and logging.
---
--- @return Table with level, exp_current, exp_max, points, used_points
---
function Paragon:GetState()
    return {
        guid = self.guid,
        level = self.level,
        experience = self.exp.current,
        experience_max = self.exp.max,
        available_points = self.points,
        used_points = self:GetUsedPoints(),
        statistics_count = self:GetStatisticsCount()
    }
end

---
--- Gets the number of statistics that have been invested in.
---
--- @return The count of non-zero statistics
---
function Paragon:GetStatisticsCount()
    local count = 0
    for _ in pairs(self.statistics) do
        count = count + 1
    end

    return count
end

return Paragon
