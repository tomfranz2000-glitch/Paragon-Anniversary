--[[
    Paragon Target Level Module

    Displays the Paragon level of the player's current target in the UI.
    Only works when targeting another player character.

    Features:
    - Server-side hook to retrieve target's Paragon level
    - Client-side UI display near target frame
    - Automatic updates when target changes

    @module paragon_target_level
    @author Paragon Team
]]


-- ============================================================================
-- MODULE CONFIGURATION
-- ============================================================================
local ParagonHook = require("paragon_hook")
ParagonHook.Addon.Functions[6] = "OnParagonClientRequestTargetLevel"

RegisterClientRequests(ParagonHook.Addon, true)

-- ============================================================================
-- SERVER HOOK HANDLERS
-- ============================================================================

---
--- Handles client request to get the Paragon level of their current target.
---
--- Validates that the target exists and is a player, then retrieves their
--- Paragon data and sends the level back to the requesting client.
---
--- @param player The player object making the request
--- @param _ Unused parameter (always nil for addon requests)
--- @return boolean True if target level was sent, false otherwise
---
function OnParagonClientRequestTargetLevel(player, _)
    if not player then
        return false
    end

    -- Get the player's current target
    local target = player:GetSelection()
    if not target then
        -- No target selected, send level 0 to hide UI
        player:SendServerResponse(ParagonHook.Addon.Prefix, 6, 0)
        return false
    end

    -- Check if target is a player (not a creature/NPC)
    if not target:IsPlayer() then
        -- Target is not a player, send level 0 to hide UI
        player:SendServerResponse(ParagonHook.Addon.Prefix, 6, 0)
        return false
    end

    -- Get target's Paragon data
    local target_paragon = target:GetData("Paragon")
    if not target_paragon then
        -- Target has no Paragon data (shouldn't happen but handle gracefully)
        player:SendServerResponse(ParagonHook.Addon.Prefix, 6, 0)
        return false
    end

    -- Send target's Paragon level to the client
    local target_level = target_paragon:GetLevel()
    player:SendServerResponse(ParagonHook.Addon.Prefix, 6, target_level or 0)

    return true
end

-- ============================================================================
-- MODULE INITIALIZATION
-- ============================================================================

print("[Paragon] Paragon Anniversary Target Level module loaded")