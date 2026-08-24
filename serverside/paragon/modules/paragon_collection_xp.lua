--[[
    Paragon Rework: collection experience bonuses (milestones 300 + 325)

    Additive paragon XP bonuses from collections, consolidated into one factor
    so collection state is scanned and rounded once. This modifier composes in
    registration order with independent progression modifiers:

        milestone 300: +1.0% per collected mount     (ParagonMountSpells)
        milestone 325: +0.5% per collected companion (ParagonCompanionSpells)

    factor = 1 + mounts/100 + companions/200 (each term gated on its own
    milestone; benefits require character level 80 as usual).

    "Collected" = spells in the spellbook — account-wide on this server:
    mod-collections unions mount/companion spells (its classification =
    SkillLineAbility skill 777/778, which the generated companion set
    mirrors exactly) into account_collection_spell and learnSpell()s them
    onto every character at login.

    Applies to CREATURE / CRAFT / GATHER / PROCESS experience via
    OnExperienceCalculated. ACHIEVEMENT and SKILLUP experience are deliberately
    excluded (one-time rewards stay flat — user spec); QUEST experience
    likewise since the 2026-08-18 nerf
    pass (quests grant their full base XP flat, no multipliers). The
    banked pre-80 achievement payout bypasses the event (stays flat). Creature
    kill shares use the same OnExperienceCalculated path, so every personal XP
    bonus is applied after the native group share is calculated.

    Supersedes paragon_mount_xp.lua (milestone 300's original home).
]]

local PREFIX = "ParagonCollection"
local COUNT_KEY = "ParagonCollectionCounts"
local Config = require("paragon_config")

local function MinLevel()
    return tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
end

-- paragon_hook.lua EXPERIENCE_SOURCE: achievement rewards stay flat
local SOURCE_ACHIEVEMENT = 2
local SOURCE_SKILLUP = 3
-- quests stay flat too (2026-08-18 nerf pass): quest paragon XP is the
-- quest's full base XP (paragon_config_experience_quest, populated by
-- Tools/populate_quest_paragon_xp.py from QuestXP.dbc, no level
-- penalties) and is exempt from EVERY paragon-XP multiplier — including
-- the achievement/quest/transmog ladder percentages that join below
local SOURCE_QUEST = 4

-- one entry per collection bonus; future collections are one line + a data
-- set + a track milestone
local BONUSES = {
    -- track reorder 2026-08-18: mounts 300 -> 100, companions 325 -> 200
    { key = "mounts",     milestone = 100, percent_each = 1.0, set = function() return ParagonMountSpells end },
    { key = "companions", milestone = 200, percent_each = 0.5, set = function() return ParagonCompanionSpells end },
}

local function Counts(player)
    local cached = player:GetData(COUNT_KEY)
    if cached ~= nil then
        return cached
    end
    local counts = {}
    for _, bonus in ipairs(BONUSES) do
        local n = 0
        for spell in pairs(bonus.set() or {}) do
            if player:HasSpell(spell) then
                n = n + 1
            end
        end
        counts[bonus.key] = n
    end
    player:SetData(COUNT_KEY, counts)
    return counts
end

--- 1.0 when nothing is entitled, else 1 + the sum of unlocked collection
--- bonuses. The OnExperienceCalculated subscriber below applies it after each
--- recipient's native group share has been selected.
function ParagonCollectionXP_Factor(player, paragon)
    if not player or not paragon then
        return 1.0
    end
    -- account-wide paragon: benefits require character level 80
    if player:GetLevel() < MinLevel() then
        return 1.0
    end
    local level = paragon:GetLevel()
    local factor = 1.0
    local counts = nil
    for _, bonus in ipairs(BONUSES) do
        if level >= bonus.milestone then
            counts = counts or Counts(player)
            factor = factor + counts[bonus.key] * bonus.percent_each / 100
        end
    end
    -- milestone 475: achievement-ladder XP tiers. The ladder lives in
    -- paragon_achievement_bonus.lua (table, gating, tooltip); only its
    -- factor joins HERE, keeping this the single XP modifier. Party
    -- shares inherit it through this same function.
    if ParagonAchievementXP_Percent then
        factor = factor + ParagonAchievementXP_Percent(player, paragon) / 100
    end
    -- milestone 600: Loremaster's Ledger XP tier (paragon_quest_bonus.lua)
    if ParagonQuestXP_Percent then
        factor = factor + ParagonQuestXP_Percent(player, paragon) / 100
    end
    -- milestone 975: Collector's Wardrobe XP tier (paragon_transmog_bonus.lua)
    if ParagonTransmogXP_Percent then
        factor = factor + ParagonTransmogXP_Percent(player, paragon) / 100
    end
    -- codex "Starter Pack" (paragon_codex.lua node 54): flat additive share
    if ParagonCodex_ExperiencePercent then
        factor = factor + ParagonCodex_ExperiencePercent(player) / 100
    end
    return factor
end

-- ============================================================================
-- EXPERIENCE MODIFIER (polite: returns only when modifying — see header)
-- ============================================================================

RegisterMediatorEvent("OnExperienceCalculated", function(player, paragon, source_type, xp)
    local ok, ret = pcall(function()
        if source_type == SOURCE_ACHIEVEMENT or source_type == SOURCE_SKILLUP
                or source_type == SOURCE_QUEST or not xp then
            return
        end
        local factor = ParagonCollectionXP_Factor(player, paragon)
        if factor > 1.0 then
            return { math.floor(xp * factor) }
        end
    end)
    if ok then
        return ret
    end
    print("[Paragon] collection xp modifier error: " .. tostring(ret))
end)

-- ============================================================================
-- CLIENT STATE (track tooltip live lines)
-- ============================================================================

local function PushState(player)
    local paragon = player:GetData("Paragon")
    if not paragon then
        return -- paragon still loading (or bot without addon): nothing to say
    end
    local level = paragon:GetLevel()
    local sub80 = player:GetLevel() < MinLevel()
    local counts = nil
    local state = {}
    for _, bonus in ipairs(BONUSES) do
        if not sub80 and level >= bonus.milestone then
            counts = counts or Counts(player)
            state[bonus.key] = counts[bonus.key]
        else
            state[bonus.key] = 0
        end
    end
    player:SendServerResponse(PREFIX, 1, state)
end

-- learning any collected spell invalidates the cache and updates the
-- tooltip live (also fires for mod-collections' login sync — the paragon
-- guard in PushState keeps that quiet until the client is loaded)
RegisterPlayerEvent(44, function(event, player, spellId)
    local ok, err = pcall(function()
        for _, bonus in ipairs(BONUSES) do
            local set = bonus.set()
            if set and set[spellId] then
                player:SetData(COUNT_KEY, nil)
                PushState(player)
                return
            end
        end
    end)
    if not ok then
        print("[Paragon] collection xp learn error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            PushState(player)
        end
    end)
    if not ok then
        print("[Paragon] collection xp state-push error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            PushState(player)
        end
    end)
    if not ok then
        print("[Paragon] collection xp level-change error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: collection xp module loaded")
