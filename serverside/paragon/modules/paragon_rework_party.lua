--[[
    Paragon Rework: party kill credit (local module, see
    "Paragon Progression Design.md")

    Upstream grants paragon kill XP only to the killing player. This module
    grants the same per-member share to every other eligible group member,
    mirroring regular kill-XP distribution (the killer's own share is reduced
    to the split value by paragon_rework_sources.lua).

    Eligibility mirrors the share computation: alive, at/above the paragon
    minimum level, same map, within share range. Members' XP goes through the
    same Mediator level-up pipeline and client sync as any other gain.
]]

local Hook = require("paragon_hook")

local function GrantShare(member, share)
    -- account-wide paragon: this path bypasses the hook's bot gate (it
    -- raises the mediator directly), so bots must be refused here too;
    -- the minimum level is already part of share eligibility
    if member.IsPlayerBot and member:IsPlayerBot() then
        return
    end

    local paragon = member:GetData("Paragon")
    if not paragon then
        return
    end

    -- local patch (milestones 300/325): shares bypass OnExperienceCalculated,
    -- so the collection bonuses apply per RECIPIENT here (kill shares are
    -- creature experience — the achievement exclusion cannot apply)
    if ParagonCollectionXP_Factor then
        share = math.floor(share * ParagonCollectionXP_Factor(member, paragon))
    end

    paragon = Mediator.On("OnUpdatePlayerExperience", {
        arguments = { member, paragon, share },
        defaults = { paragon },
    })

    member:SendServerResponse(Hook.Addon.Prefix, 1, paragon:GetLevel())
    member:SendServerResponse(Hook.Addon.Prefix, 2, paragon:GetExperience(), paragon:GetExperienceForNextLevel())
    member:SendServerResponse(Hook.Addon.Prefix, 4, paragon:GetPoints())
    member:SetData("Paragon", paragon)
end

local function OnKillCreature(event, killer, creature)
    if not killer or not creature then
        return
    end

    -- Killer must be in a group for anyone else to be owed a share; the
    -- share function returns the other eligible members.
    if not killer:GetGroup() then
        return
    end

    local share, others = ParagonRework_ComputeKillShare(killer, creature)
    if not others then
        return
    end

    for _, member in ipairs(others) do
        GrantShare(member, share)
    end
end

RegisterPlayerEvent(7, OnKillCreature)

print("[Paragon] Rework: party kill credit module loaded")
