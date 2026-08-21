--[[
    Paragon Level-Up Animation Module

    Provides visual feedback when players gain Paragon levels by casting
    a configurable spell effect and automatically removing it after a short duration.

    Features:
    - Configurable animation spell ID via database config
    - Automatic spell removal after 3 seconds
    - Level-up detection through Mediator events
    - Database migration for default configuration

    Architecture:
    - OnAfterMigrationExecute: Inserts default spell ID during system initialization
    - OnParagonLevelChanged: Triggers animation when player levels up
    - Timed aura removal using player:RegisterEvent

    Registered mediator events:
    - OnAfterMigrationExecute: Initialize default configuration (DEFERRED)
    - OnParagonLevelChanged: React to level-up events

    @module paragon_levelup_animation
    @author Paragon Team
    @license AGL v3
]]

local Config = require("paragon_config")
local Constants = require("paragon_constant")

local sf = string.format

-- ============================================================================
-- DATABASE MIGRATION
-- ============================================================================

---
--- Inserts default level-up animation configuration during system initialization.
---
--- This handler is triggered by the deferred OnAfterMigrationExecute event,
--- allowing it to execute after all modules are loaded and registered.
---
--- Mediator Event: OnAfterMigrationExecute (deferred)
---
--- @param _ Repository The repository instance (unused)
---
local function OnAfterMigrationExecute(_)
    -- Insert default spell ID for level-up animation (Spell ID: 64785)
    CharDBExecute(sf(
        "INSERT IGNORE INTO %s.paragon_config (field, value) VALUES ('LEVEL_UP_ANIMATION', '64785');",
        Constants.DB_NAME
    ))
end

-- Register for deferred migration event
RegisterMediatorEvent("OnAfterMigrationExecute", OnAfterMigrationExecute)

-- ============================================================================
-- ANIMATION HANDLERS
-- ============================================================================

---
--- Removes the level-up animation aura from the player.
---
--- This function is registered as a timed event and automatically executes
--- 3 seconds after the level-up animation is applied.
---
--- @param _ number Event ID (unused)
--- @param _ number Delay (unused)
--- @param _ number Repeats (unused)
--- @param player Player The player object to remove the aura from
---
local function RemoveAura(_, _, _, player)
    local spell_id = tonumber(Config:GetByField("LEVEL_UP_ANIMATION"))
    if spell_id then
        player:RemoveAura(spell_id)
    end
end

---
--- Handles Paragon level changes and triggers level-up animation.
---
--- When a player gains a Paragon level (new_level > old_level), this handler:
--- 1. Retrieves the configured animation spell ID
--- 2. Casts the spell on the player (triggered = true, instant cast)
--- 3. Schedules automatic removal of the aura after 3000ms
---
--- Mediator Event: OnParagonLevelChanged
---
--- @param player Player The player object that leveled up
--- @param _ Paragon The paragon instance (unused)
--- @param old_level number The player's previous Paragon level
--- @param new_level number The player's new Paragon level
---
local function OnParagonLevelChanged(player, _, old_level, new_level)
    -- Only trigger animation when leveling up (not down)
    if new_level > old_level then
        local spell_id = tonumber(Config:GetByField("LEVEL_UP_ANIMATION"))
        if not spell_id then
            return
        end

        -- Cast the level-up animation spell (instant, triggered)
        player:CastSpell(player, spell_id, true)

        -- Schedule aura removal after 3 seconds (3000ms, execute once)
        player:RegisterEvent(RemoveAura, 3000, 1)
    end
end

-- Register for level change events
RegisterMediatorEvent("OnParagonLevelChanged", OnParagonLevelChanged)

-- ============================================================================
-- MODULE INITIALIZATION
-- ============================================================================

print("[Paragon] Paragon Anniversary Level Animation module loaded")