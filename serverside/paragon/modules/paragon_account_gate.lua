--[[
    Paragon Rework: account-wide gating (companion to LEVEL_LINKED_TO_ACCOUNT)

    With the upstream account mode enabled, every character on the account
    loads the shared paragon level — including characters below 80. Policy:
    sub-80 characters neither collect paragon experience (the hook's
    minimum-level gate handles that) nor BENEFIT from paragon: no allocated
    statistics, no point spending. This module enforces the benefit side at
    the two mediator choke points instead of touching every consumer:

    - OnBeforeUpdatePlayerStatistics: force the apply-pass into a remove-pass
      for sub-80 characters. All milestone/aura/talent/spell reconciles that
      ride OnAfterUpdatePlayerStatistics see apply=false and strip/skip.
      The paragon OBJECT keeps the true account level (never mutated), so
      logout saves write correct values back to account_paragon.
    - OnBeforeClientStatisticsUpdate: blank the change list, so a sub-80
      client cannot allocate points.

    Handlers that fire OUTSIDE the apply chain carry their own inline
    player-level gates (extended talents event 74, Consecration burst
    event 5 in paragon_rework_track.lua, glyph slot Owed()).

    On reaching 80 the regular level-up statistics refresh re-runs the apply
    pass with apply=true and everything activates, banking pays out, etc.
]]

local Config = require("paragon_config")

local function MinLevel()
    return tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
end

-- MEDIATOR RETURN CONTRACT (mediator.lua:85-87): the dispatcher captures only
-- the FIRST return value of a subscriber; returning multiple values silently
-- drops all but the first. A returned TABLE is treated as the positional
-- returns array — the only working way to override later positions (like the
-- apply flag at position 3).

RegisterMediatorEvent("OnBeforeUpdatePlayerStatistics", function(player, paragon, apply)
    local ok, ret = pcall(function()
        if player and apply and player:GetLevel() < MinLevel() then
            return { player, paragon, false }
        end
    end)
    if ok then
        return ret
    end
end)

RegisterMediatorEvent("OnBeforeClientStatisticsUpdate", function(player, paragon, data)
    local ok, ret = pcall(function()
        if player and player:GetLevel() < MinLevel() then
            return { paragon, {} }
        end
    end)
    if ok then
        return ret
    end
end)

-- ============================================================================
-- SUB-80 CLIENT PRESENTATION
-- ============================================================================

-- The client renders lock state purely from the pushed paragon level: the
-- reward track nodes, talent mask, glyph socket and exp bar all key off it.
-- For sub-80 characters, replace the paragon object used by the login push
-- with a read-only proxy reporting level 0 / no points, so the whole window
-- reads locked. The REAL object (and its saves) is never touched; on
-- reaching 80 the live level/xp/points pushes use the real object and the
-- client unlocks on the spot.
local function MaskedParagon(paragon)
    return setmetatable({
        GetLevel = function() return 0 end,
        GetExperience = function() return 0 end,
        GetExperienceForNextLevel = function() return 100 end,
        GetPoints = function() return 0 end,
    }, { __index = paragon })
end

RegisterMediatorEvent("OnBeforeClientLoadRequest", function(player, paragon)
    local ok, ret = pcall(function()
        if player and paragon and player:GetLevel() < MinLevel() then
            return { MaskedParagon(paragon) }
        end
    end)
    if ok then
        return ret
    end
end)

print("[Paragon] Rework: account-wide gate module loaded")
