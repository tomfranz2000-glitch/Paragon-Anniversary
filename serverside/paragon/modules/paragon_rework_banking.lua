--[[
    Paragon Rework: pre-80 banking of one-time rewards (local module, see
    "Paragon Progression Design.md")

    One-time XP rewards earned below the paragon minimum level (achievements,
    for now) accrue in acore_ale.paragon_banked_experience and are paid out as
    a single lump sum through the normal level-up pipeline when the character
    reaches the minimum level. Forward-only: nothing is granted retroactively
    for achievements earned before this module shipped.
]]

local Config = require("paragon_config")
local Constant = require("paragon_constant")
local Hook = require("paragon_hook")

local BANK_TABLE = Constant.DB_NAME .. ".paragon_banked_experience"

local function MinLevel()
    return tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
end

-- ============================================================================
-- ACCRUAL (below minimum level)
-- ============================================================================

RegisterMediatorEvent("OnBeforeAchievementExperience", function(player, achievement, paragon)
    if player:GetLevel() >= MinLevel() then
        return
    end

    local value = ParagonRework_AchievementValue(achievement:GetId())
    if not value or value <= 0 then
        return
    end

    CharDBExecute(string.format(
        "INSERT INTO %s (guid, amount) VALUES (%d, %d) ON DUPLICATE KEY UPDATE amount = amount + %d;",
        BANK_TABLE, player:GetGUIDLow(), value, value))
end)

-- ============================================================================
-- PAYOUT (on reaching minimum level)
-- ============================================================================

local function PayOut(player, banked)
    local paragon = player:GetData("Paragon")
    if not paragon then
        return
    end

    -- Ensure the curved cost is in place before cascading level-ups
    if ParagonRework_CurveCost then
        local expected = ParagonRework_CurveCost(paragon:GetLevel())
        if paragon:GetExperienceForNextLevel() ~= expected then
            paragon:SetExperienceForNextLevel(expected)
        end
    end

    paragon = Mediator.On("OnUpdatePlayerExperience", {
        arguments = { player, paragon, banked },
        defaults = { paragon },
    })

    player:SendServerResponse(Hook.Addon.Prefix, 1, paragon:GetLevel())
    player:SendServerResponse(Hook.Addon.Prefix, 2, paragon:GetExperience(), paragon:GetExperienceForNextLevel())
    player:SendServerResponse(Hook.Addon.Prefix, 4, paragon:GetPoints())
    player:SetData("Paragon", paragon)

    player:SendBroadcastMessage(string.format(
        "|cff00ff00[Paragon]|r Your banked achievement rewards paid out %d paragon experience!", banked))

    CharDBExecute(string.format("DELETE FROM %s WHERE guid = %d;", BANK_TABLE, player:GetGUIDLow()))
end

local function OnLevelChange(event, player, old_level)
    local min_level = MinLevel()
    if min_level <= 0 or player:GetLevel() < min_level or old_level >= min_level then
        return
    end

    local guid_low = player:GetGUIDLow()
    CharDBQueryAsync(string.format(
        "SELECT amount FROM %s WHERE guid = %d;", BANK_TABLE, guid_low),
        function(results)
            if not results then
                return
            end
            local banked = results:GetUInt32(0)
            if banked <= 0 then
                return
            end

            -- Re-resolve the player: the async callback may run on a later
            -- tick and the original reference must not be trusted.
            local target = GetPlayerByGUID(GetPlayerGUID(guid_low))
            if target then
                PayOut(target, banked)
            end
            -- If they logged out mid-query the bank row simply survives
            -- for the next time they are online past the threshold.
        end)
end

RegisterPlayerEvent(13, OnLevelChange)

-- Backstop: pay any surviving bank at login for characters already past the
-- threshold (e.g. logged out mid-payout). PayOut skips silently if paragon
-- data has not finished its async load yet; the row survives for next login.
local function OnLogin(event, player)
    local min_level = MinLevel()
    if min_level <= 0 or player:GetLevel() < min_level then
        return
    end

    local guid_low = player:GetGUIDLow()
    CharDBQueryAsync(string.format(
        "SELECT amount FROM %s WHERE guid = %d;", BANK_TABLE, guid_low),
        function(results)
            if not results then
                return
            end
            local banked = results:GetUInt32(0)
            if banked <= 0 then
                return
            end
            local target = GetPlayerByGUID(GetPlayerGUID(guid_low))
            if target then
                PayOut(target, banked)
            end
        end)
end

RegisterPlayerEvent(3, OnLogin)

print("[Paragon] Rework: pre-80 banking module loaded")
