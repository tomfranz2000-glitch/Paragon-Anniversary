--[[
    Paragon Level-Up Achievement Module

    Provides automatic achievement assignment when players gain Paragon levels.

    Features:
    - Assigns achievements for Paragon levels at specific intervals:
        * Every 10 levels up to level 100
        * Every 25 levels from 125 to 500
    - Ensures each achievement is granted only once
    - Uses O(1) lookup for achievements via a table

    Architecture:
    - _InitAchievementTable: Generates the achievement table with appropriate intervals
    - OnParagonLevelChanged: Assigns achievement when player levels up

    Registered mediator events:
    - OnParagonLevelChanged: Reacts to Paragon level changes

    @module paragon_levelup_achievements
    @author Paragon Team
    @license AGL v3
]]

local Config = require("paragon_config")
local Constants = require("paragon_constant")

local sf = string.format

local Achievements = {}
local AchievementID = 70000

-- ============================================================================
-- ACHIEVEMENT TABLE INITIALIZATION
-- ============================================================================

--- Initializes the achievement table for Paragon levels
--- Levels 10..100 -> every 10
--- Levels 125..500 -> every 25
local function _InitAchievementTable()
    for level = 1, 100, 10 do
        Achievements[level] = AchievementID
        AchievementID = AchievementID + 1
    end

    for level = 125, 500, 25 do
        Achievements[level] = AchievementID
        AchievementID = AchievementID + 1
    end
end

-- Initialize table at module load
_InitAchievementTable()

-- ============================================================================
-- PARAGON LEVEL-UP HANDLER
-- ============================================================================

--- Handles Paragon level changes and assigns achievements.
---
--- If a player reaches a Paragon level that has an associated achievement,
--- and the player hasn't already earned it, it is granted automatically.
---
--- Mediator Event: OnParagonLevelChanged
---
--- @param player Player The player object that leveled up
--- @param _ Paragon The paragon instance (unused)
--- @param old_level number Previous Paragon level
--- @param new_level number New Paragon level
local function OnParagonLevelChanged(player, _, old_level, new_level)
    if new_level > old_level and Achievements[new_level] then
        if not player:HasAchieved(Achievements[new_level]) then
            player:SetAchievement(Achievements[new_level])
        end
    end
end

-- Register for Paragon level change events
RegisterMediatorEvent("OnParagonLevelChanged", OnParagonLevelChanged)

-- ============================================================================
-- MODULE INITIALIZATION
-- ============================================================================

print("[Paragon] Paragon Anniversary Level Achievements module loaded")