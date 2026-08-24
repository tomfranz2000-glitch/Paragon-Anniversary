--[[
    Paragon Anniversary Experience Module

    Central module that manages:
    1. Experience gains with automatic cascading level-ups
    2. Experience multipliers based on player conditions
    3. Level-up notifications and effects
    4. Experience tracking and logging
    5. Statistic validation and application effects

    Architecture:
    - ProcessMultipleLevelUps: Handles cascading level-ups from experience gains
    - OnUpdatePlayerExperience: Core handler processing experience with type conversion
    - OnExperienceCalculated: Adjusts experience based on player level/conditions
    - OnParagonLevelChanged: Reacts to level-up events

    This module demonstrates business logic externalization via Mediator.
    All functionality is implemented as event handlers rather than hard-coded
    in paragon_hook.lua, enabling easy customization without modifying core hooks.

    Registered mediator events:
    - OnUpdatePlayerExperience: Process experience and handle level-ups (REQUIRED)
    - OnExperienceCalculated: Adjust XP based on player conditions
    - OnParagonLevelChanged: React to level-up events

    @module paragon_anniversary
    @author iThorgrim
    @license AGL v3
]]

local Config = require("paragon_config")

-- ============================================================================
-- LEVEL-UP PROCESSING
-- ============================================================================

---
--- Processes cascading level-ups when experience exceeds the threshold.
---
--- Handles successive level-ups by:
--- 1. Accumulating total experience (current + gained)
--- 2. Looping through each level's threshold
--- 3. Subtracting experience from the threshold and incrementing level
--- 4. Recalculating experience required for the new level
--- 5. Returning remaining experience after all level-ups
---
--- Example: Level 1 with 49/50 XP gains 150 XP
--- - Total: 49 + 150 = 199 XP
--- - Subtract 50 (level 2 threshold): 199 - 50 = 149, Level → 2
--- - Subtract 100 (level 3 threshold): 149 - 100 = 49, Level → 3
--- - Final state: Level 3 with 49/150 XP remaining
---
--- @param paragon The paragon instance to update
--- @param gained_experience The amount of experience gained
--- @return paragon The updated paragon instance
--- @return levels_gained The number of levels gained
---
local function ProcessMultipleLevelUps(paragon, gained_experience)
    if gained_experience <= 0 then
        return paragon, 0
    end

    -- Accumulate total experience
    local total_experience = paragon:GetExperience() + gained_experience
    local base_max_experience = tonumber(Config:GetByField("BASE_MAX_EXPERIENCE")) or 30000
    local levels_gained = 0

    -- Process level-ups while experience exceeds current level's threshold
    while total_experience >= paragon:GetExperienceForNextLevel() and base_max_experience > 0 do
        total_experience = total_experience - paragon:GetExperienceForNextLevel()
        paragon:AddLevel(1)
        levels_gained = levels_gained + 1
    end

    -- Set remaining experience after all level-ups
    paragon:SetExperience(total_experience)

    return paragon, levels_gained
end

-- ============================================================================
-- EXPERIENCE PROCESSING WITH MEDIATOR INTEGRATION
-- ============================================================================

---
--- Main process for handling experience gains with automatic level-ups.
---
--- Entry point for experience updates, delegates to ProcessMultipleLevelUps
--- to handle multiple consecutive level-ups.
---
--- Extensibility mechanisms via Mediator:
--- - Allows experience adjustment before processing
--- - Allows custom effects after level-ups
--- - Allows logging and notifications
---
--- @param player The player object receiving the experience
--- @param paragon The paragon instance to update
--- @param specific_experience The amount of experience to add
--- @return paragon The updated paragon instance
---
local function OnUpdatePlayerExperience(player, paragon, specific_experience)
    -- This is the final mutation choke point for every normal XP source.
    -- The main hook checks the minimum too, but party shares, collection
    -- rewards and future modules may raise this mediator event directly.
    -- Enforcing the knob here prevents those paths from bypassing the gate.
    local min_level = tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
    if player and player:GetLevel() < min_level then
        return paragon
    end

    -- Convert to number if received as string from database
    if type(specific_experience) == "string" then
        specific_experience = tonumber(specific_experience)
    end

    if not paragon or not specific_experience or specific_experience <= 0 then
        return paragon
    end

    -- Process cascading level-ups
    paragon, levels_gained = ProcessMultipleLevelUps(paragon, specific_experience)

    -- Store metadata for other handlers
    if paragon then
        paragon._last_levels_gained = levels_gained
        paragon._last_exp_gained = specific_experience
    end

    return paragon
end

-- ============================================================================
-- EXPERIENCE MULTIPLIERS (HOOK)
-- ============================================================================

