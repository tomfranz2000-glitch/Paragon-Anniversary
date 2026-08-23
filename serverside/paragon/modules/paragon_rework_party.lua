--[[
    Paragon Rework: native group kill credit (local module, see
    "Paragon Progression Design.md")

    ALE player event 75 is emitted by KillRewarder once for every group member
    credited by the core. It is independent of the killing unit, so playerbot,
    pet, guardian and totem killing blows follow the same path. It also fires
    when ordinary XP is zero because the victim is gray.

    Each recipient goes through paragon_hook's complete experience pipeline.
    Bots and sub-80 players are rejected there, after group size has already
    been used to calculate the share in paragon_rework_sources.lua.
]]

local Hook = require("paragon_hook")

local function OnKillReward(event, player, creature, isDungeon, participantCount, isRaid)
    if not player or not creature or not player:IsAlive() then
        return
    end

    Hook.OnPlayerKillReward(event, player, creature, isDungeon, participantCount, isRaid)
end

RegisterPlayerEvent(75, OnKillReward)

print("[Paragon] Rework: native group kill credit module loaded")
