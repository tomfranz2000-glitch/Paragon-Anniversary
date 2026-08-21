--[[
    Paragon Admin Commands (local test module, not part of upstream)

    GM commands for testing paragon progression without grinding:

        .paragon addxp <amount>     - grant raw paragon experience (runs the
                                      normal level-up pipeline, cascades levels)
        .paragon addlevel <n>       - grant n paragon levels (fed through the
                                      XP pipeline so points stay consistent)
        .paragon addpoints <n>      - grant spendable points directly
        .paragon setlevel <n>       - jump the paragon level to n, up OR
                                      down (XP resets to 0/next). Downward:
                                      Paragon:SetLevel publishes the normal
                                      level-change event, so learned-spell
                                      markers, special auras and bonus
                                      talents reconcile immediately; the
                                      per-session stat milestones only
                                      strip on the next relog.

    Requires GM rank 3+. State persists on logout like normal gains.
]]

local Hook = require("paragon_hook")

local GM_RANK_REQUIRED = 3

--- Push current paragon state to the client UI (level, xp, points),
--- mirroring what paragon_hook.UpdatePlayerExperience does after a gain.
local function SyncClient(player, paragon)
    player:SendServerResponse(Hook.Addon.Prefix, 1, paragon:GetLevel())
    player:SendServerResponse(Hook.Addon.Prefix, 2, paragon:GetExperience(), paragon:GetExperienceForNextLevel())
    player:SendServerResponse(Hook.Addon.Prefix, 4, paragon:GetPoints())
    player:SetData("Paragon", paragon)
end

--- Grant raw experience through the standard Mediator pipeline.
local function GrantExperience(player, paragon, amount)
    paragon = Mediator.On("OnUpdatePlayerExperience", {
        arguments = { player, paragon, amount },
        defaults = { paragon },
    })
    return paragon
end

local function OnCommand(event, player, command)
    if not player or not command then
        return
    end

    local sub, arg = command:match("^paragon%s+(%S+)%s*(%S*)$")
    if not sub then
        return
    end

    if player:GetGMRank() < GM_RANK_REQUIRED then
        player:SendBroadcastMessage("|cffff0000[Paragon]|r GM rank " .. GM_RANK_REQUIRED .. " required.")
        return false
    end

    local paragon = player:GetData("Paragon")
    if not paragon then
        player:SendBroadcastMessage("|cffff0000[Paragon]|r No paragon data yet - open the Paragon window once, then retry.")
        return false
    end

    local amount = tonumber(arg)

    if sub == "addxp" then
        if not amount or amount <= 0 then
            player:SendBroadcastMessage("|cffff0000[Paragon]|r Usage: .paragon addxp <amount>")
            return false
        end
        paragon = GrantExperience(player, paragon, amount)
        SyncClient(player, paragon)
        player:SendBroadcastMessage(("|cff00ff00[Paragon]|r +%d XP -> level %d (%d/%d XP, %d points)."):format(
            amount, paragon:GetLevel(), paragon:GetExperience(), paragon:GetExperienceForNextLevel(), paragon:GetPoints()))
        return false

    elseif sub == "addlevel" then
        if not amount or amount <= 0 then
            player:SendBroadcastMessage("|cffff0000[Paragon]|r Usage: .paragon addlevel <n>")
            return false
        end
        for _ = 1, amount do
            local missing = paragon:GetExperienceForNextLevel() - paragon:GetExperience()
            paragon = GrantExperience(player, paragon, missing)
        end
        SyncClient(player, paragon)
        player:SendBroadcastMessage(("|cff00ff00[Paragon]|r +%d level(s) -> level %d (%d points available)."):format(
            amount, paragon:GetLevel(), paragon:GetPoints()))
        return false

    elseif sub == "setlevel" then
        if not amount or amount < 1 then
            player:SendBroadcastMessage("|cffff0000[Paragon]|r Usage: .paragon setlevel <n> (n >= 1)")
            return false
        end
        local old = paragon:GetLevel()
        paragon:SetLevel(amount)
        paragon:SetExperience(0)
        SyncClient(player, paragon)
        player:SendBroadcastMessage(("|cff00ff00[Paragon]|r Level %d -> %d (%d points available).%s"):format(
            old, paragon:GetLevel(), paragon:GetPoints(),
            amount < old and " Relog to fully strip session stat milestones." or ""))
        return false

    elseif sub == "addpoints" then
        if not amount or amount <= 0 then
            player:SendBroadcastMessage("|cffff0000[Paragon]|r Usage: .paragon addpoints <n>")
            return false
        end
        paragon:AddPoints(amount)
        SyncClient(player, paragon)
        player:SendBroadcastMessage(("|cff00ff00[Paragon]|r +%d points (%d available)."):format(
            amount, paragon:GetPoints()))
        return false
    end

    player:SendBroadcastMessage("|cffff0000[Paragon]|r Unknown subcommand. Use: addxp / addlevel / addpoints / setlevel")
    return false
end

RegisterPlayerEvent(42, OnCommand)
