--[[
    Paragon_TargetLevel.lua
    Client-side module for displaying target's Paragon level

    Displays the Paragon level of the player's current target next to the
    target portrait. Only shows when targeting another player.

    Features:
    - Automatic target change detection
    - Server communication to fetch target level
    - UI integration with TargetFrame

    @module Paragon_TargetLevel
    @author Paragon Team
]]

-- ============================================================================
-- TARGET CHANGE DETECTION
-- ============================================================================

---
--- Requests target's Paragon level from server when target changes
--- Called automatically by the OnUpdate handler
---
local function RequestTargetParagonLevel()
    -- Check if player has a target
    if not UnitExists("target") then
        -- No target, hide the frame
        if ParagonTargetLevel then
            ParagonTargetLevel:SetAlpha(0)
        end
        return
    end

    -- Check if target is a player
    if not UnitIsPlayer("target") then
        if ParagonTargetLevel then
            ParagonTargetLevel:SetAlpha(0)
        end
        return
    end

    -- Request target's Paragon level from server
    SendClientRequest("ParagonAnniversary", 6)
end

---
--- OnUpdate handler for ParagonTargetLevel frame
--- Detects target changes and requests level update
--- @param self Frame The ParagonTargetLevel frame
--- @param elapsed number Time since last update
---
function ParagonTargetLevel_OnUpdate(self, elapsed)
    -- Check if target has changed
    local currentTargetGUID = UnitGUID("target")
    if currentTargetGUID ~= self.lastTargetGUID then
        self.lastTargetGUID = currentTargetGUID
        RequestTargetParagonLevel()
    end
end

---
--- OnLoad handler for ParagonTargetLevel frame
--- Initializes the frame state
--- @param self Frame The ParagonTargetLevel frame
---
function ParagonTargetLevel_OnLoad(self)
    self.lastTargetGUID = nil
    self:SetAlpha(0)
end

function ParagonTargetLevel_OnHide(self)
    self.lastTargetGUID = nil
end