---
--- Internal: Calculates experience multiplier based on paragon level.
---
--- Applies level-based multipliers for scaling difficulty and progression speed:
--- - Low-level paragons (< LOW_LEVEL_THRESHOLD) get EXPERIENCE_MULTIPLIER_LOW_LEVEL bonus
--- - High-level paragons (> HIGH_LEVEL_THRESHOLD) get EXPERIENCE_MULTIPLIER_HIGH_LEVEL penalty
--- - Mid-level paragons receive no multiplier (1.0)
---
--- Configuration keys:
--- - LOW_LEVEL_THRESHOLD: Paragon level below which bonus applies (default: 5)
--- - HIGH_LEVEL_THRESHOLD: Paragon level above which penalty applies (default: 100)
--- - EXPERIENCE_MULTIPLIER_LOW_LEVEL: Bonus multiplier (default: 1.5)
--- - EXPERIENCE_MULTIPLIER_HIGH_LEVEL: Penalty multiplier (default: 0.8)
---
--- @param paragon The paragon instance
--- @return The experience multiplier to apply (1.0 for no change)
---
local function GetExperienceMultiplier(paragon)
    if not paragon then
        return 1.0
    end

    local paragon_level = paragon:GetLevel()
    local low_threshold = tonumber(Config:GetByField("LOW_LEVEL_THRESHOLD")) or 5
    local high_threshold = tonumber(Config:GetByField("HIGH_LEVEL_THRESHOLD")) or 100

    -- Apply low-level bonus (early progression)
    if paragon_level < low_threshold then
        local multiplier = tonumber(Config:GetByField("EXPERIENCE_MULTIPLIER_LOW_LEVEL")) or 1.5
        return multiplier
    end

    -- Apply high-level penalty (late progression scaling)
    if paragon_level > high_threshold then
        local multiplier = tonumber(Config:GetByField("EXPERIENCE_MULTIPLIER_HIGH_LEVEL")) or 0.8
        return multiplier
    end

    -- No multiplier for mid-level paragons
    return 1.0
end

---
--- Adjusts experience based on paragon level and configured multipliers.
---
--- Applies bonuses/penalties based on:
--- - Paragon's current level (via GetExperienceMultiplier)
--- - Configured thresholds and multiplier values
---
--- @param player The player object
--- @param paragon The paragon instance
--- @param source_type The source type of experience
--- @param specific_experience The calculated experience value
--- @return The modified experience value
---
local function OnExperienceCalculated(player, paragon, source_type, specific_experience)
    -- Modifier subscribers compose sequentially. Return nil when this module
    -- has no opinion so the current value passes to the next subscriber.
    if not player or not paragon then
        return
    end

    -- Achievement, profession mastery, quest, and collectible awards are
    -- exact flat rewards. The common hook bypasses this event for them; retain
    -- this guard as defense in depth for direct/custom event dispatches.
    if source_type == 2 or source_type == 3 or source_type == 4
            or source_type == 8 then
        return
    end

    -- Get the experience multiplier based on paragon level
    local multiplier = GetExperienceMultiplier(paragon)

    -- Apply multiplier to experience
    if multiplier ~= 1.0 then
        return specific_experience * multiplier
    end
end

-- ============================================================================
-- LEVEL-UP NOTIFICATIONS (HOOK)
-- ============================================================================

---
--- Tracks level changes for notification handling.
---
--- Stores level change metadata in paragon instance for the main hook to process.
--- The main hook (paragon_hook.lua) has access to the player object and will
--- handle notifications based on this stored data.
---
--- @param paragon The paragon instance
--- @param old_level The previous level
--- @param new_level The new level
---
local function OnParagonLevelChanged(player, paragon, old_level, new_level)
    if not paragon or old_level >= new_level then
        return
    end

    -- Store level change metadata for main hook to process
    -- The hook will use this to send notifications to the player
    paragon._last_level_change = {
        old_level = old_level,
        new_level = new_level,
        levels_gained = new_level - old_level
    }

    player:SendNotification("You win a new Paragon level !")
end

-- ============================================================================
-- MODULE REGISTRATION
-- ============================================================================

---
--- Core required hook: Processes experience gains with cascading level-ups.
--- This is mandatory for paragon system functionality.
---
RegisterMediatorEvent("OnUpdatePlayerExperience", OnUpdatePlayerExperience)

---
--- Optional hooks: Extend paragon behavior via Mediator without modifying core hooks.
--- These demonstrate the extensibility pattern and can be activated as needed.
---

-- Experience modifier: Adjusts XP based on player conditions
RegisterMediatorEvent("OnExperienceCalculated", OnExperienceCalculated)

-- Level-up notifications: React to level changes
RegisterMediatorEvent("OnParagonLevelChanged", OnParagonLevelChanged)

print("[Paragon] Paragon Anniversary Experience module loaded")
